"""Интеграционные тесты: приложение целиком, агент и живой сервер.

В отличие от модульных тестов здесь поднимаются настоящие HTTP- и
WebSocket-соединения: так проверяются защита Host/Origin, сериализация
ответов и совместная работа роутеров в одном процессе.
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import socket
import threading
import time
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
#  Наборы конфигов через полное приложение
# --------------------------------------------------------------------------- #

def test_bundle_lifecycle_via_full_app(client):
    created = client.post("/api/bundles", json={"name": "Интеграционный набор"}).json()
    assert created["ok"] is True, created
    bundle_id = created["id"]
    try:
        written = client.post(f"/api/bundles/{bundle_id}/file", json={
            "path": "conf/app.conf",
            "content": "key = value\n",
        }).json()
        assert written["ok"] is True, written

        read = client.get(f"/api/bundles/{bundle_id}/file", params={"path": "conf/app.conf"}).json()
        assert read["ok"] is True
        assert "key = value" in read["content"]

        exported = client.get(f"/api/bundles/{bundle_id}/export")
        assert exported.status_code == 200
        assert exported.content[:2] == b"PK"
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            names = archive.namelist()
        assert any(name.endswith("conf/app.conf") for name in names), names

        listed = client.get("/api/bundles").json()
        assert any(item["id"] == bundle_id for item in listed["items"])
    finally:
        removed = client.delete(f"/api/bundles/{bundle_id}").json()
        assert removed["ok"] is True


# --------------------------------------------------------------------------- #
#  Агент мониторинга: настоящий процесс-скрипт + API
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def agent_server():
    path = ROOT / "scripts" / "megatool-agent.py"
    spec = importlib.util.spec_from_file_location("megatool_agent_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.TOKEN = "agent-test-token"
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), module.AgentHandler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield module, f"http://127.0.0.1:{httpd.server_address[1]}/metrics"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)


def test_agent_script_metrics(agent_server):
    from megatool.agents import fetch, pick_metrics

    _module, url = agent_server
    with pytest.raises(Exception):
        fetch(url)  # токен обязателен
    payload = fetch(url, "agent-test-token")
    metrics = pick_metrics(payload)
    assert metrics["host"]
    assert 0 <= metrics["cpu_pct"] <= 100
    assert 0 <= metrics["mem_pct"] <= 100
    assert 0 <= metrics["disk_pct"] <= 100
    assert metrics["uptime"] >= 0


def test_agent_api_and_token_redaction(client, agent_server):
    _module, url = agent_server
    agent_id = ""
    try:
        saved = client.patch("/api/settings", json={"agents": [{
            "name": "Тестовый агент",
            "url": url.replace("/metrics", ""),
            "token": "agent-test-token",
        }]}).json()
        assert saved["ok"] is True, saved
        agents = saved["data"]["agents"]
        assert len(agents) == 1
        assert agents[0]["token"] == ""
        assert agents[0]["token_set"] is True
        agent_id = agents[0]["id"]

        state = client.get("/api/agents").json()
        assert state["ok"] is True
        assert "agent-test-token" not in json.dumps(state)
        item = state["data"]["agents"][0]
        assert item["online"] is True, item
        assert item["metrics"]["host"]

        tested = client.post("/api/agents/test", json={"id": agent_id}).json()
        assert tested["ok"] is True, tested
        # Проверка с изменённым адресом и пустым токеном берёт сохранённый секрет.
        retested = client.post("/api/agents/test", json={
            "id": agent_id,
            "url": url.replace("/metrics", "/"),
            "token": "",
        }).json()
        assert retested["ok"] is True, retested

        # Пустой токен при повторном сохранении не должен затирать секрет.
        again = client.patch("/api/settings", json={"agents": [{
            "id": agent_id,
            "name": "Тестовый агент",
            "url": url.replace("/metrics", ""),
            "token": "",
        }]}).json()
        assert again["ok"] is True, again
        stored = client.app.state.rt.cfg.get("agents")[0]
        assert stored["token"] == "agent-test-token"

        bad = client.patch("/api/settings", json={"agents": [{
            "name": "Плохой",
            "url": "ftp://example.com",
        }]}).json()
        assert bad["ok"] is False

        with_creds = client.patch("/api/settings", json={"agents": [{
            "name": "С кредами",
            "url": "http://user:pass@127.0.0.1:8791",
        }]}).json()
        assert with_creds["ok"] is False
    finally:
        client.patch("/api/settings", json={"agents": []})


# --------------------------------------------------------------------------- #
#  Лимит одновременных задач
# --------------------------------------------------------------------------- #

def test_job_limit_settings_are_clamped(client):
    before = client.get("/api/settings").json()["data"]["jobs"]
    try:
        clamped = client.patch("/api/settings", json={
            "jobs": {"max_active": 999, "keep": 1},
        }).json()
        assert clamped["ok"] is True
        assert clamped["data"]["jobs"]["max_active"] == 64
        assert clamped["data"]["jobs"]["keep"] == 10

        ignored = client.patch("/api/settings", json={
            "jobs": {"max_active": "abc"},
        }).json()
        assert ignored["ok"] is True
        assert ignored["data"]["jobs"]["max_active"] == 64

        infinite = client.patch("/api/settings", json={
            "jobs": {"max_active": "inf"},
        }).json()
        assert infinite["ok"] is True
        assert infinite["data"]["jobs"]["max_active"] == 64
    finally:
        client.patch("/api/settings", json={"jobs": before})


def test_job_limit(tmp_path):
    import sys

    from megatool.jobs import JobLimitError, JobManager

    manager = JobManager(tmp_path / "runs", keep=5, max_active=1)
    first = manager.submit("первая", [sys.executable, "-c", "import time; time.sleep(0.5)"])
    try:
        with pytest.raises(JobLimitError):
            manager.submit("вторая", [sys.executable, "-c", "pass"])
    finally:
        manager.cancel(first.id)
        manager.cancel_all()

    # После завершения слот освобождается. Запас времени увеличен: под
    # coverage запуск процесса и чтение потоков заметно медленнее.
    deadline = time.time() + 15
    while manager.active_count() and time.time() < deadline:
        time.sleep(0.05)
    assert manager.active_count() == 0
    second = manager.submit("третья", [sys.executable, "-c", "pass"])
    assert second.id


def test_pending_job_cancel_is_immediate(tmp_path):
    import sys

    from megatool.jobs import JobManager

    manager = JobManager(tmp_path / "runs", keep=5, max_active=1)
    job = manager.submit("долгая", [sys.executable, "-c", "import time; time.sleep(30)"])
    result = manager.cancel(job.id)
    assert result == {"ok": True}
    assert job.status == "cancelled"
    deadline = time.time() + 10
    while manager.active_count() and time.time() < deadline:
        time.sleep(0.05)
    assert manager.active_count() == 0


# --------------------------------------------------------------------------- #
#  Живой uvicorn + WebSocket
# --------------------------------------------------------------------------- #

def test_live_server_http_and_websocket(tmp_path: Path):
    import uvicorn

    from megatool.auth import AuthManager
    from megatool.server import create_app

    manager = AuthManager(tmp_path / "auth.json")
    manager.set_password("live-server-password")

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    config = uvicorn.Config(create_app(auth=manager), host="127.0.0.1", port=port,
                            log_level="error", lifespan="on", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 15
        while not server.started and time.time() < deadline:
            time.sleep(0.05)
        assert server.started, "uvicorn did not start"

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as response:
            health = json.loads(response.read().decode("utf-8"))
        assert health["ok"] is True

        login_data = json.dumps({"username": "admin", "password": "live-server-password"}).encode("utf-8")
        login = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/auth/login",
            data=login_data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(login, timeout=5) as response:
            cookie = response.headers["Set-Cookie"].split(";", 1)[0]
        assert cookie.startswith("megatool_session=")

        import websockets

        async def hello() -> dict:
            async with websockets.connect(
                f"ws://127.0.0.1:{port}/api/ws/metrics",
                open_timeout=5,
                extra_headers={"Cookie": cookie},
            ) as socket_conn:
                message = await asyncio.wait_for(socket_conn.recv(), timeout=8)
                return json.loads(message)

        payload = asyncio.run(hello())
        assert payload["type"] == "hello"
        assert payload["version"]
        assert isinstance(payload["state"], dict)
    finally:
        server.should_exit = True
        thread.join(timeout=10)

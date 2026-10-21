"""Мониторинг серверов: инвентарь, uptime/простой и защита от ошибок."""

from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from megatool.hosts import HostsManager


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - сигнатура http.server
        body = b"mega-host-ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


@pytest.fixture()
def http_target():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/health"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _raw(item) -> dict:
    data = {
        "id": item["id"],
        "name": item["name"],
        "kind": item["kind"],
        "note": item["note"],
        "probe": item["probe"],
        "interval_sec": item["interval_sec"],
        "timeout_sec": item["timeout_sec"],
        "enabled": item["enabled"],
    }
    for key in ("host", "port", "url", "expected_status", "keyword", "verify_tls"):
        if key in item:
            data[key] = item[key]
    return data


def test_hosts_validation(client):
    try:
        assert client.put("/api/hosts", json={"hosts": "nope"}).json()["ok"] is False
        assert client.put("/api/hosts", json={"hosts": [{
            "name": "bad probe", "probe": "icmp", "host": "example.com",
        }]}).json()["ok"] is False
        assert client.put("/api/hosts", json={"hosts": [{
            "name": "bad url", "probe": "http", "url": "file:///etc/passwd",
        }]}).json()["ok"] is False
        assert client.put("/api/hosts", json={"hosts": [{
            "name": "bad host", "probe": "tcp", "host": "bad host",
        }]}).json()["ok"] is False
        too_many = client.put("/api/hosts", json={"hosts": [
            {"name": str(index), "probe": "tcp", "host": "127.0.0.1", "port": 80}
            for index in range(51)
        ]}).json()
        assert too_many["ok"] is False
    finally:
        client.put("/api/hosts", json={"hosts": []})


def test_hosts_state_survives_restart(tmp_path: Path):
    host_id = "host-persist"
    now = time.time()
    path = tmp_path / "hosts_state.json"
    path.write_text(json.dumps({
        "version": 1,
        "items": {
            host_id: {
                "state": "up", "since": now - 120,
                "total_up_sec": 3600, "total_down_sec": 60,
                "total_disabled_sec": 30, "last_check": now,
            },
        },
    }), encoding="utf-8")

    class _Cfg:
        def get(self, key, default=None):
            if key == "hosts":
                return [{
                    "id": host_id, "name": "Persist", "probe": "tcp",
                    "host": "127.0.0.1", "port": 9, "interval_sec": 3600,
                }]
            return default

    first = HostsManager(_Cfg(), path)
    item = first.snapshot()["items"][0]
    assert item["state"] == "up"
    assert item["total_uptime_sec"] >= 3600
    assert item["total_downtime_sec"] >= 60
    assert item["total_disabled_sec"] >= 30
    first.stop()

    second = HostsManager(_Cfg(), path)
    restored = second.snapshot()["items"][0]
    assert restored["state"] == "up"
    assert restored["total_uptime_sec"] >= 3600
    assert restored["total_downtime_sec"] >= 60
    assert restored["total_disabled_sec"] >= 30
    second.stop()


def test_hosts_monitoring_uptime_and_disabled_time(client, http_target):
    http_port = urlsplit(http_target).port
    closed_port = _free_port()
    try:
        saved = client.put("/api/hosts", json={"hosts": [
            {
                "name": "Local TCP", "kind": "web", "note": "test",
                "probe": "tcp", "host": "127.0.0.1", "port": http_port,
                "interval_sec": 3600, "timeout_sec": 3,
            },
            {
                "name": "Closed TCP", "probe": "tcp", "host": "127.0.0.1",
                "port": closed_port, "interval_sec": 3600,
            },
            {
                "name": "Local HTTP", "probe": "http", "url": http_target,
                "expected_status": 200, "keyword": "mega-host-ok", "interval_sec": 3600,
            },
        ]}).json()
        assert saved["ok"] is True, saved
        items = saved["data"]["items"]
        assert len(items) == 3
        assert all(item["state"] == "unknown" for item in items)
        by_name = {item["name"]: item for item in items}

        ran = client.post("/api/hosts/run", json={}).json()
        assert ran["ok"] is True, ran
        items = ran["data"]["items"]
        by_name = {item["name"]: item for item in items}
        assert by_name["Local TCP"]["state"] == "up", by_name["Local TCP"]
        assert by_name["Closed TCP"]["state"] == "down", by_name["Closed TCP"]
        assert by_name["Local HTTP"]["state"] == "up", by_name["Local HTTP"]
        assert by_name["Local TCP"]["kind"] == "web"
        assert by_name["Local TCP"]["note"] == "test"
        assert by_name["Local TCP"]["uptime_sec"] >= 0
        assert ran["data"]["summary"] == {"total": 3, "up": 2, "down": 1, "unknown": 0, "disabled": 0}

        state_hosts = client.get("/api/state").json()["data"]["hosts"]
        assert state_hosts["summary"]["down"] == 1
        assert any(item["state"] == "up" for item in state_hosts["items"])

        alerts = client.app.state.rt.hosts.alerts()
        assert any("Closed TCP" in alert["title"] for alert in alerts)

        # Выключение сервера переводит его в disabled и накапливает uptime.
        time.sleep(0.2)
        target_id = by_name["Local TCP"]["id"]
        raw_items = [_raw(item) for item in items]
        for item in raw_items:
            if item["id"] == target_id:
                item["enabled"] = False
        disabled = client.put("/api/hosts", json={"hosts": raw_items}).json()
        assert disabled["ok"] is True, disabled
        disabled_item = next(item for item in disabled["data"]["items"] if item["id"] == target_id)
        assert disabled_item["state"] == "disabled"
        assert disabled_item["total_uptime_sec"] > 0
        time.sleep(0.05)
        later = client.get("/api/hosts").json()["data"]
        disabled_item = next(item for item in later["items"] if item["id"] == target_id)
        assert disabled_item["disabled_sec"] > 0
        assert client.post("/api/hosts/run", json={"id": target_id}).json()["ok"] is True

        # Включение снова начинает проверки; суммарное выключенное время сохранено.
        time.sleep(0.05)
        for item in raw_items:
            if item["id"] == target_id:
                item["enabled"] = True
        enabled = client.put("/api/hosts", json={"hosts": raw_items}).json()
        assert enabled["ok"] is True, enabled
        after_enable = next(item for item in enabled["data"]["items"] if item["id"] == target_id)
        assert after_enable["state"] in ("unknown", "up")
        assert after_enable["total_disabled_sec"] > 0
        rerun = client.post("/api/hosts/run", json={"id": target_id}).json()
        assert rerun["ok"] is True
        assert next(item for item in rerun["data"]["items"] if item["id"] == target_id)["state"] == "up"
    finally:
        client.put("/api/hosts", json={"hosts": []})
        rest = client.get("/api/hosts").json()["data"]
        assert rest["items"] == []
        assert rest["summary"]["total"] == 0

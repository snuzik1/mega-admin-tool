"""Дымовые и модульные тесты MegaTool.

Проверяем то, что легко сломать: API отвечает, песочница не выпускает наружу,
шаблоны рецептов рендерятся без «дыр», балансировщик распределяет трафик
по весам, а валидатор конфигов ловит ошибки.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


# --------------------------------------------------------------------------- #
#  API
# --------------------------------------------------------------------------- #

def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["app"] == "MegaTool"


def test_state_and_info(client):
    state = client.get("/api/state").json()
    assert state["ok"] is True
    assert "monitor" in state["data"]
    assert "engines" in state["data"]

    info = client.get("/api/info").json()
    assert info["ok"] is True
    assert info["data"]["host"]["hostname"]


def test_metrics_endpoints(client):
    snapshot = client.get("/api/metrics/snapshot").json()
    assert snapshot["ok"] is True
    assert "point" in snapshot["data"]
    history = client.get("/api/metrics/history?minutes=5&series=cpu,mem_pct").json()
    assert history["ok"] is True
    assert "cpu" in history["data"]["series"]


def test_processes_and_connections(client):
    processes = client.get("/api/processes?limit=5").json()
    assert processes["ok"] is True
    if processes["items"]:  # на живых системах процессы есть всегда
        assert "pid" in processes["items"][0]
    assert client.get("/api/connections?limit=5").json()["ok"] is True


def test_changes_and_audit(client):
    changes = client.get("/api/changes?hours=1").json()
    assert changes["ok"] is True
    sections = {section["id"] for section in changes["data"]["sections"]}
    assert {"files", "packages", "services", "logs", "audit"} <= sections
    assert client.get("/api/audit").json()["ok"] is True


def test_catalog_recipes(client):
    recipes = client.get("/api/catalog/recipes").json()
    assert recipes["ok"] is True
    assert len(recipes["items"]) >= 15
    ids = {item["id"] for item in recipes["items"]}
    assert {"postgres", "nginx", "k8s-web-app", "cfg-nginx-site"} <= ids


def test_jobs_empty_start(client):
    jobs = client.get("/api/jobs").json()
    assert jobs["ok"] is True
    assert isinstance(jobs["items"], list)


def test_settings_roundtrip(client):
    settings = client.get("/api/settings").json()
    assert settings["ok"] is True
    assert "auth" not in settings["data"]  # система доступа удалена из проекта

    patched = client.patch("/api/settings", json={"ui": {"theme": "light"}}).json()
    assert patched["ok"] is True
    assert patched["data"]["ui"]["theme"] == "light"
    client.patch("/api/settings", json={"ui": {"theme": "dark"}})


# --------------------------------------------------------------------------- #
#  Файлы и валидация
# --------------------------------------------------------------------------- #

def test_files_write_read_delete(client, home):
    payload = {"path": "configs/пример.txt", "content": "привет\nвторая строка"}
    written = client.post("/api/files/write", json=payload).json()
    assert written["ok"] is True
    assert (home / "configs" / "пример.txt").is_file()

    read = client.get("/api/files/read", params={"path": "configs/пример.txt"}).json()
    assert read["ok"] is True
    assert "вторая строка" in read["text"]

    tree = client.get("/api/files/tree").json()
    assert tree["ok"] is True

    deleted = client.post("/api/files/delete", json={"path": "configs/пример.txt"}).json()
    assert deleted["ok"] is True


def test_workspace_sandbox(home):
    from megatool.appfiles import Workspace
    from megatool.config import Config
    from megatool.util import is_within

    workspace = Workspace(Config())
    with pytest.raises(PermissionError):
        workspace.resolve("C:/Windows/System32/config.json" if os.name == "nt" else "/etc/passwd")
    inside = workspace.resolve("configs/test.txt")
    # home может быть симлинком (macOS) или коротким 8.3-путём (Windows),
    # поэтому сравниваем уже разрешённые пути.
    assert is_within(inside, home)


def test_validate_helpers():
    from megatool.appfiles import validate

    assert validate("a.yml", "key: value")["ok"] is True
    bad_yaml = validate("a.yml", "key: [1, 2")
    assert bad_yaml["ok"] is False

    compose_ok = validate("docker-compose.yml", "services:\n  web:\n    image: nginx\n")
    assert compose_ok["ok"] is True
    compose_bad = validate("docker-compose.yml", "services: {}\n")
    assert compose_bad["ok"] is False

    env_bad = validate(".env", "ЭТО_НЕ_ПЕРЕМЕННАЯ\n")
    assert env_bad["ok"] is False

    nginx_bad = validate("site.conf", "server {\n  listen 80;\n")
    assert nginx_bad["ok"] is False

    assert validate("myapp.service", "[Unit]\nDescription=x\n\n[Service]\nExecStart=/bin/true\n")["ok"] is True


def test_templates_available(client):
    templates = client.get("/api/templates").json()
    assert templates["ok"] is True
    assert len(templates["items"]) >= 8
    full = client.get("/api/templates/compose-basic").json()
    assert full["ok"] is True
    assert "services:" in full["data"]["content"]


# --------------------------------------------------------------------------- #
#  Рецепты и развёртывание
# --------------------------------------------------------------------------- #

def test_all_recipes_render_without_missing_placeholders():
    from megatool.recipes import RECIPES, render

    for recipe in RECIPES:
        values = {item["key"]: (item.get("default") or "").replace("__secret__", "секрет-12345")
                  for item in recipe.get("vars", [])}
        values.update({item["key"]: "секрет-12345" for item in recipe.get("env", [])})
        for path, content in recipe["files"].items():
            _, missing_path = render(path, values)
            _, missing_text = render(content, values)
            assert not missing_path, f"{recipe['id']}: путь {path} — не заполнено {missing_path}"
            assert not missing_text, f"{recipe['id']}: {path} — не заполнено {missing_text}"


def test_deploy_config_pack_and_remove(client, home):
    recipes = client.get("/api/catalog/recipes", params={"q": "nginx"}).json()["items"]
    target = next(item for item in recipes if item["id"] == "cfg-nginx-site")

    deployed = client.post("/api/catalog/deploy", json={
        "recipe_id": target["id"],
        "name": "demo-site",
        "values": {"DOMAIN": "demo.example.com", "BACKEND": "127.0.0.1:9000"},
    }).json()
    assert deployed["ok"] is True
    directory = Path(deployed["dir"])
    assert (directory / "site.conf").is_file()
    assert "demo.example.com" in (directory / "site.conf").read_text(encoding="utf-8")

    listed = client.get("/api/catalog/deployed").json()
    assert any(item["slug"] == deployed["slug"] for item in listed["items"])

    removed = client.delete(f"/api/catalog/deployed/{deployed['slug']}").json()
    assert removed["ok"] is True
    assert not directory.exists()


def test_deploy_compose_generates_secrets(client):
    deployed = client.post("/api/catalog/deploy", json={
        "recipe_id": "postgres",
        "name": "pg-demo",
        "values": {"NAME": "pg-demo", "PORT": "55432"},
    }).json()
    assert deployed["ok"] is True
    directory = Path(deployed["dir"])
    env_text = (directory / ".env").read_text(encoding="utf-8")
    assert "POSTGRES_PASSWORD=" in env_text
    password = [line for line in env_text.splitlines() if line.startswith("POSTGRES_PASSWORD=")][0]
    assert len(password.split("=", 1)[1]) >= 12
    assert "pg-demo" in (directory / "docker-compose.yml").read_text(encoding="utf-8")
    client.delete(f"/api/catalog/deployed/{deployed['slug']}")


def test_deploy_rejects_bad_name(client):
    response = client.post("/api/catalog/deploy", json={
        "recipe_id": "nginx", "values": {"NAME": "плохое имя", "PORT": "8080"},
    }).json()
    assert response["ok"] is False


# --------------------------------------------------------------------------- #
#  Балансировщик
# --------------------------------------------------------------------------- #

def test_balancer_lifecycle_and_weights(client):
    created = client.post("/api/balancers", json={
        "name": "test-lb",
        "listen_host": "127.0.0.1",
        "listen_port": 18432,
        "algorithm": "round_robin",
        "targets": [
            {"host": "127.0.0.1", "port": 9001, "weight": 3},
            {"host": "127.0.0.1", "port": 9002, "weight": 1},
        ],
    }).json()
    assert created["ok"] is True, created

    updated = client.patch("/api/balancers/test-lb", json={"algorithm": "least_conn"}).json()
    assert updated["ok"] is True
    assert updated["balancer"]["algorithm"] == "least_conn"

    stats = client.get("/api/balancers/test-lb/stats").json()
    assert stats["ok"] is True
    assert len(stats["data"]["targets"]) == 2

    exported = client.get("/api/balancers/test-lb/export", params={"format": "haproxy"}).json()
    assert exported["ok"] is True
    assert "backend megatool_test_lb" in exported["content"]
    assert "balance leastconn" in exported["content"]

    nginx_export = client.get("/api/balancers/test-lb/export", params={"format": "nginx"}).json()
    assert "upstream megatool_test_lb" in nginx_export["content"]
    assert "server 127.0.0.1:9001 weight=3" in nginx_export["content"]

    assert client.delete("/api/balancers/test-lb").json()["ok"] is True


def test_balancer_proxy_and_health():
    """Поднимаем два настоящих HTTP-бэкенда и гоняем через балансировщик реальные запросы.

    Везде используем порт 0 («любой свободный»): на Windows занятый порт — самая частая
    причина ложных падений. Клиент асинхронный (asyncio), потому что балансировщик живёт
    в том же event loop — блокирующий fetch() остановил бы и его.
    """
    import asyncio
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from megatool.balancer import BalancerManager
    from megatool.config import Config

    def make_backend(name: str) -> ThreadingHTTPServer:
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):  # noqa: N802
                payload = b"ok" if self.path == "/healthz" else name.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):  # тишина в тестах
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    server_a = make_backend("backend-A")
    server_b = make_backend("backend-B")
    port_a = server_a.server_address[1]
    port_b = server_b.server_address[1]

    async def scenario() -> dict:
        cfg = Config()
        manager = BalancerManager(cfg)
        await manager.delete("proxy-test")  # на случай конфига, оставшегося от прошлого прогона
        created = manager.create({
            "name": "proxy-test",
            "listen_host": "127.0.0.1",
            "listen_port": 0,  # 0 — занять любой свободный порт
            "algorithm": "round_robin",
            "targets": [{"host": "127.0.0.1", "port": port_a}, {"host": "127.0.0.1", "port": port_b}],
            "health": {"enabled": True, "mode": "http", "path": "/healthz", "interval_s": 2},
        })
        assert created["ok"] is True, created
        started = await manager.start("proxy-test")
        assert started["ok"] is True, started
        lb_port = started["port"]
        assert lb_port and lb_port not in (port_a, port_b)

        balancer = manager.get("proxy-test")
        await asyncio.sleep(0.4)  # даём health-check отметить цели живыми

        async def fetch() -> str:
            """Минимальный HTTP-клиент: читаем тело по Content-Length, без блокировки loop."""
            reader, writer = await asyncio.open_connection("127.0.0.1", lb_port)
            try:
                writer.write(b"GET / HTTP/1.1\r\nHost: proxy-test\r\nConnection: close\r\n\r\n")
                await writer.drain()
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
                status = head.split(b"\r\n", 1)[0]
                assert b" 200 " in status, status
                length = 0
                for line in head.decode("latin-1").split("\r\n")[1:]:
                    if line.lower().startswith("content-length:"):
                        length = int(line.split(":", 1)[1].strip())
                body = await asyncio.wait_for(reader.readexactly(length), timeout=5) if length else b""
                return body.decode()
            finally:
                writer.close()

        results = [await fetch() for _ in range(6)]
        await manager.stop("proxy-test")
        await manager.delete("proxy-test")
        return {"results": results, "targets": [t.to_dict() for t in balancer.targets]}

    try:
        outcome = asyncio.run(scenario())
    finally:
        server_a.shutdown()
        server_b.shutdown()

    # Оба бэкенда должны получить запросы (round robin по кругу).
    assert set(outcome["results"]) == {"backend-A", "backend-B"}
    assert all(target["healthy"] for target in outcome["targets"])
    assert sum(target["requests"] for target in outcome["targets"]) == 6


def test_balancer_pick_round_robin_weights():
    from megatool.balancer import Balancer, BalancerManager
    from megatool.config import Config

    manager = BalancerManager(Config())
    balancer = Balancer(manager, {
        "name": "pick-test",
        "algorithm": "round_robin",
        "targets": [{"host": "127.0.0.1", "port": 10001, "weight": 2},
                    {"host": "127.0.0.1", "port": 10002, "weight": 1}],
    })
    for target in balancer.targets:
        target.healthy = True
    picks = [balancer.pick("10.0.0.1").address for _ in range(9)]
    heavy = picks.count("127.0.0.1:10001")
    light = picks.count("127.0.0.1:10002")
    assert heavy == 6 and light == 3  # пропорция весов 2:1

    for target in balancer.targets:
        target.healthy = False
    assert balancer.pick("10.0.0.1") is None


# --------------------------------------------------------------------------- #
#  Ansible и утилиты
# --------------------------------------------------------------------------- #

def test_ansible_inventory_parse(home):
    from megatool.ansible import Ansible
    from megatool.config import Config

    ansible = Ansible(Config())
    ansible.ensure_scaffold()
    tree = ansible.inventory_tree()
    assert tree["ok"] is True
    assert "localhost" in tree["hosts"]
    assert any(group["name"] == "local" for group in tree["groups"])
    playbooks = ansible.playbooks()
    assert any(item["name"] == "site.yml" for item in playbooks)


def test_ansible_rejects_bad_limit(home, monkeypatch):
    from megatool.ansible import Ansible
    from megatool.config import Config
    from megatool.jobs import JobManager

    ansible = Ansible(Config())
    ansible.ensure_scaffold()
    # ansible может быть не установлен — подменяем только поиск бинарника,
    # чтобы проверить именно валидацию --limit.
    monkeypatch.setattr(Ansible, "playbook_bin", property(lambda self: "ansible-playbook"))

    jobs = JobManager(home / "runs")
    result = ansible.run_playbook(jobs, playbook="site.yml", limit="$(rm -rf /)")
    assert result["ok"] is False
    assert "limit" in result["error"]


def test_util_helpers():
    from megatool.util import human_bytes, is_safe_limit, safe_id, split_kv

    assert safe_id("web-01") == "web-01"
    assert safe_id("web; rm -rf /") == ""
    assert safe_id("--flag") == ""
    assert is_safe_limit("web:&prod,!db1") is True
    assert is_safe_limit("web; rm") is False
    assert human_bytes(1024) == "1.0 KB"
    assert split_kv(["A=1", "B=x=y"]) == {"A": "1", "B": "x=y"}


def test_changes_files_section_home(client, home):
    """Файл, созданный только что в рабочем каталоге, должен попасть в отчёт об изменениях."""
    from megatool.changes import collect
    from megatool.config import Config

    cfg = Config()
    cfg.update({"monitor": {"watch_paths": [str(home)]}})
    (home / "свежий-файл.txt").write_text("test", encoding="utf-8")
    report = collect(cfg, hours=1)
    files_section = next(section for section in report["sections"] if section["id"] == "files")
    assert any("свежий-файл" in item["path"] for item in files_section["items"])


def test_jobs_run_and_log(client, home):
    """Задача реально запускается, лог пишется, статус успешный."""
    import sys
    import time as _time

    from megatool.config import Config
    from megatool.jobs import JobManager

    jobs = JobManager(home / "runs-test")
    job = jobs.submit("проверка", [sys.executable, "-c", "print('привет из задачи')"])
    for _ in range(60):
        if job.status != "running" and job.status != "pending":
            break
        _time.sleep(0.1)
    assert job.status == "success", job.status
    log = jobs.log(job.id)
    assert any("привет из задачи" in line["text"] for line in log["lines"])


def test_k8s_parsers():
    """Разбор количеств Kubernetes: CPU-доли и объёмы памяти."""
    from megatool.k8s import parse_cpu, parse_qty, parse_percent_text

    assert parse_cpu("250m") == 0.25
    assert parse_cpu("2") == 2.0
    assert parse_cpu(None) == 0.0
    assert parse_cpu("мусор") == 0.0
    assert parse_qty("8123456Ki") == 8123456 * 1024
    assert parse_qty("2Gi") == 2 * 1024 ** 3
    assert parse_qty("1024M") == 1024 * 1000 ** 2
    assert parse_qty("") == 0.0
    assert parse_percent_text("23%") == 23.0
    assert parse_percent_text("—") == 0.0


def test_k8s_set_target_and_guards(home):
    """Пустой context сбрасывает выбор; опасные аргументы не проходят."""
    from megatool.config import Config
    from megatool.k8s import K8s

    k8s = K8s(Config())
    result = k8s.set_target(context="dev-cluster", namespace="default")
    assert result["ok"] is True
    assert k8s.context == "dev-cluster"
    assert k8s.namespace == "default"

    # пустая строка возвращает поведение «как настроен kubectl»
    result = k8s.set_target(context="", namespace="")
    assert result["ok"] is True
    assert k8s.context == ""
    assert k8s.namespace == ""

    assert k8s.set_target(context="bad context!")["ok"] is False
    assert k8s.set_target(namespace="Default")["ok"] is False

    # защита от подстановки команд в имя ресурса
    assert k8s.describe("pod; rm -rf /", "web")["ok"] is False
    assert k8s.scale("job", "web", 2)["ok"] is False
    assert k8s.delete("pod", "$(evil)")["ok"] is False
    assert k8s.logs("web; shutdown")["ok"] is False

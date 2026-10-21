"""Тесты раздела «Серверы по SSH» (provision.py, api_servers.py).

Проверки не требуют настоящего ssh: выполнение подменяется, а сценарии
запуска используют dry_run. Приложение собирается отдельным FastAPI без
middleware, чтобы тесты не зависели от защиты Host в server.py.
"""

from __future__ import annotations

import base64
import json

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from megatool import provision
from megatool.api_servers import router
from megatool.config import Config
from megatool.i18n import audit, set_language, t
from megatool.runtime import build_runtime
from megatool.util import CmdResult

ALL_TASKS = [
    "base-update", "base-tools", "system-tz", "system-swap", "user-create",
    "security-ssh", "security-fw", "docker-install", "web-nginx",
    "security-fail2ban",
]

ALL_PARAMS = {
    "extra": "mc nano",
    "tz": "Europe/Moscow",
    "size_gb": "4",
    "user": "deploy",
    "sshkey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKey deploy@example",
    "backend": "firewalld",
}

CANONICAL_REPORT = """MEGATOOL_REPORT=1
HOSTNAME=web1
USER=root
KERNEL=6.8.0-31-generic
ARCH=x86_64
OS_ID=ubuntu
OS_PRETTY=Ubuntu 24.04 LTS
OS_LIKE=debian
SUDO_NOPASS=yes
TOOL_DOCKER=Docker version 27.0.3, build abc
TOOL_COMPOSE=2.29.1
TOOL_PODMAN=
TOOL_NGINX=nginx version: nginx/1.24.0
TOOL_UFW=ufw 0.36.2
TOOL_FIREWALL_CMD=
TOOL_SYSTEMCTL=systemd 255
TOOL_FAIL2BAN=fail2ban-client v1.0.2
TOOL_CURL=curl 8.5.0
TOOL_GIT=git version 2.43.0
DISK_AVAIL=18G
MEM_MB=3924
"""


@pytest.fixture()
def app(tmp_path):
    """Отдельное приложение: роутер без глобальных middleware и host guard."""
    cfg = Config(tmp_path / "config.json")
    runtime = build_runtime(cfg)
    application = FastAPI()
    application.state.rt = runtime
    application.include_router(router)
    with TestClient(application, base_url="http://127.0.0.1:8799") as client:
        yield application, client, runtime
    set_language("ru")


@pytest.fixture()
def client(app):
    return app[1]


def add_server(client: TestClient, **overrides) -> dict:
    payload = {
        "name": "web1",
        "host": "10.0.0.10",
        "port": 2222,
        "user": "deploy",
        "key": "C:/keys/id_ed25519",
        "note": "тестовый сервер",
    }
    payload.update(overrides)
    response = client.post("/api/servers", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True, data
    return data["server"]


# --------------------------------------------------------------------------- #
#  Хранилище и CRUD
# --------------------------------------------------------------------------- #

def test_crud_and_config_persistence(app):
    application, client, runtime = app
    assert client.get("/api/servers").json()["items"] == []

    server = add_server(client)
    assert server["id"]
    assert server["port"] == 2222
    assert server["created_ts"] > 0
    assert "password" not in server

    listing = client.get("/api/servers").json()
    assert listing["ok"] is True
    assert [item["id"] for item in listing["items"]] == [server["id"]]
    assert listing["items"][0]["last_check"] is None
    assert "ssh" in listing and "remote" in listing

    stored = json.loads((runtime.cfg.path).read_text(encoding="utf-8"))
    assert stored["servers"][0]["name"] == "web1"

    updated = client.patch(f"/api/servers/{server['id']}", json={"note": "новое имя", "port": 22}).json()
    assert updated["ok"] is True
    assert updated["server"]["note"] == "новое имя"
    assert updated["server"]["port"] == 22
    assert updated["server"]["host"] == "10.0.0.10"

    deleted = client.delete(f"/api/servers/{server['id']}").json()
    assert deleted["ok"] is True
    assert client.get("/api/servers").json()["items"] == []
    assert client.delete(f"/api/servers/{server['id']}").json()["ok"] is False


def test_add_validation(client):
    bad_host = client.post("/api/servers", json={"name": "x", "host": "bad host!"}).json()
    assert bad_host["ok"] is False
    assert bad_host["error"]

    bad_port = client.post("/api/servers", json={"name": "x", "host": "10.0.0.1", "port": 70000}).json()
    assert bad_port["ok"] is False

    bad_user = client.post("/api/servers", json={"name": "x", "host": "10.0.0.1", "user": "root; rm"}).json()
    assert bad_user["ok"] is False

    bad_key = client.post("/api/servers", json={"name": "x", "host": "10.0.0.1", "key": "a\nb"}).json()
    assert bad_key["ok"] is False

    implicit_name = client.post("/api/servers", json={"host": "192.0.2.7"}).json()
    assert implicit_name["ok"] is True
    assert implicit_name["server"]["name"] == "192.0.2.7"
    assert implicit_name["server"]["user"] == "root"
    assert implicit_name["server"]["port"] == 22


# --------------------------------------------------------------------------- #
#  Каталог задач
# --------------------------------------------------------------------------- #

def test_catalog_has_ten_tasks_and_is_localized(app):
    application, client, runtime = app
    data = client.get("/api/servers/tasks").json()
    assert data["ok"] is True
    assert len(data["items"]) == 10
    assert [item["id"] for item in data["items"]] == ALL_TASKS
    assert {group["id"] for group in data["groups"]} == {
        "base", "system", "user", "security", "docker", "web",
    }
    for item in data["items"]:
        assert item["title"] and not item["title"].startswith("tasks.")
        assert item["description"] and not item["description"].startswith("tasks.")
        assert item["risk"] in ("safe", "medium", "high")
        assert item["sudo"] is True
        for param in item["params"]:
            assert param["label"] and not param["label"].startswith("tasks.")
            assert param["kind"] in ("text", "int", "tz", "user", "sshkey", "choice")

    set_language("en")
    english = client.get("/api/servers/tasks").json()
    titles = {item["id"]: item["title"] for item in english["items"]}
    assert titles["base-update"] == "Refresh package index"
    assert english["risks"]["high"] == "High risk"
    set_language("ru")
    russian = client.get("/api/servers/tasks").json()
    assert {item["id"]: item["title"] for item in russian["items"]}["base-update"] == "Обновить индекс пакетов"


# --------------------------------------------------------------------------- #
#  Скрипт и плейбук
# --------------------------------------------------------------------------- #

def test_dry_run_script_has_all_tasks_and_rendered_params(client):
    server = add_server(client)
    response = client.post(
        f"/api/servers/{server['id']}/run",
        json={"tasks": ALL_TASKS, "params": ALL_PARAMS, "dry_run": True},
    )
    data = response.json()
    assert response.status_code == 200
    assert data["ok"] is True
    assert data["preview"] is True
    script = data["script"]

    assert script.startswith("#!/bin/sh")
    assert "MEGATOOL_ERRORS" in script
    for task_id in ALL_TASKS:
        assert f"== MegaTool: {task_id} ==" in script
    assert "sudo -n sh -c" in script
    assert 'zone="Europe/Moscow"' in script
    assert "size_gb=4" in script
    assert 'user="deploy"' in script
    assert 'backend="firewalld"' in script
    assert "mc nano" in script
    assert "{" + "tz}" not in script
    assert "{" + "size_gb}" not in script


def test_playbook_is_valid_yaml(client):
    server = add_server(client)
    response = client.post(
        f"/api/servers/{server['id']}/playbook",
        json={"tasks": ["base-update", "system-tz", "web-nginx"], "params": {"tz": "UTC"}},
    )
    data = response.json()
    assert data["ok"] is True
    assert data["filename"] == f"{server['id']}.yml"

    document = yaml.safe_load(data["yaml"])
    assert isinstance(document, list) and len(document) == 1
    play = document[0]
    assert play["hosts"] == "all"
    assert play["become"] is True
    assert isinstance(play["tasks"], list) and len(play["tasks"]) == 3
    commands = [task["ansible.builtin.shell"]["cmd"] for task in play["tasks"]]
    assert all("== MegaTool:" in cmd for cmd in commands)
    assert "zone=" not in commands[0]  # base-update
    assert 'zone="UTC"' in commands[1]
    assert all(task["ansible.builtin.shell"]["executable"] == "/bin/sh" for task in play["tasks"])


def test_script_passwordless_sudo_known(client, app):
    application, client, runtime = app
    server = add_server(client)
    manager = runtime.servers
    manager.remember_report(server["id"], {
        "reachable": True, "sudo_nopass": False, "tools": {}, "installed": {},
    })
    data = client.post(
        f"/api/servers/{server['id']}/run",
        json={"tasks": ["system-tz"], "params": {"tz": "UTC"}, "dry_run": True},
    ).json()
    assert data["ok"] is True
    # Права заведомо недоступны: задача пропускается с сообщением, sudo не вызывается.
    assert "sudo -n sh -c" not in data["script"]
    assert "MEGATOOL_ERRORS" in data["script"]


# --------------------------------------------------------------------------- #
#  Проверка сервера
# --------------------------------------------------------------------------- #

def test_parse_report_and_installed_tasks():
    report = provision.parse_report(CANONICAL_REPORT)
    assert report["reachable"] is True
    assert report["hostname"] == "web1"
    assert report["user"] == "root"
    assert report["family"] == "debian"
    assert report["os_id"] == "ubuntu"
    assert report["sudo_nopass"] is True
    assert report["mem_mb"] == 3924
    assert report["disk_avail"] == "18G"
    assert report["tools"]["docker"].startswith("Docker version")
    assert report["tools"]["compose"] == "2.29.1"
    assert report["tools"]["podman"] is None
    assert report["tools"]["nginx"].startswith("nginx version")

    installed = provision.installed_tasks(report)
    assert installed == {
        "base-tools": True,
        "docker-install": True,
        "web-nginx": True,
        "security-fail2ban": True,
        "security-fw": True,
    }

    redhat = provision.parse_report(
        "MEGATOOL_REPORT=1\nOS_ID=rocky\nOS_LIKE=\"rhel centos\"\nSUDO_NOPASS=no\n"
    )
    assert redhat["family"] == "redhat"
    assert redhat["sudo_nopass"] is False
    assert provision.parse_report("")["reachable"] is False


def test_check_api_caches_inventory(client, app, monkeypatch):
    application, client, runtime = app
    server = add_server(client)
    calls = []

    def fake_run(cfg, srv, script, timeout=60):
        calls.append((srv, script, timeout))
        return CmdResult(True, 0, out=CANONICAL_REPORT)

    monkeypatch.setattr(provision, "run_remote", fake_run)
    data = client.post(f"/api/servers/{server['id']}/check").json()
    assert data["ok"] is True
    assert data["reachable"] is True
    assert data["hostname"] == "web1"
    assert data["installed"]["docker-install"] is True
    assert data["checked_at"]
    assert calls and calls[0][1] == provision.CHECK_SCRIPT

    listing = client.get("/api/servers").json()["items"]
    assert listing[0]["last_check"]["hostname"] == "web1"
    catalog = client.get("/api/servers/tasks", params={"sid": server["id"]}).json()
    flags = {item["id"]: item["installed"] for item in catalog["items"]}
    assert flags["web-nginx"] is True
    assert flags["security-fw"] is True
    assert flags["base-update"] is None

    monkeypatch.setattr(
        provision, "run_remote",
        lambda *args, **kwargs: CmdResult(False, 255, err="Permission denied (publickey)."),
    )
    failed = client.post(f"/api/servers/{server['id']}/check").json()
    assert failed["ok"] is False
    assert failed["reachable"] is False
    assert "Permission denied" in failed["error"]


def test_check_reports_missing_ssh(client, monkeypatch):
    server = add_server(client)
    monkeypatch.setattr(
        provision, "run_remote",
        lambda *args, **kwargs: CmdResult(False, 127, missing=True, error="no ssh"),
    )
    data = client.post(f"/api/servers/{server['id']}/check").json()
    assert data["ok"] is False
    assert data["reachable"] is False
    assert data["error"] == t("servers.noSsh")


# --------------------------------------------------------------------------- #
#  Ограничения и параметры
# --------------------------------------------------------------------------- #

def test_allow_remote_exec_blocks_actions(app, monkeypatch):
    application, client, runtime = app
    server = add_server(client)
    runtime.cfg.set("safety.allow_remote_exec", False)

    check = client.post(f"/api/servers/{server['id']}/check").json()
    assert check["ok"] is False
    assert check["error"] == t("servers.disabled")

    run = client.post(f"/api/servers/{server['id']}/run", json={"tasks": ["base-update"]}).json()
    assert run["ok"] is False
    assert run["error"] == t("servers.disabled")

    execute = client.post(
        f"/api/servers/{server['id']}/exec", json={"command": "uptime"},
    ).json()
    assert execute["ok"] is False
    assert execute["error"] == t("servers.disabled")

    # Генерация скрипта и плейбука остаётся доступной: это локальные операции.
    preview = client.post(
        f"/api/servers/{server['id']}/run",
        json={"tasks": ["base-update"], "dry_run": True},
    ).json()
    assert preview["ok"] is True
    playbook = client.post(
        f"/api/servers/{server['id']}/playbook", json={"tasks": ["base-update"]},
    ).json()
    assert playbook["ok"] is True


def test_remote_run_requires_ssh(client, monkeypatch):
    server = add_server(client)
    monkeypatch.setattr(provision, "ssh_available", lambda cfg: None)
    data = client.post(
        f"/api/servers/{server['id']}/run", json={"tasks": ["base-update"]},
    ).json()
    assert data["ok"] is False
    assert data["error"] == t("servers.noSsh")


@pytest.mark.parametrize(
    ("task", "params"),
    [
        ("user-create", {"user": "Deploy"}),
        ("user-create", {"user": "-bad"}),
        ("user-create", {"sshkey": "ssh-ed25519 AAAA\nInjected"}),
        ("user-create", {"sshkey": "ssh-ed25519 AAAA\n"}),
        ("system-tz", {"tz": "Europe/Moscow; reboot"}),
        ("system-tz", {"tz": "Europe/Moscow\nUTC"}),
        ("system-tz", {"tz": ""}),
        ("system-swap", {"size_gb": "0"}),
        ("system-swap", {"size_gb": "65"}),
        ("system-swap", {"size_gb": "2.5"}),
        ("security-fw", {"backend": "iptables"}),
        ("base-tools", {"extra": "curl; reboot"}),
    ],
)
def test_bad_params_are_rejected_with_200(client, task, params):
    server = add_server(client)
    response = client.post(
        f"/api/servers/{server['id']}/run",
        json={"tasks": [task], "params": params, "dry_run": True},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert task in data["error"]
    assert "{" not in data["error"]


def test_validate_params_direct():
    ok = provision.validate_params(["system-tz", "system-swap"], {"tz": "UTC", "size_gb": "8"})
    assert ok == {"ok": True, "values": {"tz": "UTC", "size_gb": "8"}}

    assert provision.validate_params([], {})["ok"] is False
    assert provision.validate_params(["nope"], {})["ok"] is False
    assert provision.validate_params(["system-tz"], {"tz": "UTC\n"})["ok"] is True  # strip
    assert provision.validate_params(["system-tz"], {"tz": "UTC\nGMT"})["ok"] is False
    assert provision.validate_params(["base-tools"], {"extra": ""})["ok"] is True
    assert provision.validate_params(
        ["user-create"], {"user": "ok_user-1", "sshkey": ""}
    )["ok"] is True


def test_empty_and_too_long_commands(client, monkeypatch):
    server = add_server(client)
    empty = client.post(f"/api/servers/{server['id']}/exec", json={"command": "   "}).json()
    assert empty["ok"] is False
    assert empty["error"] == t("servers.emptyCommand")

    too_long = client.post(
        f"/api/servers/{server['id']}/exec", json={"command": "x" * (provision.MAX_COMMAND + 1)},
    ).json()
    assert too_long["ok"] is False

    captured = {}

    def fake_submit(cfg, jobs, srv, command, title, meta=None):
        captured.update(command=command, title=title, meta=meta)
        return {"ok": True, "job": {"id": "job-1", "title": title}}

    monkeypatch.setattr("megatool.api_servers.submit_command_job", fake_submit)
    good = client.post(
        f"/api/servers/{server['id']}/exec", json={"command": "uptime && df -h"},
    ).json()
    assert good["ok"] is True
    assert good["job"]["id"] == "job-1"
    assert captured["command"] == "uptime && df -h"
    assert captured["meta"]["server"] == server["id"]


# --------------------------------------------------------------------------- #
#  Служебные помощники и локализация
# --------------------------------------------------------------------------- #

def test_ssh_arguments_and_script_transport(tmp_path):
    cfg = Config(tmp_path / "config.json")
    cfg.set("engines.ssh", "ssh")
    server = {
        "host": "example.com", "port": 2200, "user": "deploy", "key": "C:/keys/id",
    }
    args = provision.build_ssh_args(cfg, server)
    assert args[-1] == "deploy@example.com"
    assert "-p" in args and args[args.index("-p") + 1] == "2200"
    assert "-i" in args and args[args.index("-i") + 1] == "C:/keys/id"
    assert "BatchMode=yes" in args
    assert "StrictHostKeyChecking=accept-new" in args

    script = "#!/bin/sh\necho 'привет'\n"
    payload = provision.pipe_script(script)
    encoded = payload.split()[1]
    assert base64.b64decode(encoded).decode("utf-8") == script
    assert payload.endswith("| base64 -d | sh")


def test_i18n_audit_is_clean():
    assert audit() == {"ru": [], "en": []}

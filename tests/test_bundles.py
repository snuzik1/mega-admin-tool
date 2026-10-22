"""Тесты наборов конфигов: хранилище, API, шаблоны и экспорт в ZIP.

Приложение собирается отдельно от общего server.py: только роутер
api_bundles и runtime с конфигом во временном каталоге. Так тесты
не трогают реальный ~/.megatool и не зависят от остальных разделов.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest


@pytest.fixture(scope="module")
def bundle_home(tmp_path_factory):
    """Изолированный рабочий каталог: конфиг и наборы лежат рядом."""
    return tmp_path_factory.mktemp("bundles")


@pytest.fixture(scope="module")
def bundle_client(bundle_home):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from megatool import api_bundles
    from megatool.config import Config
    from megatool.runtime import build_runtime

    app = FastAPI()
    app.state.rt = build_runtime(Config(bundle_home / "config.json"))
    app.include_router(api_bundles.router)
    with TestClient(app) as client:
        yield client


def _create(client, name: str, preset: str = "") -> dict:
    payload = {"name": name, "description": f"{name} — тестовый набор"}
    if preset:
        payload["preset"] = preset
    response = client.post("/api/bundles", json=payload).json()
    assert response["ok"] is True, response
    return response


# --------------------------------------------------------------------------- #
#  Шаблоны и создание
# --------------------------------------------------------------------------- #

def test_presets_are_complete(bundle_client):
    response = bundle_client.get("/api/bundles/presets").json()
    assert response["ok"] is True
    items = response["items"]
    assert len(items) == 5
    assert {item["id"] for item in items} == {
        "dev-server", "web-nginx", "docker-host", "monitoring", "k8s-app",
    }
    dev = next(item for item in items if item["id"] == "dev-server")
    assert dev["title"] and dev["title_en"]
    assert dev["description"] and dev["description_en"]
    assert "docker-compose.yml" in dev["files"]
    assert "ansible/playbook.yml" in dev["files"]
    # Содержимое в список шаблонов не попадает — только пути.
    assert all(isinstance(path, str) for path in dev["files"])


def test_create_from_preset(bundle_client, bundle_home):
    created = _create(bundle_client, "Dev Server", preset="dev-server")
    bid = created["id"]
    assert bid == "dev-server"
    assert created["preset"] == "dev-server"
    assert created["file_count"] >= 7

    root = bundle_home / "bundles" / bid
    assert (root / "bundle.json").is_file()
    assert (root / "files" / "docker-compose.yml").is_file()
    assert (root / "files" / "ansible" / "playbook.yml").is_file()
    assert (root / "files" / ".env.example").is_file()

    manifest = json.loads((root / "bundle.json").read_text(encoding="utf-8"))
    assert manifest["id"] == bid
    assert manifest["preset"] == "dev-server"
    assert "docker-compose.yml" in manifest["files"]
    assert isinstance(manifest["created_ts"], (int, float))
    assert manifest["updated_ts"] >= manifest["created_ts"]

    detail = bundle_client.get(f"/api/bundles/{bid}").json()
    assert detail["ok"] is True
    paths = {item["path"] for item in detail["files"]}
    assert {
        "docker-compose.yml", ".env.example", "nginx/default.conf",
        "html/index.html", "ansible/playbook.yml", "ansible/inventory.ini",
        "README.md",
    } <= paths

    listed = bundle_client.get("/api/bundles").json()
    assert listed["ok"] is True
    assert any(item["id"] == bid and item["file_count"] >= 7 for item in listed["items"])

    # Изменения пишутся в общий журнал аудита.
    from megatool.changes import read_audit

    assert any(entry.get("action") == "bundle.create" for entry in read_audit(500))


def test_create_validation(bundle_client):
    assert bundle_client.post("/api/bundles", json={"preset": "dev-server"}).json()["ok"] is False
    bad_preset = bundle_client.post("/api/bundles", json={"name": "Bad Preset", "preset": "nope"}).json()
    assert bad_preset["ok"] is False
    assert bad_preset["error"]


def test_unique_ids(bundle_client):
    first = _create(bundle_client, "dup-name")
    second = _create(bundle_client, "dup-name")
    assert first["id"] == "dup-name"
    assert second["id"] == "dup-name-2"


# --------------------------------------------------------------------------- #
#  Файлы
# --------------------------------------------------------------------------- #

def test_file_add_read_write_delete(bundle_client):
    bundle = _create(bundle_client, "Files Demo")
    bid = bundle["id"]

    added = bundle_client.post(f"/api/bundles/{bid}/files",
                               json={"path": "nginx/site.conf"}).json()
    assert added["ok"] is True
    assert added["path"] == "nginx/site.conf"
    assert added["size"] == 0

    duplicate = bundle_client.post(f"/api/bundles/{bid}/files",
                                   json={"path": "nginx/site.conf"}).json()
    assert duplicate["ok"] is False

    written = bundle_client.post(f"/api/bundles/{bid}/file", json={
        "path": "nginx/site.conf",
        "content": "server {\n    listen 80;\n}\n",
    }).json()
    assert written["ok"] is True
    assert written["size"] > 0

    read = bundle_client.get(f"/api/bundles/{bid}/file",
                             params={"path": "nginx/site.conf"}).json()
    assert read["ok"] is True
    assert "listen 80" in read["content"]

    # Запись в новый вложенный путь создаёт каталоги.
    nested = bundle_client.post(f"/api/bundles/{bid}/file", json={
        "path": "app/config/app.env", "content": "DEBUG=1\n",
    }).json()
    assert nested["ok"] is True
    assert nested["path"] == "app/config/app.env"

    detail = bundle_client.get(f"/api/bundles/{bid}").json()
    assert detail["file_count"] == 2
    assert {item["path"] for item in detail["files"]} == {"nginx/site.conf", "app/config/app.env"}

    removed = bundle_client.delete(f"/api/bundles/{bid}/file",
                                   params={"path": "nginx/site.conf"}).json()
    assert removed["ok"] is True
    assert bundle_client.get(f"/api/bundles/{bid}/file",
                             params={"path": "nginx/site.conf"}).json()["ok"] is False
    # Пустой каталог nginx/ убран вместе с файлом.
    assert not (bundle_client.app.state.rt._bundles.dir / bid / "files" / "nginx").exists()


def test_path_traversal_is_rejected(bundle_client, bundle_home):
    bundle = _create(bundle_client, "Path Demo")
    bid = bundle["id"]

    bad_paths = [
        "../evil.txt",
        "..\\evil.txt",
        "a/../../b.txt",
        "/etc/passwd",
        "~/secret.txt",
        "dir/~/secret.txt",
        "/root/.ssh/id_rsa",
        "",
    ]
    for bad in bad_paths:
        response = bundle_client.post(f"/api/bundles/{bid}/file", json={
            "path": bad, "content": "x",
        }).json()
        assert response["ok"] is False, f"путь должен быть отклонён: {bad!r}"
        assert response["error"]

    read = bundle_client.get(f"/api/bundles/{bid}/file",
                             params={"path": "../bundle.json"}).json()
    assert read["ok"] is False

    add = bundle_client.post(f"/api/bundles/{bid}/files",
                             json={"path": "../created.txt"}).json()
    assert add["ok"] is False

    root = bundle_home / "bundles" / bid
    assert not (root / "evil.txt").exists()
    assert not (root / "created.txt").exists()
    assert not (bundle_home / "bundles" / "evil.txt").exists()
    # Манифест не перезаписан содержимым "x".
    assert json.loads((root / "bundle.json").read_text(encoding="utf-8"))["id"] == bid


def test_rename_bundle(bundle_client):
    bundle = _create(bundle_client, "Rename Demo")
    bid = bundle["id"]

    patched = bundle_client.patch(f"/api/bundles/{bid}", json={
        "name": "Renamed Demo", "description": "новое описание",
    }).json()
    assert patched["ok"] is True
    assert patched["name"] == "Renamed Demo"
    assert patched["description"] == "новое описание"

    assert bundle_client.patch(f"/api/bundles/{bid}", json={"name": "   "}).json()["ok"] is False


# --------------------------------------------------------------------------- #
#  Экспорт и удаление
# --------------------------------------------------------------------------- #

def test_export_zip(bundle_client):
    bundle = _create(bundle_client, "Monitoring Demo", preset="monitoring")
    bid = bundle["id"]

    response = bundle_client.get(f"/api/bundles/{bid}/export")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")
    assert f'filename="{bid}.zip"' in response.headers["content-disposition"]

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.testzip() is None
        names = set(archive.namelist())
        assert "bundle.json" in names
        assert "files/docker-compose.yml" in names
        assert "files/prometheus/prometheus.yml" in names
        assert "files/README.md" in names

        manifest = json.loads(archive.read("bundle.json").decode("utf-8"))
        assert manifest["id"] == bid
        assert "docker-compose.yml" in manifest["files"]
        assert "prometheus/prometheus.yml" in manifest["files"]

        compose = archive.read("files/docker-compose.yml").decode("utf-8")
        assert "prometheus" in compose and "grafana" in compose

    # Экспорт несуществующего набора — ошибка в общем формате.
    assert bundle_client.get("/api/bundles/no-such-bundle/export").json()["ok"] is False


def test_delete_bundle(bundle_client, bundle_home):
    bundle = _create(bundle_client, "Delete Me")
    bid = bundle["id"]
    root = bundle_home / "bundles" / bid
    assert root.is_dir()

    removed = bundle_client.delete(f"/api/bundles/{bid}").json()
    assert removed["ok"] is True
    assert not root.exists()
    assert bundle_client.get(f"/api/bundles/{bid}").json()["ok"] is False
    assert bundle_client.delete(f"/api/bundles/{bid}").json()["ok"] is False

    ids = {item["id"] for item in bundle_client.get("/api/bundles").json()["items"]}
    assert bid not in ids

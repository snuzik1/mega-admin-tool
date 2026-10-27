"""Удалённый доступ: Host allowlist, TLS в настройках и HSTS."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from megatool.config import Config
from megatool.server import create_app

ORIGIN = "http://127.0.0.1:8799"
PASSWORD = "test-password-123"
USER = "admin"


@pytest.fixture()
def isolated_client(tmp_path: Path, auth_manager):
    cfg = Config(tmp_path / "config.json")
    app = create_app(cfg, auth=auth_manager)
    with TestClient(app, base_url=ORIGIN) as client:
        login = client.post("/api/auth/login", json={"username": USER, "password": PASSWORD})
        assert login.status_code == 200, login.text
        yield client, cfg


def test_external_bind_without_tls_is_rejected(isolated_client) -> None:
    client, cfg = isolated_client
    result = client.patch("/api/settings", json={"server": {"host": "0.0.0.0"}}).json()
    assert result["ok"] is False
    assert "HTTPS" in result["error"]
    assert cfg.get("server.host") == "127.0.0.1"


def test_ipv6_allowed_hosts_are_normalized(isolated_client) -> None:
    client, cfg = isolated_client
    saved = client.patch("/api/settings", json={
        "server": {"allowed_hosts": ["[2001:db8::1]:8787"]},
    }).json()
    assert saved["ok"] is True, saved
    assert cfg.get("server.allowed_hosts") == ["2001:db8::1"]
    assert client.get("/api/health", headers={"host": "2001:db8::1"}).status_code == 200


def test_external_bind_can_be_explicitly_allowed(isolated_client) -> None:
    client, cfg = isolated_client
    result = client.patch("/api/settings", json={
        "server": {"host": "0.0.0.0", "allow_insecure_remote": True},
    }).json()
    assert result["ok"] is True
    assert cfg.get("server.host") == "0.0.0.0"
    assert cfg.get("server.allow_insecure_remote") is True


def test_tls_requires_existing_certificate(isolated_client) -> None:
    client, cfg = isolated_client
    result = client.patch("/api/settings", json={"server": {"tls": {"enabled": True}}}).json()
    assert result["ok"] is False
    assert cfg.get("server.tls.enabled") is False


@pytest.mark.skipif(not shutil.which("openssl"), reason="нужен openssl")
def test_generate_tls_and_enable_external_bind(isolated_client) -> None:
    client, cfg = isolated_client
    result = client.post("/api/settings/tls/generate", json={"force": True})
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["ok"] is True
    assert body["restart_required"] is True
    assert body["data"]["exists"] is True
    assert body["data"]["fingerprint"]
    assert body["data"]["enabled"] is True
    assert cfg.get("server.tls.enabled") is True

    settings = client.get("/api/settings").json()
    assert settings["tls"]["fingerprint"] == body["data"]["fingerprint"]

    enabled = client.patch("/api/settings", json={"server": {"host": "0.0.0.0"}}).json()
    assert enabled["ok"] is True
    assert cfg.get("server.host") == "0.0.0.0"


@pytest.mark.skipif(not shutil.which("openssl"), reason="нужен openssl")
def test_generate_tls_to_custom_paths(tmp_path: Path, auth_manager) -> None:
    cfg = Config(tmp_path / "config.json")
    cert = tmp_path / "custom.crt"
    key = tmp_path / "custom.key"
    cfg.set("server.tls.certfile", str(cert))
    cfg.set("server.tls.keyfile", str(key))
    app = create_app(cfg, auth=auth_manager)
    with TestClient(app, base_url=ORIGIN) as client:
        login = client.post("/api/auth/login", json={"username": USER, "password": PASSWORD})
        assert login.status_code == 200, login.text
        result = client.post("/api/settings/tls/generate", json={"force": True}).json()
        assert result["ok"] is True, result
        assert cert.is_file()
        assert key.is_file()
        assert result["data"]["certfile"] == str(cert)
        assert result["data"]["keyfile"] == str(key)


def test_host_guard_applies_to_external_bind(tmp_path: Path, auth_manager) -> None:
    cfg = Config(tmp_path / "config.json")
    cfg.set("server.host", "0.0.0.0")
    cfg.set("server.allowed_hosts", ["megatool.local"])
    app = create_app(cfg, auth=auth_manager)
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get("/api/health", headers={"host": "127.0.0.1:8799"}).status_code == 200
        assert client.get("/api/health", headers={"host": "megatool.local"}).status_code == 200
        assert client.get("/api/health", headers={"host": "evil.example"}).status_code == 421


def test_hsts_is_emitted_only_over_https(tmp_path: Path, auth_manager) -> None:
    cfg = Config(tmp_path / "config.json")
    app = create_app(cfg, auth=auth_manager)
    with TestClient(app, base_url="https://127.0.0.1:8799") as client:
        response = client.get("/api/health")
        assert response.headers["strict-transport-security"] == "max-age=31536000"
    with TestClient(app, base_url="http://127.0.0.1:8799") as client:
        response = client.get("/api/health")
        assert "strict-transport-security" not in response.headers


def test_https_login_sets_secure_cookie(tmp_path: Path, auth_manager) -> None:
    cfg = Config(tmp_path / "config.json")
    app = create_app(cfg, auth=auth_manager)
    with TestClient(app, base_url="https://127.0.0.1:8799") as client:
        response = client.post("/api/auth/login", json={"username": USER, "password": PASSWORD})
        assert response.status_code == 200, response.text
        assert "secure" in response.headers["set-cookie"].lower()

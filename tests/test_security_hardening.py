"""Дополнительная защита: заголовки, WebSocket Origin, настройки и права."""

from __future__ import annotations

import os

import pytest
from starlette.websockets import WebSocketDisconnect


def test_security_headers(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    policy = response.headers["content-security-policy"]
    assert "default-src 'self'" in policy
    assert "frame-ancestors 'none'" in policy
    assert "object-src 'none'" in policy


def test_websocket_foreign_origin_is_rejected(client):
    with pytest.raises(WebSocketDisconnect) as failure:
        with client.websocket_connect(
            "/api/ws/metrics", headers={"origin": "http://evil.example"}
        ):
            pass
    assert failure.value.code == 4403


def test_websocket_same_origin_is_allowed(client):
    with client.websocket_connect(
        "/api/ws/metrics",
        headers={"host": "127.0.0.1:8799", "origin": "http://127.0.0.1:8799"},
    ) as socket:
        hello = socket.receive_json()
        assert hello["type"] == "hello"


def test_removed_auth_section_is_not_accepted_through_settings_patch(client):
    runtime = client.app.state.rt
    response = client.patch("/api/settings", json={
        "auth": {"enabled": True, "token": "injected"},
    }).json()
    assert response["ok"] is False
    assert "auth" not in runtime.cfg.export()
    assert runtime.cfg.get("auth.token", "") == ""


def test_removed_auth_is_ignored_on_import(client):
    runtime = client.app.state.rt
    ignored = client.post("/api/settings/import", json={"config": {
        "auth": {"enabled": True, "token": "evil"},
    }}).json()
    assert ignored["ok"] is False
    assert "auth" not in runtime.cfg.export()


def test_malformed_settings_are_rejected_without_crashing(client):
    before_theme = client.app.state.rt.cfg.get("ui.theme", "light")
    wrong_type = client.patch("/api/settings", json={"server": "oops"}).json()
    assert wrong_type["ok"] is False

    bad_port = client.patch("/api/settings", json={"server": {"port": "abc"}}).json()
    assert bad_port["ok"] is False

    unknown_theme = client.patch("/api/settings", json={"ui": {"theme": "neon"}}).json()
    assert unknown_theme["ok"] is True
    assert client.app.state.rt.cfg.get("ui.theme", "light") == before_theme


def test_allowed_hosts_can_be_configured(client):
    before = client.app.state.rt.cfg.get("server.allowed_hosts", [])
    try:
        saved = client.patch("/api/settings", json={
            "server": {"allowed_hosts": ["megatool.local", "invalid/path", "megatool.local:8787"]},
        }).json()
        assert saved["ok"] is True, saved
        assert client.app.state.rt.cfg.get("server.allowed_hosts") == ["megatool.local"]
        allowed = client.get("/api/health", headers={"host": "megatool.local"})
        assert allowed.status_code == 200
        denied = client.get("/api/health", headers={"host": "other.local"})
        assert denied.status_code == 421
    finally:
        client.patch("/api/settings", json={"server": {"allowed_hosts": before}})


def test_import_validates_settings_and_ignores_unknown_sections(client):
    runtime = client.app.state.rt
    before_theme = runtime.cfg.get("ui.theme", "light")
    bad = client.post("/api/settings/import", json={"config": {
        "auth": {"enabled": True, "token": "evil"},
        "server": {"port": "not-a-port"},
    }}).json()
    assert bad["ok"] is False
    assert "auth" not in runtime.cfg.export()

    bad_theme = client.post("/api/settings/import", json={"config": {
        "ui": {"theme": "neon"},
    }}).json()
    assert bad_theme["ok"] is True
    # Неизвестная тема отбрасывается, конфиг остаётся рабочим.
    assert runtime.cfg.get("ui.theme", "light") == before_theme

    bad_checks = client.post("/api/settings/import", json={"config": {
        "checks": [{"type": "http", "url": "file:///etc/passwd"}],
    }}).json()
    assert bad_checks["ok"] is False


@pytest.mark.parametrize("theme", ["sepia", "slate", "contrast"])
def test_new_themes_are_accepted(client, theme):
    runtime = client.app.state.rt
    before = runtime.cfg.get("ui.theme", "light")
    try:
        saved = client.patch("/api/settings", json={"ui": {"theme": theme}}).json()
        assert saved["ok"] is True, saved
        assert runtime.cfg.get("ui.theme") == theme
    finally:
        client.patch("/api/settings", json={"ui": {"theme": before}})


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes are not available on Windows")
def test_config_file_is_private(client, home):
    from megatool.config import CONFIG_PATH

    client.patch("/api/settings", json={"ui": {"theme": "light"}})
    mode = CONFIG_PATH.stat().st_mode & 0o777
    assert mode == 0o600, oct(mode)

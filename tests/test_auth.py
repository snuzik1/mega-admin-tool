"""Аутентификация: файл секрета, терминальные помощники и веб-сессии."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from starlette.websockets import WebSocketDisconnect

from megatool.api_auth import MAX_LOGIN_BODY_BYTES
from megatool.auth import (
    AuthError,
    AuthManager,
    LoginLimiter,
    ensure_login,
    load_record,
    set_password_cli,
    validate_password,
)

PASSWORD = "correct-horse-battery"
ORIGIN = "http://127.0.0.1:8799"


@pytest.fixture()
def manager(tmp_path: Path) -> AuthManager:
    item = AuthManager(tmp_path / "auth.json")
    item.set_password(PASSWORD)
    return item


@pytest.fixture()
def app(manager):
    from megatool.server import create_app

    return create_app(auth=manager)


@pytest.fixture()
def anon_client(app):
    from fastapi.testclient import TestClient

    with TestClient(app, base_url=ORIGIN) as client:
        yield client


def test_auth_file_is_private_and_has_no_plaintext(manager):
    text = manager.path.read_text(encoding="utf-8")
    assert PASSWORD not in text
    assert '"salt"' in text and '"hash"' in text
    if os.name == "posix":
        assert stat.S_IMODE(manager.path.stat().st_mode) == 0o600


def test_password_verification(manager):
    assert manager.verify("admin", PASSWORD) is True
    assert manager.verify("admin", "wrong-password") is False
    assert manager.verify("other", PASSWORD) is False
    with pytest.raises(AuthError):
        validate_password("short")


def test_short_password_is_rejected(tmp_path: Path):
    item = AuthManager(tmp_path / "auth.json")
    with pytest.raises(AuthError):
        item.set_password("short")


def test_auth_file_with_open_permissions_is_rejected(tmp_path: Path):
    if os.name != "posix":
        pytest.skip("POSIX permissions only")
    item = AuthManager(tmp_path / "auth.json")
    item.set_password(PASSWORD)
    os.chmod(item.path, 0o644)
    broken = AuthManager(item.path)
    assert broken.configured is False
    assert broken.error


def test_auth_directory_with_open_permissions_is_rejected(tmp_path: Path):
    if os.name != "posix":
        pytest.skip("POSIX permissions only")
    private = tmp_path / "private"
    private.mkdir()
    os.chmod(private, 0o700)
    item = AuthManager(private / "auth.json")
    item.set_password(PASSWORD)
    os.chmod(private, 0o755)
    broken = AuthManager(item.path)
    assert broken.configured is False
    assert broken.error
    with pytest.raises(AuthError):
        item.set_password("another-password")


def test_auth_directory_is_created_private(tmp_path: Path):
    if os.name != "posix":
        pytest.skip("POSIX permissions only")
    nested = tmp_path / "nested" / "auth.json"
    item = AuthManager(nested)
    item.set_password(PASSWORD)
    assert stat.S_IMODE(nested.parent.stat().st_mode) == 0o700


def test_auth_file_owner_mismatch_is_rejected(manager, monkeypatch):
    if os.name != "posix":
        pytest.skip("POSIX permissions only")
    monkeypatch.setattr(os, "geteuid", lambda: os.stat(manager.path).st_uid + 1)
    with pytest.raises(AuthError):
        load_record(manager.path)


def test_api_requires_session(anon_client, home):
    health = anon_client.get("/api/health")
    assert health.status_code == 200
    assert health.headers["cache-control"] == "no-store"
    assert str(home) not in health.text
    status = anon_client.get("/api/auth/status").json()["data"]
    assert status["configured"] is True
    assert status["authenticated"] is False

    protected = anon_client.get("/api/state")
    assert protected.status_code == 401
    assert protected.headers["cache-control"] == "no-store"
    assert protected.json()["ok"] is False


def test_missing_configuration_does_not_leak_auth_path(tmp_path: Path):
    from fastapi.testclient import TestClient

    from megatool.server import create_app

    item = AuthManager(tmp_path / "auth.json")
    app = create_app(auth=item)
    with TestClient(app, base_url=ORIGIN) as client:
        state = client.get("/api/state")
        assert state.status_code == 503
        assert str(item.path) not in state.text
        login = client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
        assert login.status_code == 503
        assert str(item.path) not in login.text


def test_websocket_requires_session(anon_client):
    with pytest.raises(WebSocketDisconnect) as failure:
        with anon_client.websocket_connect(
            "/api/ws/metrics",
            headers={"host": "127.0.0.1:8799", "origin": ORIGIN},
        ):
            pass
    assert failure.value.code == 4401


def test_login_logout_flow(anon_client):
    bad = anon_client.post("/api/auth/login", json={"username": "admin", "password": "bad"})
    assert bad.status_code == 401

    good = anon_client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    assert good.status_code == 200
    set_cookie = good.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=strict" in set_cookie

    assert anon_client.get("/api/state").status_code == 200
    assert anon_client.get("/api/auth/status").json()["data"]["authenticated"] is True

    cookie = "; ".join(f"{key}={value}" for key, value in anon_client.cookies.items())
    with anon_client.websocket_connect(
        "/api/ws/metrics",
        headers={"host": "127.0.0.1:8799", "origin": ORIGIN, "cookie": cookie},
    ) as socket:
        assert socket.receive_json()["type"] == "hello"

    logout = anon_client.post("/api/auth/logout")
    assert logout.status_code == 200
    assert anon_client.get("/api/state").status_code == 401


def test_login_accepts_urlencoded_form(anon_client):
    response = anon_client.post(
        "/api/auth/login",
        content=f"username=admin&password={PASSWORD}",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 200


def test_login_rejects_oversized_body(anon_client):
    response = anon_client.post(
        "/api/auth/login",
        content=b"x" * (MAX_LOGIN_BODY_BYTES + 1),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413


def test_login_rejects_oversized_chunked_body(anon_client):
    def chunks():
        for _ in range(MAX_LOGIN_BODY_BYTES // 1024 + 1):
            yield b"x" * 1024

    response = anon_client.post(
        "/api/auth/login",
        content=chunks(),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413


def test_login_ratelimit(anon_client):
    for _ in range(5):
        response = anon_client.post("/api/auth/login", json={"username": "admin", "password": "bad"})
        assert response.status_code == 401
    blocked = anon_client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0


def test_limiter_expires():
    limiter = LoginLimiter(max_attempts=2, window=60)
    limiter.fail("x")
    limiter.fail("x")
    assert limiter.retry_after("x") > 0
    limiter.reset("x")
    assert limiter.retry_after("x") == 0


def test_record_is_json_readable(manager):
    record = load_record(manager.path)
    assert record is not None
    assert record["username"] == "admin"
    assert record["algorithm"] == "pbkdf2_sha256"
    assert int(record["iterations"]) >= 1000


def test_running_manager_applies_password_change_and_revokes_sessions(tmp_path: Path):
    item = AuthManager(tmp_path / "auth.json")
    item.set_password(PASSWORD)
    token = item.sessions.create()

    changed = AuthManager(item.path)
    changed.set_password("another-long-password")

    assert item.verify("admin", PASSWORD) is False
    assert item.verify("admin", "another-long-password") is True
    assert item.authenticate(token) is False


def test_running_manager_fails_closed_when_auth_file_is_deleted(tmp_path: Path):
    item = AuthManager(tmp_path / "auth.json")
    item.set_password(PASSWORD)
    token = item.sessions.create()

    item.path.unlink()

    assert item.configured is False
    assert item.verify("admin", PASSWORD) is False
    assert item.authenticate(token) is False


def test_missing_credentials_fail_closed_without_prompt(tmp_path, monkeypatch):
    item = AuthManager(tmp_path / "auth.json")
    monkeypatch.delenv("MEGATOOL_ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("MEGATOOL_NO_LOGIN_PROMPT", "1")
    assert ensure_login(item) is False
    assert not item.path.exists()


def test_configured_credentials_skip_terminal_prompt(tmp_path, monkeypatch):
    item = AuthManager(tmp_path / "auth.json")
    item.set_password(PASSWORD)
    monkeypatch.setenv("MEGATOOL_NO_LOGIN_PROMPT", "1")
    assert ensure_login(item) is True


def test_admin_password_env_creates_credentials_for_noninteractive_start(tmp_path, monkeypatch):
    item = AuthManager(tmp_path / "auth.json")
    monkeypatch.setenv("MEGATOOL_ADMIN_PASSWORD", PASSWORD)
    assert ensure_login(item, no_prompt=True) is True
    assert item.verify("admin", PASSWORD) is True


def test_set_password_refuses_noninteractive_change(tmp_path, monkeypatch):
    item = AuthManager(tmp_path / "auth.json")
    item.set_password(PASSWORD)
    monkeypatch.setattr("megatool.auth.tty_available", lambda: False)
    monkeypatch.setenv("MEGATOOL_NEW_PASSWORD", "another-long-password")
    assert set_password_cli(item) == 1
    assert item.verify("admin", PASSWORD) is True
    assert item.verify("admin", "another-long-password") is False

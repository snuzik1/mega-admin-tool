"""Доступ: пресеты прав, пользователи, сессии и серверная проверка прав."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from megatool import localauth
from megatool.access import AccessManager, master_only


class _Cfg:
    """Минимальная замена Config для автономных тестов доступа."""

    def __init__(self, enabled: bool = True, local_only: bool = False) -> None:
        self.data = {"auth.enabled": enabled, "auth.local_only": local_only}

    def get(self, key: str, default=None):
        return self.data.get(key, default)


def _preset(name: str = "Оператор мониторинга") -> dict:
    return {
        "name": name,
        "description": "Мониторинг и серверы",
        "permissions": {"dashboard": "view", "hosts": "manage", "checks": "view"},
    }


def _user(username: str = "operator", password: str = "correct-horse") -> dict:
    return {
        "username": username,
        "display_name": username.title(),
        "preset_id": "",
        "password": password,
        "enabled": True,
    }


def test_access_presets_users_sessions_and_secrets(tmp_path: Path):
    manager = AccessManager(_Cfg(), tmp_path / "access.json")

    state = manager.state()
    assert len(state["presets"]) == 3
    assert state["users"] == []

    presets, error = manager.save_presets([_preset()])
    assert error == ""
    preset = next(item for item in presets["presets"] if item["name"] == "Оператор мониторинга")
    assert preset["permissions"] == {"dashboard": "view", "hosts": "manage", "checks": "view"}

    payload = _user()
    payload["preset_id"] = preset["id"]
    users, error = manager.save_users([payload])
    assert error == ""
    user = users["users"][0]
    assert user["username"] == "operator"
    assert user["preset_name"] == "Оператор мониторинга"
    assert user["password_set"] is True
    assert "password" not in user
    assert "hash" not in json.dumps(users)

    raw = (tmp_path / "access.json").read_text(encoding="utf-8")
    assert "pbkdf2_sha256$" in raw
    assert "correct-horse" not in raw

    token, principal, error = manager.login("operator", "wrong-password", client="unit")
    assert token == "" and principal is None and error
    token, principal, error = manager.login("operator", "correct-horse", client="unit")
    assert error == "" and token and principal
    assert principal["kind"] == "user"
    assert manager.allowed(principal, "hosts", "manage") is True
    assert manager.allowed(principal, "dashboard", "view") is True
    assert manager.allowed(principal, "dashboard", "manage") is False
    assert manager.allowed(principal, "settings", "view") is False
    assert manager.authenticate(token)["username"] == "operator"

    # Изменение пресета действует на следующем запросе.
    presets, error = manager.save_presets([{
        "id": preset["id"],
        "name": preset["name"],
        "permissions": {"dashboard": "view"},
    }])
    assert error == ""
    refreshed = manager.authenticate(token)
    assert manager.allowed(refreshed, "hosts", "view") is False

    # Пресет, назначенный пользователю, нельзя удалить.
    _state, error = manager.save_presets([_preset("Другой")])
    assert error

    # Смена пароля требует старый пароль и отзывает прежние сессии.
    token2, _principal, _error = manager.login("operator", "correct-horse", client="unit2")
    assert token2
    changed = _user(password="new-correct-horse")
    changed["id"] = user["id"]
    changed["preset_id"] = preset["id"]
    _users, error = manager.save_users([changed])
    assert error  # пароль нельзя сменить через общую форму пользователей
    new_token, changed_principal, error = manager.change_password(
        user["id"], "wrong-password", "new-correct-horse", client="unit3"
    )
    assert error and not new_token
    new_token, changed_principal, error = manager.change_password(
        user["id"], "correct-horse", "new-correct-horse", client="unit4"
    )
    assert error == "" and new_token and changed_principal
    assert manager.authenticate(token) is None
    assert manager.authenticate(token2) is None
    assert manager.authenticate(new_token)["username"] == "operator"
    assert manager.login("operator", "correct-horse", client="unit5")[0] == ""
    assert manager.login("operator", "new-correct-horse", client="unit6")[0]

    # Выключение пользователя тоже закрывает сессии.
    token3, _principal, _error = manager.login("operator", "new-correct-horse", client="unit7")
    disabled = _user()
    disabled.pop("password")
    disabled.update({"id": user["id"], "preset_id": preset["id"], "enabled": False})
    _users, error = manager.save_users([disabled])
    assert error == ""
    assert manager.authenticate(token3) is None


def test_preset_and_user_validation(tmp_path: Path):
    manager = AccessManager(_Cfg(), tmp_path / "access.json")

    assert manager.save_presets([{"name": "", "permissions": {"hosts": "view"}}])[1]
    assert manager.save_presets([
        {"name": "A", "permissions": {"hosts": "view"}},
        {"name": "a", "permissions": {"checks": "view"}},
    ])[1]
    assert manager.save_presets([{"name": "A", "permissions": {}}])[1]

    presets, _error = manager.save_presets([_preset()])
    preset_id = presets["presets"][0]["id"]
    payload = _user("bad name")
    payload["preset_id"] = preset_id
    assert manager.save_users([payload])[1]
    payload = _user(password="short")
    payload["preset_id"] = preset_id
    assert manager.save_users([payload])[1]
    payload = _user()
    payload["preset_id"] = "missing"
    assert manager.save_users([payload])[1]

    assert master_only("/api/settings/token/rotate") is True
    assert master_only("/api/settings/token/disable") is True
    assert master_only("/api/settings") is False


@pytest.fixture()
def auth_client(client):
    """Клиент с включённой проверкой токена; в конце возвращает настройки."""
    runtime = client.app.state.rt
    previous = {
        "enabled": runtime.cfg.get("auth.enabled", False),
        "token": runtime.cfg.get("auth.token", ""),
    }
    created: list[str] = []
    master = "test-master-token_access"
    runtime.cfg.set("auth.enabled", True)
    runtime.cfg.set("auth.token", master)
    try:
        yield client, runtime, master, created
    finally:
        # Удаляем тестовых пользователей и отключаем проверку обратно.
        try:
            state = client.get(
                "/api/access", headers={"X-MegaTool-Token": master}
            ).json()["data"]
            keep = [
                {
                    "id": user["id"],
                    "username": user["username"],
                    "display_name": user["display_name"],
                    "preset_id": user["preset_id"],
                    "enabled": user["enabled"],
                }
                for user in state["users"]
                if user["id"] not in created
            ]
            client.put(
                "/api/access/users",
                headers={"X-MegaTool-Token": master},
                json={"users": keep},
            )
        except Exception:
            pass
        for key, value in previous.items():
            runtime.cfg.set(f"auth.{key}", value)


def _public_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "username": user["username"],
        "display_name": user["display_name"],
        "preset_id": user["preset_id"],
        "enabled": user["enabled"],
        "owner": bool(user.get("owner", False)),
        "local_account": user.get("local_account", ""),
    }


def test_api_enforces_presets_and_master_only_settings(auth_client):
    client, _runtime, master, created = auth_client
    master_headers = {"X-MegaTool-Token": master}

    assert client.get("/api/state").status_code == 401
    assert client.get("/api/access").status_code == 401

    state = client.get("/api/access", headers=master_headers).json()["data"]
    viewer = next(item for item in state["presets"] if item["id"] == "preset-viewer")
    admin = next(item for item in state["presets"] if item["id"] == "preset-admin")

    created_users = client.put(
        "/api/access/users",
        headers=master_headers,
        json={"users": [
            {
                "username": "pytest_viewer",
                "display_name": "Pytest Viewer",
                "preset_id": viewer["id"],
                "password": "viewer-pass-123",
                "enabled": True,
            },
            {
                "username": "pytest_admin",
                "display_name": "Pytest Admin",
                "preset_id": admin["id"],
                "password": "admin-pass-123",
                "enabled": True,
            },
        ]},
    ).json()
    assert created_users["ok"] is True, created_users
    created.extend(user["id"] for user in created_users["data"]["users"]
                   if user["username"].startswith("pytest_"))

    body = client.get("/api/access", headers=master_headers).text
    assert "pbkdf2" not in body and "viewer-pass-123" not in body

    viewer_login = client.post(
        "/api/auth/login",
        json={"username": "pytest_viewer", "password": "viewer-pass-123"},
    ).json()
    assert viewer_login["ok"] is True
    viewer_token = viewer_login["data"]["token"]
    viewer_headers = {"X-MegaTool-Token": viewer_token}

    assert client.get("/api/state", headers=viewer_headers).status_code == 200
    assert client.get("/api/hosts", headers=viewer_headers).status_code == 200
    assert client.get("/api/agents", headers=viewer_headers).status_code == 200
    assert client.get("/api/settings", headers=viewer_headers).status_code == 403
    assert client.get("/api/access", headers=viewer_headers).status_code == 403
    assert client.put("/api/hosts", headers=viewer_headers, json={"hosts": []}).status_code == 403

    me = client.get("/api/auth/me", headers=viewer_headers).json()["data"]
    assert me["mode"] == "user"
    assert me["permissions"]["hosts"] == "view"
    assert "settings" not in me["permissions"]

    # Смена собственного пароля требует старый пароль и выдаёт новую сессию.
    wrong_change = client.post(
        "/api/auth/password", headers=viewer_headers,
        json={"current_password": "wrong-pass", "new_password": "viewer-pass-456"},
    ).json()
    assert wrong_change["ok"] is False
    changed = client.post(
        "/api/auth/password", headers=viewer_headers,
        json={"current_password": "viewer-pass-123", "new_password": "viewer-pass-456"},
    ).json()
    assert changed["ok"] is True, changed
    viewer_token = changed["data"]["token"]
    viewer_headers = {"X-MegaTool-Token": viewer_token}
    assert client.get("/api/state", headers=viewer_headers).status_code == 200
    assert client.post(
        "/api/auth/login",
        json={"username": "pytest_viewer", "password": "viewer-pass-123"},
    ).json()["ok"] is False
    assert client.post(
        "/api/auth/login",
        json={"username": "pytest_viewer", "password": "viewer-pass-456"},
    ).json()["ok"] is True

    # Даже пресет с manage в настройках не может забрать мастер-токен.
    admin_login = client.post(
        "/api/auth/login",
        json={"username": "pytest_admin", "password": "admin-pass-123"},
    ).json()
    assert admin_login["ok"] is True
    admin_headers = {"X-MegaTool-Token": admin_login["data"]["token"]}
    assert client.get("/api/settings", headers=admin_headers).status_code == 200
    assert client.post(
        "/api/settings/token/rotate", headers=admin_headers, json={}
    ).status_code == 403
    assert client.post(
        "/api/settings/token/disable", headers=admin_headers, json={}
    ).status_code == 403
    assert client.get("/api/access", headers=admin_headers).status_code == 200

    # Выключение пользователя отзывает его сессию.
    users = client.get("/api/access", headers=master_headers).json()["data"]["users"]
    payload = []
    for user in users:
        item = _public_user(user)
        if user["username"] == "pytest_viewer":
            item["enabled"] = False
        payload.append(item)
    saved = client.put(
        "/api/access/users", headers=master_headers, json={"users": payload}
    ).json()
    assert saved["ok"] is True, saved
    assert client.get("/api/state", headers=viewer_headers).status_code == 401
    assert client.post(
        "/api/auth/login",
        json={"username": "pytest_viewer", "password": "viewer-pass-456"},
    ).json()["ok"] is False


def _fake_account(name: str = "root", privileged: bool = True) -> dict:
    return {
        "username": name,
        "privileged": privileged,
        "role": "root" if privileged else "",
    }


def test_local_admin_setup_and_password_change(tmp_path: Path, monkeypatch):
    account = _fake_account()
    monkeypatch.setattr(localauth, "current_account", lambda: account)
    monkeypatch.setattr(localauth, "inspect_account", lambda name: account
                        if localauth.normalize_name(name) == "root" else None)
    monkeypatch.setattr(localauth, "is_privileged", lambda info: bool(info and info["privileged"]))

    manager = AccessManager(_Cfg(local_only=True), tmp_path / "access.json")
    _state, error = manager.bootstrap_local_admin("root", "short")
    assert error
    _state, error = manager.bootstrap_local_admin("bad name", "root-pass-123")
    assert error

    state, error = manager.bootstrap_local_admin("mega-admin", "root-pass-123")
    assert error == ""
    assert state["local_only"] is True
    user = state["users"][0]
    assert user["username"] == "mega-admin"
    assert user["local_account"] == "root"
    assert user["local_role"] == "root"
    assert "password_hash" not in json.dumps(state)
    assert "root-pass-123" not in json.dumps(state)
    raw = (tmp_path / "access.json").read_text(encoding="utf-8")
    assert "pbkdf2_sha256$" in raw and "root-pass-123" not in raw

    # Повторный setup невозможен.
    assert manager.bootstrap_local_admin("mega-admin", "other-pass-123")[1]

    # Пароль MegaTool, а не системный: вход только с заданным паролем.
    assert manager.login("mega-admin", "wrong-pass", client="setup-test")[0] == ""
    token, principal, error = manager.login("mega-admin", "root-pass-123", client="setup-test")
    assert error == "" and token and principal["kind"] == "user"

    # Смена только со старым паролем; прежняя сессия отзывается.
    assert manager.change_password(user["id"], "wrong-pass", "next-pass-123")[2]
    new_token, new_principal, error = manager.change_password(
        user["id"], "root-pass-123", "next-pass-123", client="setup-test-2"
    )
    assert error == "" and new_token and new_principal
    assert manager.authenticate(token) is None
    assert manager.authenticate(new_token) is not None
    assert manager.login("mega-admin", "root-pass-123", client="setup-test-3")[0] == ""
    assert manager.login("mega-admin", "next-pass-123", client="setup-test-4")[0]

    # При local_only новые пользователи обязаны иметь локальный root-аккаунт.
    # Владелец должен остаться в списке: иначе сработает защита от потери
    # единственного владельца.
    owner = next(item for item in state["users"] if item["owner"])
    owner_entry = {
        key: owner[key] for key in (
            "id", "username", "display_name", "preset_id", "enabled",
            "owner", "local_account",
        )
    }
    base = {
        "username": "operator", "display_name": "Operator",
        "preset_id": "preset-admin", "password": "operator-pass-123",
        "enabled": True,
    }
    assert manager.save_users([owner_entry, base])[1]
    allowed = dict(base, local_account="root")
    saved, error = manager.save_users([owner_entry, allowed])
    assert error == ""
    operator = next(item for item in saved["users"] if item["username"] == "operator")
    assert operator["local_account"] == "root"
    assert next(item for item in saved["users"] if item["id"] == owner["id"])["owner"] is True


def test_only_owner_manages_admins_and_presets(tmp_path: Path, monkeypatch):
    account = _fake_account()
    monkeypatch.setattr(localauth, "current_account", lambda: account)
    monkeypatch.setattr(localauth, "inspect_account", lambda name: account
                        if localauth.normalize_name(name) == "root" else None)
    monkeypatch.setattr(localauth, "is_privileged", lambda info: bool(info and info["privileged"]))

    manager = AccessManager(_Cfg(local_only=True), tmp_path / "access.json")
    state, error = manager.bootstrap_local_admin("owner", "owner-pass-123")
    assert error == ""
    owner = state["users"][0]
    assert owner["owner"] is True

    def entry(user: dict, **changes) -> dict:
        item = _public_user(user)
        item.update(changes)
        return item

    owner_entry = entry(owner)
    owner_principal = manager.login("owner", "owner-pass-123", client="owner-unit")[1]
    assert owner_principal["owner"] is True

    admin_data = {
        "username": "admin", "display_name": "Admin", "preset_id": "preset-admin",
        "password": "admin-pass-123", "enabled": True, "local_account": "root",
    }
    saved, error = manager.save_users([owner_entry, admin_data], owner_principal)
    assert error == ""
    admin = next(item for item in saved["users"] if item["username"] == "admin")
    admin_principal = manager.login("admin", "admin-pass-123", client="admin-unit")[1]
    assert admin_principal["owner"] is False

    # Рядовой администратор не может создать ещё одного администратора.
    newcomer = {
        "username": "second", "display_name": "Second", "preset_id": "preset-admin",
        "password": "second-pass-123", "enabled": True, "local_account": "root",
    }
    _state, error = manager.save_users(
        [owner_entry, entry(admin), newcomer], admin_principal
    )
    assert error

    # ... не может менять или удалять владельца.
    _state, error = manager.save_users(
        [entry(owner_entry, display_name="Hacked"), entry(admin)], admin_principal
    )
    assert error
    _state, error = manager.save_users([entry(admin)], admin_principal)
    assert error

    # ... но может менять обычные пресеты, даже если в запросе присутствует
    # неизменённый административный пресет.
    def presets_payload(*, change_admin: bool = False, change_viewer: bool = False) -> list[dict]:
        items = []
        for preset in manager.state()["presets"]:
            name = preset["name"]
            if change_admin and preset["id"] == "preset-admin":
                name += " (changed)"
            if change_viewer and preset["id"] == "preset-viewer":
                name += " (changed)"
            items.append({
                "id": preset["id"], "name": name,
                "description": preset["description"],
                "permissions": preset["permissions"],
            })
        return items

    saved, error = manager.save_presets(
        presets_payload(change_viewer=True), admin_principal
    )
    assert error == ""
    viewer = next(item for item in saved["presets"] if item["id"] == "preset-viewer")
    assert viewer["name"].endswith("(changed)")

    # ... и не может менять административные пресеты.
    _state, error = manager.save_presets(
        presets_payload(change_admin=True), admin_principal
    )
    assert error

    # Владельцу нельзя назначить пресет без управления доступом и настройками.
    _state, error = manager.save_users(
        [entry(owner_entry, preset_id="preset-viewer"), entry(admin)], owner_principal
    )
    assert error

    # Нельзя оставить систему без включённого владельца.
    _state, error = manager.save_users(
        [entry(owner_entry, owner=False), entry(admin)], owner_principal
    )
    assert error
    _state, error = manager.save_users(
        [entry(owner_entry, enabled=False), entry(admin)], owner_principal
    )
    assert error

    # Владелец может создать второго владельца с административным пресетом.
    peer = {
        "username": "peer", "display_name": "Peer", "preset_id": "preset-admin",
        "password": "peer-pass-123", "enabled": True, "local_account": "root",
        "owner": True,
    }
    saved, error = manager.save_users(
        [owner_entry, entry(admin), peer], owner_principal
    )
    assert error == ""
    peer_row = next(item for item in saved["users"] if item["username"] == "peer")
    assert peer_row["owner"] is True
    peer_principal = manager.login("peer", "peer-pass-123", client="peer-unit")[1]
    assert peer_principal["owner"] is True


def test_local_admin_setup_requires_root(tmp_path: Path, monkeypatch):
    account = _fake_account("desktop", privileged=False)
    monkeypatch.setattr(localauth, "current_account", lambda: account)
    monkeypatch.setattr(localauth, "is_privileged", lambda info: bool(info and info["privileged"]))
    manager = AccessManager(_Cfg(), tmp_path / "access.json")
    _state, error = manager.bootstrap_local_admin("desktop", "desktop-pass-123")
    assert error
    assert manager.user_count() == 0


def test_api_first_local_admin_setup(client, monkeypatch):
    runtime = client.app.state.rt
    previous_enabled = runtime.cfg.get("auth.enabled", False)
    previous_local = runtime.cfg.get("auth.local_only", False)
    account = _fake_account()
    monkeypatch.setattr(localauth, "current_account", lambda: account)
    monkeypatch.setattr(localauth, "inspect_account", lambda name: account
                        if localauth.normalize_name(name) == "root" else None)
    monkeypatch.setattr(localauth, "is_privileged", lambda info: bool(info and info["privileged"]))
    token = ""
    try:
        # Начинаем с пустого списка пользователей в тестовом профиле.
        assert client.put("/api/access/users", json={"users": []}).json()["ok"] is True
        runtime.cfg.set("auth.enabled", False)
        runtime.cfg.set("auth.local_only", True)
        status = client.get("/api/auth/setup-status").json()["data"]
        assert status["setup_required"] is True
        assert status["setup_available"] is True
        assert status["os_user"] == "root"

        setup = client.post(
            "/api/auth/setup",
            json={"username": "root", "password": "root-pass-123"},
        ).json()
        assert setup["ok"] is True, setup
        token = setup["data"]["token"]
        assert setup["data"]["user"]["local_account"] == "root"
        assert setup["data"]["user"]["owner"] is True
        assert runtime.cfg.get("auth.enabled") is True
        assert runtime.cfg.get("auth.local_only") is True

        me = client.get(
            "/api/auth/me", headers={"X-MegaTool-Token": token}
        ).json()["data"]
        assert me["mode"] == "user"
        assert me["user"]["local_role"] == "root"
        assert me["user"]["owner"] is True

        # Повторный setup уже невозможен, но мастер-токен/вход продолжают работать.
        assert client.post(
            "/api/auth/setup",
            json={"username": "root", "password": "other-pass-123"},
        ).json()["ok"] is False
    finally:
        if token:
            # Владельца нельзя удалить, оставив систему без владельца.
            # Для очистки тестового профиля временно включаем локальный режим.
            runtime.cfg.set("auth.enabled", False)
            client.put("/api/access/users", json={"users": []})
        runtime.cfg.set("auth.enabled", previous_enabled)
        runtime.cfg.set("auth.local_only", previous_local)

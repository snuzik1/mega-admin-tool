"""Пользователи, пресеты прав и сессии MegaTool.

Пароли не попадают в config.json и не экспортируются: они хранятся только в
`access.json` в виде PBKDF2-хэша (0600). Пресеты — именованные наборы прав по
разделам; пользователь получает один пресет. Мастер-токен из настроек всегда
имеет полный доступ и позволяет восстановить управление.

Права проверяются на сервере в middleware (server.py), а не только в интерфейсе.
Уровни: none, view (только чтение), manage (чтение и изменения).
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from . import localauth
from .config import HOME_DIR
from .i18n import t
from .util import iso, read_json, safe_id, write_json

ACCESS_PATH = HOME_DIR / "access.json"
ACCESS_VERSION = 1

MAX_USERS = 100
MAX_PRESETS = 30
MIN_PASSWORD = 8
MAX_PASSWORD = 200
PBKDF2_ITERATIONS = 210_000
SESSION_TTL = 12 * 3600
LOGIN_WINDOW = 60.0
LOGIN_LIMIT = 10

SECTIONS = (
    "dashboard",
    "checks",
    "hosts",
    "containers",
    "balancer",
    "catalog",
    "bundles",
    "configs",
    "ansible",
    "servers",
    "k8s",
    "changes",
    "jobs",
    "settings",
    "access",
)
LEVELS = ("view", "manage")

_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,31}$")

# Пресет считается административным, если даёт управление доступом или
# настройками: такие пресеты и владельца могут менять только владелец MegaTool.
ELEVATED_SECTIONS = ("access", "settings")


def preset_is_elevated(preset: dict[str, Any] | None) -> bool:
    """True, если пресет позволяет управлять пользователями или настройками."""
    permissions = (preset or {}).get("permissions") or {}
    return any(permissions.get(section) == "manage" for section in ELEVATED_SECTIONS)


def preset_is_owner_capable(preset: dict[str, Any] | None) -> bool:
    """Владельцу нужны управление доступом и настройками, чтобы управлять
    администраторами и при необходимости выдать себе полный пресет."""
    permissions = (preset or {}).get("permissions") or {}
    return all(permissions.get(section) == "manage" for section in ELEVATED_SECTIONS)


def user_is_elevated(user: dict[str, Any] | None, presets: dict[str, dict[str, Any]]) -> bool:
    """True для владельца и для пользователя с административным пресетом."""
    if not user:
        return False
    if user.get("owner"):
        return True
    return preset_is_elevated(presets.get(str(user.get("preset_id") or "")))


_USER_FIELDS = (
    "username", "display_name", "preset_id", "enabled", "owner",
    "password_hash", "local_account",
)


def users_equal(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Сравнивает значимые поля пользователя (без служебных дат)."""
    return all(left.get(field) == right.get(field) for field in _USER_FIELDS)


# --------------------------------------------------------------------------- #
#  Пароли
# --------------------------------------------------------------------------- #

def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def _burn_password_check(password: str) -> None:
    """Тратит столько же времени, сколько проверка пароля (анти-энумерация)."""
    hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), b"\0" * 16, PBKDF2_ITERATIONS)


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_text, salt_hex, digest_hex = str(encoded).split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        if iterations < 10_000 or iterations > 2_000_000:
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (TypeError, ValueError):
        return False
    if not salt or not expected:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def _password_error(password: str) -> str:
    if len(password) < MIN_PASSWORD:
        return t("access.badPassword", limit=MIN_PASSWORD)
    if len(password) > MAX_PASSWORD:
        return t("access.tooLongPassword", limit=MAX_PASSWORD)
    return ""


# --------------------------------------------------------------------------- #
#  Очистка полей
# --------------------------------------------------------------------------- #

def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _permissions(raw: Any, *, require_one: bool) -> tuple[dict[str, str], str]:
    if not isinstance(raw, dict):
        return ({} if not require_one else {}), (t("access.badPermissions") if require_one else "")
    out: dict[str, str] = {}
    for section, value in raw.items():
        if section not in SECTIONS:
            continue
        level = str(value or "").strip().lower()
        if level in ("none", "off", "disabled", ""):
            continue
        if level not in LEVELS:
            return {}, t("access.badPermissions")
        out[section] = level
    if require_one and not out:
        return {}, t("access.badPermissions")
    return out, ""


def _default_presets() -> list[dict[str, Any]]:
    viewer = {section: "view" for section in SECTIONS if section not in ("settings", "access")}
    operator = {
        "dashboard": "view", "checks": "manage", "hosts": "manage",
        "containers": "manage", "balancer": "manage", "catalog": "manage",
        "bundles": "view", "configs": "view", "ansible": "manage",
        "servers": "manage", "k8s": "view", "changes": "view",
        "jobs": "manage",
    }
    admin = {section: "manage" for section in SECTIONS}
    return [
        {
            "id": "preset-viewer",
            "name": t("access.default.viewerName"),
            "description": t("access.default.viewerDesc"),
            "permissions": viewer,
        },
        {
            "id": "preset-operator",
            "name": t("access.default.operatorName"),
            "description": t("access.default.operatorDesc"),
            "permissions": operator,
        },
        {
            "id": "preset-admin",
            "name": t("access.default.adminName"),
            "description": t("access.default.adminDesc"),
            "permissions": admin,
        },
    ]


def clean_presets(raw: Any, old: list[dict[str, Any]], users: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    """Проверяет список пресетов. Возвращает (пресеты, ошибка)."""
    if not isinstance(raw, list):
        return [], t("access.badList")
    if len(raw) > MAX_PRESETS:
        return [], t("access.tooManyPresets", limit=MAX_PRESETS)

    out: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    used_names: set[str] = set()
    for source in raw:
        if not isinstance(source, dict):
            return [], t("access.badList")
        name = _text(source.get("name"), 60)
        if not name:
            return [], t("access.badName")
        folded = name.casefold()
        if folded in used_names:
            return [], t("access.duplicatePreset", name=name)
        used_names.add(folded)
        permissions, error = _permissions(source.get("permissions"), require_one=True)
        if error:
            return [], error
        preset_id = safe_id(str(source.get("id") or ""), "")
        if not preset_id or preset_id in used_ids:
            preset_id = f"preset-{secrets.token_hex(4)}"
        used_ids.add(preset_id)
        out.append({
            "id": preset_id,
            "name": name,
            "description": _text(source.get("description"), 300),
            "permissions": permissions,
        })

    old_ids = {str(item.get("id")) for item in old}
    removed = old_ids - used_ids
    if removed and any(str(user.get("preset_id")) in removed for user in users):
        return [], t("access.presetInUse")
    return out, ""


def clean_users(
    raw: Any,
    old: list[dict[str, Any]],
    presets: list[dict[str, Any]],
    *,
    require_local: bool = False,
) -> tuple[list[dict[str, Any]], str]:
    """Проверяет пользователей.

    Пароль задаётся только при создании. У существующего пользователя пароль
    меняется исключительно через смену со старым паролем, поэтому поле password
    для него отклоняется. Если указана локальная учётная запись, она должна
    существовать в ОС и иметь права root/sudo/administrator.
    """
    if not isinstance(raw, list):
        return [], t("access.badList")
    if len(raw) > MAX_USERS:
        return [], t("access.tooManyUsers", limit=MAX_USERS)
    preset_ids = {str(item.get("id")) for item in presets}
    old_by_id = {str(item.get("id")): item for item in old}
    used_ids: set[str] = set()
    used_names: set[str] = set()
    now = time.time()
    out: list[dict[str, Any]] = []
    for source in raw:
        if not isinstance(source, dict):
            return [], t("access.badList")
        username = _text(source.get("username"), 32).lower()
        if not _USERNAME_RE.match(username):
            return [], t("access.badUsername")
        folded = username.casefold()
        if folded in used_names:
            return [], t("access.duplicateUser", name=username)
        used_names.add(folded)
        preset_id = safe_id(str(source.get("preset_id") or ""), "")
        if preset_id not in preset_ids:
            return [], t("access.badPresetRef")
        user_id = safe_id(str(source.get("id") or ""), "")
        previous = old_by_id.get(user_id) if user_id else None
        if not user_id or user_id in used_ids:
            user_id = f"user-{secrets.token_hex(4)}"
            previous = None
        used_ids.add(user_id)
        display_name = _text(source.get("display_name"), 80) or username

        local_account = localauth.clean_name(source.get("local_account"))
        local_role = ""
        if local_account:
            account = localauth.inspect_account(local_account)
            if account is None:
                return [], t("access.localAccountMissing", account=local_account)
            if not localauth.is_privileged(account):
                return [], t("access.localAccountNotRoot", account=local_account)
            local_account = localauth.clean_name(account.get("username")) or local_account
            local_role = localauth.display_role(account)
        elif require_local:
            return [], t("access.localAccountRequired")

        password = str(source.get("password") or "")
        if previous:
            if password:
                return [], t("access.passwordOldRequired")
            password_hash = str(previous.get("password_hash") or "")
            if not password_hash:
                return [], t("access.passwordRequired")
        else:
            error = _password_error(password)
            if not password:
                return [], t("access.passwordRequired")
            if error:
                return [], error
            password_hash = _hash_password(password)
        if previous is not None and "owner" not in source:
            owner = bool(previous.get("owner"))
        else:
            owner = source.get("owner") is True
        out.append({
            "id": user_id,
            "username": username,
            "display_name": display_name,
            "preset_id": preset_id,
            "enabled": source.get("enabled", True) is not False,
            "owner": owner,
            "password_hash": password_hash,
            "local_account": local_account,
            "local_role": local_role,
            "created_at": str((previous or {}).get("created_at") or iso(now)),
            "updated_at": iso(now),
        })
    return out, ""


# --------------------------------------------------------------------------- #
#  Менеджер доступа
# --------------------------------------------------------------------------- #

class AccessManager:
    def __init__(self, cfg, path: Path | str | None = None) -> None:
        self.cfg = cfg
        self.path = Path(path) if path else ACCESS_PATH
        self._lock = threading.RLock()
        self._presets: list[dict[str, Any]] = []
        self._users: list[dict[str, Any]] = []
        self._sessions: dict[str, dict[str, Any]] = {}
        self._login_failures: dict[str, deque[float]] = defaultdict(deque)
        self._load()

    # ------------------------------------------------------------------ #
    #  Хранение
    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        raw = read_json(self.path, None)
        if not isinstance(raw, dict):
            self._presets = _default_presets()
            self._users = []
            self._save()
            return
        raw_presets = raw.get("presets")
        raw_users = raw.get("users")
        presets: list[dict[str, Any]] = []
        used_ids: set[str] = set()
        if isinstance(raw_presets, list):
            for item in raw_presets[:MAX_PRESETS]:
                if not isinstance(item, dict):
                    continue
                preset_id = safe_id(str(item.get("id") or ""), "")
                name = _text(item.get("name"), 60)
                permissions, _error = _permissions(item.get("permissions"), require_one=False)
                if not preset_id or preset_id in used_ids or not name or not permissions:
                    continue
                used_ids.add(preset_id)
                presets.append({
                    "id": preset_id,
                    "name": name,
                    "description": _text(item.get("description"), 300),
                    "permissions": permissions,
                })
        users: list[dict[str, Any]] = []
        used_users: set[str] = set()
        used_names: set[str] = set()
        if isinstance(raw_users, list):
            for item in raw_users[:MAX_USERS]:
                if not isinstance(item, dict):
                    continue
                user_id = safe_id(str(item.get("id") or ""), "")
                username = _text(item.get("username"), 32).lower()
                preset_id = safe_id(str(item.get("preset_id") or ""), "")
                password_hash = str(item.get("password_hash") or "")
                if (not user_id or user_id in used_users or not _USERNAME_RE.match(username)
                        or username in used_names or preset_id not in used_ids
                        or not password_hash):
                    continue
                used_users.add(user_id)
                used_names.add(username)
                users.append({
                    "id": user_id,
                    "username": username,
                    "display_name": _text(item.get("display_name"), 80) or username,
                    "preset_id": preset_id,
                    "enabled": item.get("enabled", True) is not False,
                    "owner": item.get("owner") is True,
                    "password_hash": password_hash,
                    "local_account": localauth.clean_name(item.get("local_account")),
                    "local_role": _text(item.get("local_role"), 32),
                    "created_at": str(item.get("created_at") or iso()),
                    "updated_at": str(item.get("updated_at") or iso()),
                })
        self._presets = presets
        self._users = users
        if users and not any(user.get("owner") for user in users):
            # Совместимость: в старых access.json владельца не было. Назначаем
            # первого включённого администратора владельцем, чтобы правило
            # «админов создаёт только владелец» работало после обновления.
            presets_by_id = {preset["id"]: preset for preset in presets}
            candidate = next(
                (user for user in users
                 if user.get("enabled", True)
                 and preset_is_owner_capable(presets_by_id.get(user["preset_id"]))),
                None,
            )
            if candidate is not None:
                candidate["owner"] = True
                self._save()

    def _save(self) -> None:
        with self._lock:
            payload = {
                "version": ACCESS_VERSION,
                "updated": iso(),
                "presets": self._presets,
                "users": self._users,
            }
            try:
                write_json(self.path, payload, mode=0o600)
            except OSError:
                pass

    # ------------------------------------------------------------------ #
    #  Публичное состояние
    # ------------------------------------------------------------------ #
    def _preset_name(self, preset_id: str) -> str:
        for preset in self._presets:
            if preset["id"] == preset_id:
                return str(preset.get("name") or "")
        return ""

    def _public_user(self, user: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": user["id"],
            "username": user["username"],
            "display_name": user.get("display_name") or user["username"],
            "preset_id": user["preset_id"],
            "preset_name": self._preset_name(user["preset_id"]),
            "enabled": bool(user.get("enabled", True)),
            "owner": bool(user.get("owner")),
            "password_set": bool(user.get("password_hash")),
            "local_account": user.get("local_account", ""),
            "local_role": user.get("local_role", ""),
            "created_at": user.get("created_at", ""),
            "updated_at": user.get("updated_at", ""),
            "active_sessions": sum(
                1 for session in self._sessions.values()
                if session.get("user_id") == user["id"] and session.get("expires", 0) > time.time()
            ),
        }

    def user_count(self) -> int:
        with self._lock:
            return len(self._users)

    def summary(self) -> dict[str, int]:
        with self._lock:
            return {
                "users": len(self._users),
                "presets": len(self._presets),
                "sessions": sum(1 for session in self._sessions.values()
                                if session.get("expires", 0) > time.time()),
            }

    @staticmethod
    def _actor_is_owner(actor: dict[str, Any] | None) -> bool:
        """Мастер-токен, локальный режим и owner могут управлять админами."""
        if actor is None:
            return True
        if str(actor.get("kind") or "") in ("master", "local"):
            return True
        return bool(actor.get("owner"))

    @staticmethod
    def _has_owner(users: list[dict[str, Any]]) -> bool:
        return any(user.get("owner") for user in users)

    @staticmethod
    def _has_enabled_owner(users: list[dict[str, Any]]) -> bool:
        return any(user.get("owner") and user.get("enabled", True) for user in users)

    def state(self) -> dict[str, Any]:
        with self._lock:
            users = [self._public_user(user) for user in self._users]
            preset_counts: dict[str, int] = defaultdict(int)
            for user in self._users:
                preset_counts[str(user.get("preset_id"))] += 1
            presets = [
                {
                    "id": preset["id"],
                    "name": preset["name"],
                    "description": preset.get("description", ""),
                    "permissions": dict(preset.get("permissions") or {}),
                    "user_count": preset_counts.get(preset["id"], 0),
                }
                for preset in self._presets
            ]
            active = sum(1 for session in self._sessions.values() if session.get("expires", 0) > time.time())
        return {
            "presets": presets,
            "users": users,
            "auth_enabled": bool(self.cfg.get("auth.enabled", False)),
            "local_only": bool(self.cfg.get("auth.local_only", False)),
            "sessions": active,
            "sections": list(SECTIONS),
        }

    # ------------------------------------------------------------------ #
    #  Сохранение
    # ------------------------------------------------------------------ #
    def save_presets(
        self, raw: Any, actor: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any], str]:
        with self._lock:
            cleaned, error = clean_presets(raw, self._presets, self._users)
            if error:
                return {}, error
            if self._has_owner(self._users) and not self._actor_is_owner(actor):
                old_by_id = {str(item["id"]): item for item in self._presets}
                new_by_id = {str(item["id"]): item for item in cleaned}
                protected_ids = {
                    str(user.get("preset_id") or "")
                    for user in self._users
                    if user_is_elevated(user, old_by_id)
                }
                for preset_id in old_by_id.keys() | new_by_id.keys():
                    old = old_by_id.get(preset_id)
                    new = new_by_id.get(preset_id)
                    if old == new:
                        continue
                    if preset_is_elevated(old) or preset_is_elevated(new):
                        return {}, t("access.ownerOnlyPreset")
                    if preset_id in protected_ids:
                        return {}, t("access.ownerOnlyPreset")
            self._presets = cleaned
            self._save()
        return self.state(), ""

    def save_users(
        self, raw: Any, actor: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any], str]:
        with self._lock:
            require_local = bool(self.cfg.get("auth.local_only", False))
            cleaned, error = clean_users(
                raw, self._users, self._presets, require_local=require_local
            )
            if error:
                return {}, error

            owner_actor = self._actor_is_owner(actor)
            had_owner = self._has_owner(self._users)
            had_enabled_owner = self._has_enabled_owner(self._users)
            presets_by_id = {str(preset["id"]): preset for preset in self._presets}
            old_by_id = {str(user["id"]): user for user in self._users}
            new_by_id = {str(user["id"]): user for user in cleaned}

            for user in cleaned:
                if user.get("owner") and not preset_is_owner_capable(
                        presets_by_id.get(str(user.get("preset_id") or ""))):
                    return {}, t("access.ownerPreset")

            if had_owner and not owner_actor:
                # Рядовой администратор не может трогать владельцев и
                # пользователей с административными пресетами.
                for user_id, previous in old_by_id.items():
                    current = new_by_id.get(user_id)
                    if current is None:
                        if user_is_elevated(previous, presets_by_id):
                            return {}, t("access.ownerOnly")
                        continue
                    if (user_is_elevated(previous, presets_by_id)
                            or user_is_elevated(current, presets_by_id)):
                        if not users_equal(previous, current):
                            return {}, t("access.ownerOnly")
                for user_id, current in new_by_id.items():
                    if user_id not in old_by_id and user_is_elevated(current, presets_by_id):
                        return {}, t("access.ownerOnly")
            elif not owner_actor:
                # Старая установка без владельца: назначать админов можно, но
                # флаг владельца — только мастер-токену/локальному режиму.
                for user_id, current in new_by_id.items():
                    previous = old_by_id.get(user_id)
                    if previous is None:
                        if current.get("owner"):
                            return {}, t("access.ownerOnly")
                    elif bool(previous.get("owner")) != bool(current.get("owner")):
                        return {}, t("access.ownerOnly")

            if had_owner and not self._has_owner(cleaned):
                return {}, t("access.ownerRequired")
            if had_enabled_owner and not self._has_enabled_owner(cleaned):
                return {}, t("access.ownerRequired")

            for user_id in old_by_id.keys() - new_by_id.keys():
                self._revoke(user_id)
            for user_id, user in new_by_id.items():
                previous = old_by_id.get(user_id)
                if previous is None:
                    continue
                if (not user.get("enabled", True)
                        or user.get("password_hash") != previous.get("password_hash")):
                    self._revoke(user_id)
            self._users = cleaned
            self._save()
        return self.state(), ""

    # ------------------------------------------------------------------ #
    #  Сессии и вход
    # ------------------------------------------------------------------ #
    def _clean_sessions(self) -> None:
        now = time.time()
        for token in [key for key, value in self._sessions.items() if value.get("expires", 0) <= now]:
            self._sessions.pop(token, None)

    def _revoke(self, user_id: str) -> None:
        for token in [key for key, value in self._sessions.items() if value.get("user_id") == user_id]:
            self._sessions.pop(token, None)

    def _login_allowed(self, client: str) -> bool:
        now = time.monotonic()
        items = self._login_failures[client]
        while items and now - items[0] > LOGIN_WINDOW:
            items.popleft()
        return len(items) < LOGIN_LIMIT

    def _login_failed(self, client: str) -> None:
        self._login_failures[client].append(time.monotonic())

    def _login_ok(self, client: str) -> None:
        self._login_failures.pop(client, None)

    def login(self, username: str, password: str, client: str = "local") -> tuple[str, dict[str, Any] | None, str]:
        with self._lock:
            if not self._login_allowed(client):
                return "", None, t("access.tooManyAttempts")
            normalized = str(username or "").strip().lower()
            user = next((item for item in self._users if item["username"] == normalized), None)
            if not user or not user.get("enabled", True):
                _burn_password_check(str(password or ""))
                self._login_failed(client)
                return "", None, t("access.invalidLogin")
            if not _verify_password(str(password or ""), str(user.get("password_hash") or "")):
                self._login_failed(client)
                return "", None, t("access.invalidLogin")
            if self.cfg.get("auth.local_only", False) and not user.get("local_account"):
                self._login_failed(client)
                return "", None, t("access.invalidLogin")
            if user.get("local_account"):
                account = localauth.inspect_account(str(user.get("local_account") or ""))
                if not localauth.is_privileged(account):
                    self._login_failed(client)
                    return "", None, t("access.invalidLogin")
            preset = next((item for item in self._presets if item["id"] == user["preset_id"]), None)
            if not preset:
                self._login_failed(client)
                return "", None, t("access.invalidLogin")
            token = self._new_session(user)
            self._login_ok(client)
            return token, self._principal(user, preset), ""

    def _new_session(self, user: dict[str, Any]) -> str:
        self._clean_sessions()
        token = secrets.token_urlsafe(32)
        self._sessions[token] = {
            "user_id": user["id"],
            "created": time.time(),
            "expires": time.time() + SESSION_TTL,
        }
        return token

    def change_password(
        self,
        user_id: str,
        current_password: str,
        new_password: str,
        client: str = "local",
    ) -> tuple[str, dict[str, Any] | None, str]:
        """Меняет пароль только после проверки старого и выдаёт новую сессию."""
        with self._lock:
            if not self._login_allowed(client):
                return "", None, t("access.tooManyAttempts")
            user = next((item for item in self._users if item["id"] == user_id), None)
            if not user or not user.get("enabled", True):
                _burn_password_check(str(current_password or ""))
                self._login_failed(client)
                return "", None, t("access.invalidCurrentPassword")
            if not _verify_password(
                str(current_password or ""), str(user.get("password_hash") or "")
            ):
                self._login_failed(client)
                return "", None, t("access.invalidCurrentPassword")
            error = _password_error(new_password)
            if error:
                return "", None, error
            if hmac.compare_digest(str(current_password), str(new_password)):
                return "", None, t("access.passwordSame")
            user["password_hash"] = _hash_password(new_password)
            user["updated_at"] = iso()
            self._revoke(user["id"])
            self._save()
            preset = next((item for item in self._presets if item["id"] == user["preset_id"]), None)
            if not preset:
                self._login_failed(client)
                return "", None, t("access.invalidLogin")
            token = self._new_session(user)
            self._login_ok(client)
            return token, self._principal(user, preset), ""

    def setup_local_admin(self, username: str = "") -> tuple[dict[str, Any], str]:
        """Готовит данные первого владельца MegaTool из текущей локальной учётки.

        Доступно только при пустом списке пользователей. MegaTool должен быть
        запущен от имени root/sudo-пользователя или администратора Windows.
        Логин MegaTool можно выбрать отдельно от имени локальной учётной записи.
        """
        account = localauth.current_account()
        if not localauth.is_privileged(account):
            return {}, t("access.setupNeedsRoot")
        actual = str((account or {}).get("username") or "")
        if not localauth.normalize_name(actual):
            return {}, t("access.setupNeedsRoot")
        username_value = _text(username, 32).lower()
        if not username_value:
            return {}, t("access.setupLoginRequired")
        if not _USERNAME_RE.match(username_value):
            return {}, t("access.badUsername")
        with self._lock:
            if self._users:
                return {}, t("access.setupAlreadyDone")
            preset = next(
                (item for item in self._presets if item["id"] == "preset-admin"), None
            )
            if not preset:
                return {}, t("access.setupPresetMissing")
            return {
                "username": username_value,
                "display_name": _text(actual, 80) or username_value,
                "local_account": actual,
                "local_role": localauth.display_role(account),
                "preset_id": preset["id"],
                "owner": True,
            }, ""

    def bootstrap_local_admin(
        self, username: str, password: str
    ) -> tuple[dict[str, Any], str]:
        """Создаёт первого локального администратора и сохраняет пароль."""
        with self._lock:
            state, error = self.setup_local_admin(username)
            if error:
                return {}, error
            password_problem = _password_error(password)
            if password_problem:
                return {}, password_problem
            user = {
                "id": f"user-{secrets.token_hex(4)}",
                "username": state["username"],
                "display_name": state["display_name"],
                "preset_id": state["preset_id"],
                "enabled": True,
                "owner": True,
                "password_hash": _hash_password(password),
                "local_account": state["local_account"],
                "local_role": state["local_role"],
                "created_at": iso(),
                "updated_at": iso(),
            }
            self._users = [user]
            self._save()
            return self.state(), ""

    def logout(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(str(token or ""), None)

    def _principal(self, user: dict[str, Any], preset: dict[str, Any]) -> dict[str, Any]:
        return {
            "kind": "user",
            "user_id": user["id"],
            "username": user["username"],
            "display_name": user.get("display_name") or user["username"],
            "preset_id": preset["id"],
            "preset_name": preset.get("name", ""),
            "owner": bool(user.get("owner", False)),
            "local_account": user.get("local_account", ""),
            "local_role": user.get("local_role", ""),
            "permissions": dict(preset.get("permissions") or {}),
        }

    def authenticate(self, token: str | None) -> dict[str, Any] | None:
        value = str(token or "")
        if not value:
            return None
        with self._lock:
            self._clean_sessions()
            session = self._sessions.get(value)
            if not session:
                return None
            user = next((item for item in self._users if item["id"] == session.get("user_id")), None)
            if not user or not user.get("enabled", True):
                self._sessions.pop(value, None)
                return None
            preset = next((item for item in self._presets if item["id"] == user["preset_id"]), None)
            if not preset:
                self._sessions.pop(value, None)
                return None
            return self._principal(user, preset)

    # ------------------------------------------------------------------ #
    #  Права
    # ------------------------------------------------------------------ #
    @staticmethod
    def master_principal() -> dict[str, Any]:
        return {
            "kind": "master",
            "username": "master",
            "display_name": "master",
            "preset_id": "",
            "preset_name": "",
            "permissions": {section: "manage" for section in SECTIONS},
            "is_master": True,
        }

    @staticmethod
    def local_principal() -> dict[str, Any]:
        return {
            "kind": "local",
            "username": "local",
            "display_name": "local",
            "preset_id": "",
            "preset_name": "",
            "permissions": {section: "manage" for section in SECTIONS},
            "is_master": True,
        }

    @staticmethod
    def allowed(principal: dict[str, Any] | None, section: str, needed: str = "view") -> bool:
        if not principal:
            return False
        if principal.get("kind") in ("master", "local"):
            return True
        level = (principal.get("permissions") or {}).get(section, "none")
        if needed == "view":
            return level in LEVELS
        return level == "manage"


# --------------------------------------------------------------------------- #
#  Маршруты -> права
# --------------------------------------------------------------------------- #

def master_only(path: str) -> bool:
    """Ротация/отключение мастер-токена — только для мастер-токена или local."""
    return path.startswith("/api/settings/token/")


def required_permission(method: str, path: str) -> tuple[str, str] | None:
    """Возвращает (раздел, уровень) для API-маршрута.

    GET/HEAD/OPTIONS требуют view, изменяющие методы — manage. Неизвестные
    маршруты закрыты настройками (fail closed), чтобы новый обработчик не
    оказался случайно доступен наблюдателю.
    """
    if path in ("/api/health", "/api/version", "/api/openapi.json"):
        return None
    if path.startswith(("/api/auth/", "/api/docs", "/api/redoc")):
        return None
    read = method.upper() in ("GET", "HEAD", "OPTIONS")
    # Метрики агентов нужны карточке дашборда; настройка агентов остаётся
    # в разделе settings (POST /api/agents/test и PATCH настроек).
    if read and path == "/api/agents":
        return "dashboard", "view"
    prefix_rules = (
        ("/api/access", "access"),
        ("/api/settings", "settings"),
        ("/api/paths", "settings"),
        ("/api/agents", "settings"),
        ("/api/checks", "checks"),
        ("/api/hosts", "hosts"),
        ("/api/servers", "servers"),
        ("/api/bundles", "bundles"),
        ("/api/containers", "containers"),
        ("/api/compose", "containers"),
        ("/api/engine", "containers"),
        ("/api/engines", "containers"),
        ("/api/balancers", "balancer"),
        ("/api/catalog", "catalog"),
        ("/api/files", "configs"),
        ("/api/templates", "configs"),
        ("/api/ansible", "ansible"),
        ("/api/k8s", "k8s"),
        ("/api/jobs", "jobs"),
        ("/api/audit", "changes"),
        ("/api/changes", "changes"),
        ("/api/state", "dashboard"),
        ("/api/info", "dashboard"),
        ("/api/metrics", "dashboard"),
        ("/api/processes", "dashboard"),
        ("/api/connections", "dashboard"),
        ("/api/alerts", "dashboard"),
    )
    for prefix, section in prefix_rules:
        if path == prefix or path.startswith(prefix + "/"):
            return section, "view" if read else "manage"
    if path.startswith("/api/"):
        return "settings", "manage"
    return None

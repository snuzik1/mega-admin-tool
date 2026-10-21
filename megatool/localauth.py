"""Проверка локальных учётных записей ОС для входа в MegaTool.

MegaTool не проверяет системный пароль: он лишь убеждается, что локальная
учётная запись существует и имеет права root/sudo (POSIX) либо входит в группу
администраторов (Windows). Пароль MegaTool хранится отдельно в access.json.

На POSIX root определяется по uid 0 или членству в группах root/sudo/wheel/admin.
На Windows используется встроенная группа S-1-5-32-544 (Administrators):
членство проверяется даже тогда, когда сам процесс запущен без повышения прав.
"""

from __future__ import annotations

import getpass
import os
import re
import subprocess
import threading
import time
from typing import Any

_CACHE_TTL = 30.0
_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, dict[str, Any] | None]] = {}

_NAME_RE = re.compile(r"^[^\x00-\x1f]{1,64}$")
_PRIV_GROUPS_POSIX = {"root", "sudo", "wheel", "admin", "adm"}


def clean_name(value: Any) -> str:
    """Убирает домен и управляющие символы из имени локальной учётной записи."""
    text = str(value or "").strip()
    if "\\" in text:
        text = text.rsplit("\\", 1)[-1]
    if "@" in text and os.name == "nt":
        text = text.split("@", 1)[0]
    if not _NAME_RE.match(text):
        return ""
    return text[:64]


def normalize_name(value: Any) -> str:
    return clean_name(value).casefold()


def _cached(username: str) -> tuple[bool, dict[str, Any] | None]:
    now = time.monotonic()
    with _CACHE_LOCK:
        found = _CACHE.get(username)
        if found and now - found[0] < _CACHE_TTL:
            return True, found[1]
    return False, None


def _remember(username: str, info: dict[str, Any] | None) -> dict[str, Any] | None:
    with _CACHE_LOCK:
        _CACHE[username] = (time.monotonic(), info)
    return info


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def _posix_info(name: str) -> dict[str, Any] | None:
    try:
        import grp
        import pwd
    except ImportError:  # pragma: no cover - Windows
        return None
    try:
        entry = pwd.getpwnam(name)
    except KeyError:
        return None
    groups: list[str] = []
    try:
        for gid in os.getgrouplist(name, entry.pw_gid):
            try:
                groups.append(grp.getgrgid(gid).gr_name.casefold())
            except KeyError:
                continue
    except (AttributeError, OSError):
        try:
            groups.append(grp.getgrgid(entry.pw_gid).gr_name.casefold())
        except KeyError:
            pass
    privileged = entry.pw_uid == 0 or bool(_PRIV_GROUPS_POSIX.intersection(groups))
    role = "root" if entry.pw_uid == 0 else next(
        (group for group in ("sudo", "wheel", "admin", "adm") if group in groups), "")
    return {
        "username": entry.pw_name,
        "uid": entry.pw_uid,
        "privileged": privileged,
        "role": role,
        "groups": groups,
    }


def _run_powershell(script: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, errors="replace", timeout=6,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _windows_exists(name: str) -> bool:
    try:
        result = subprocess.run(
            ["net", "user", name],
            capture_output=True, text=True, errors="replace", timeout=6,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _windows_admin_names() -> set[str] | None:
    result = _run_powershell(
        "Get-LocalGroupMember -SID S-1-5-32-544 | "
        "Select-Object -ExpandProperty Name"
    )
    if result is None or result.returncode != 0:
        return None
    names: set[str] = set()
    for line in result.stdout.splitlines():
        item = clean_name(line)
        if item:
            names.add(item.casefold())
    return names


def current_name() -> str:
    if os.name != "nt":
        try:
            import pwd
            return pwd.getpwuid(os.geteuid()).pw_name
        except (ImportError, KeyError, OSError):
            pass
    return clean_name(os.environ.get("USERNAME") or os.environ.get("USER")
                       or getpass.getuser())


def inspect_account(username: str) -> dict[str, Any] | None:
    """Возвращает сведения о локальной учётной записи или None, если её нет."""
    name = clean_name(username)
    if not name:
        return None
    cached, info = _cached(name.casefold())
    if cached:
        return info
    if os.name == "nt":
        if not _windows_exists(name):
            return _remember(name.casefold(), None)
        admins = _windows_admin_names()
        if admins is None:
            # Не выдаём права при сбое проверки группы: лучше отказать в setup.
            return _remember(name.casefold(), {
                "username": name, "privileged": False, "role": "",
                "error": "admin-check-failed",
            })
        privileged = name.casefold() in admins
        info = {
            "username": name,
            "privileged": privileged,
            "role": "administrator" if privileged else "",
        }
    else:
        info = _posix_info(name)
    return _remember(name.casefold(), info)


def current_account() -> dict[str, Any] | None:
    return inspect_account(current_name())


def is_privileged(info: dict[str, Any] | None) -> bool:
    return bool(info and info.get("privileged"))


def display_role(info: dict[str, Any] | None) -> str:
    role = str((info or {}).get("role") or "")
    if role:
        return role
    return "user"

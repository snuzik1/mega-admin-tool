"""«Что изменилось на сервере»: файлы, пакеты, входы, сервисы, ошибки в журналах.

Каждый источник опрашивается осторожно и независимо: недоступность одного
(нет systemd, нет прав, нет журнала) не ломает остальные.
"""

from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .config import AUDIT_LOG, Config, default_watch_paths
from .i18n import t
from .util import append_jsonl, iso, read_jsonl, run

#: Каталоги, которые никогда не сканируем (шум и огромный объём).
SKIP_DIRS = {
    "proc", "sys", "dev", "run", "node_modules", ".git", "__pycache__", ".venv",
    "venv", "site-packages", "Cache", "cache", "tmp",
}

_CACHE: Dict[str, Any] = {"ts": 0.0, "key": "", "data": None}
_CACHE_TTL = 45.0
_CACHE_LOCK = threading.Lock()


def reset_cache() -> None:
    """Сбрасывает кэш отчёта (например, при смене языка интерфейса)."""
    with _CACHE_LOCK:
        _CACHE.update({"ts": 0.0, "key": "", "data": None})

#: Параметры ротации журнала действий: предел размера (МБ) и число архивов.
_AUDIT_MAX_MB = 5.0
_AUDIT_KEEP = 3


def collect(cfg: Config, hours: float = 24.0, file_limit: int = 150) -> Dict[str, Any]:
    """Собирает отчёт об изменениях за последние `hours` часов."""
    key = f"{hours}:{file_limit}:{','.join(cfg.get('monitor.watch_paths', []) or [])}"
    with _CACHE_LOCK:
        if _CACHE["data"] and _CACHE["key"] == key and time.time() - _CACHE["ts"] < _CACHE_TTL:
            return _CACHE["data"]

    data = {
        "generated_at": iso(),
        "hours": hours,
        "sections": [],
    }
    data["sections"].append(_files_section(cfg, hours, file_limit))
    data["sections"].append(_packages_section(hours))
    data["sections"].append(_logins_section())
    data["sections"].append(_services_section())
    data["sections"].append(_logs_section(hours))
    data["sections"].append(_audit_section(hours))
    data["summary"] = {
        section["id"]: {"count": len(section.get("items", [])), "available": section.get("available", True)}
        for section in data["sections"]
    }
    with _CACHE_LOCK:
        _CACHE.update({"ts": time.time(), "key": key, "data": data})
    return data


# --------------------------------------------------------------------------- #
#  Файлы
# --------------------------------------------------------------------------- #

def _files_section(cfg: Config, hours: float, limit: int) -> Dict[str, Any]:
    roots = [p for p in (cfg.get("monitor.watch_paths") or []) if p]
    if not roots:
        roots = default_watch_paths()
    cutoff = time.time() - hours * 3600
    found: List[Dict[str, Any]] = []
    scanned = 0
    for root in roots[:8]:
        base = Path(root)
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            scanned += 1
            if scanned > 4000:
                break
            depth = len(Path(dirpath).parts) - len(base.parts)
            if depth >= 3:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith("$")]
            for name in filenames:
                if len(found) >= limit * 3:
                    break
                path = Path(dirpath) / name
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if stat.st_mtime >= cutoff:
                    found.append({
                        "time": iso(stat.st_mtime),
                        "ts": stat.st_mtime,
                        "path": str(path),
                        "size": stat.st_size,
                        "root": root,
                    })
    found.sort(key=lambda item: item["ts"], reverse=True)
    return {
        "id": "files",
        "title": t("changes.files"),
        "hint": t("changes.filesHint"),
        "available": True,
        "items": found[:limit],
        "scanned_dirs": scanned,
    }


# --------------------------------------------------------------------------- #
#  Пакеты и обновления
# --------------------------------------------------------------------------- #

_DPKG_RE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (?P<action>\w+) (?P<pkg>[^ ]+) (?P<ver>[^ ]+)")


def _packages_section(hours: float) -> Dict[str, Any]:
    cutoff = time.time() - hours * 3600
    source, items = _packages_linux(cutoff)

    items.sort(key=lambda i: i.get("ts", 0), reverse=True)
    return {
        "id": "packages",
        "title": t("changes.packages"),
        "hint": t("changes.hintSource", source=source or t("changes.sourceUndefined")),
        "available": bool(items) or bool(source),
        "items": items[:100],
    }


def _packages_linux(cutoff: float) -> Tuple[str, List[Dict[str, Any]]]:
    items: List[Dict[str, Any]] = []
    dpkg_log = Path("/var/log/dpkg.log")
    apt_history = Path("/var/log/apt/history.log")
    pacman_log = Path("/var/log/pacman.log")
    dnf_log = Path("/var/log/dnf.log")

    if dpkg_log.is_file():
        for line in _tail_lines_safe(dpkg_log, 4000):
            match = _DPKG_RE.match(line)
            if not match:
                continue
            ts = _parse_local_ts(match.group("ts"))
            if ts and ts >= cutoff:
                items.append({
                    "ts": ts, "time": iso(ts), "kind": match.group("action"),
                    "title": match.group("pkg"), "detail": match.group("ver"),
                })
        return "dpkg /var/log/dpkg.log", items

    if apt_history.is_file():
        text = _read_safe(apt_history)
        for block in text.split("\n\n"):
            start = re.search(r"Start-Date: (?P<ts>[^\n]+)", block)
            if not start:
                continue
            ts = _parse_local_ts(start.group("ts").strip())
            if not ts or ts < cutoff:
                continue
            command = re.search(r"Commandline: (?P<cmd>[^\n]+)", block)
            install = re.search(r"Install: (?P<pkgs>[^\n]+)", block)
            upgrade = re.search(r"Upgrade: (?P<pkgs>[^\n]+)", block)
            remove = re.search(r"Remove: (?P<pkgs>[^\n]+)", block)
            what = install or upgrade or remove
            items.append({
                "ts": ts, "time": iso(ts),
                "kind": "apt",
                "title": (what.group("pkgs")[:160] if what else t("changes.aptOperation")),
                "detail": (command.group("cmd")[:200] if command else ""),
            })
        return "apt /var/log/apt/history.log", items

    if pacman_log.is_file():
        for line in _tail_lines_safe(pacman_log, 3000):
            match = re.match(r"^\[(?P<ts>[^\]]+)\] \[ALPM\] (?P<action>\w+) (?P<pkg>[^ ]+) \((?P<ver>[^)]+)\)", line)
            if not match:
                continue
            ts = _parse_local_ts(match.group("ts")[:19].replace("T", " "))
            if ts and ts >= cutoff:
                items.append({
                    "ts": ts, "time": iso(ts), "kind": match.group("action"),
                    "title": match.group("pkg"), "detail": match.group("ver"),
                })
        return "pacman /var/log/pacman.log", items

    if dnf_log.is_file():
        for line in _tail_lines_safe(dnf_log, 3000):
            match = re.match(r"^(?P<ts>\d{4}-\d{2}-\d{2}T[\d:]+).*(Installed|Upgraded|Removed): (?P<pkg>[^ ]+)", line)
            if match:
                ts = _parse_local_ts(match.group("ts")[:19].replace("T", " "))
                if ts and ts >= cutoff:
                    items.append({
                        "ts": ts, "time": iso(ts), "kind": match.group(2),
                        "title": match.group("pkg"), "detail": "",
                    })
        if items:
            return "dnf /var/log/dnf.log", items

    res = run(["rpm", "-qa", "--last", "--qf", "%{INSTALLTIME}|%{NAME}-%{VERSION}\n"], timeout=20)
    if res.ok:
        for line in res.lines(150):
            stamp, _, name = line.partition("|")
            try:
                ts = float(stamp)
            except ValueError:
                continue
            if ts >= cutoff:
                items.append({"ts": ts, "time": iso(ts), "kind": "rpm", "title": name, "detail": ""})
        return "rpm --last", items

    return "", items


# --------------------------------------------------------------------------- #
#  Входы в систему
# --------------------------------------------------------------------------- #

def _logins_section() -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    source = ""
    res = run(["last", "-n", "30", "-w"], timeout=10)
    if res.ok:
        source = "last"
        for line in res.lines(30):
            parts = line.split()
            if len(parts) < 4 or line.startswith(("wtmp", "reboot", "shutdown")):
                continue
            user = parts[0]
            if user in ("reboot", "shutdown", "wtmp"):
                continue
            items.append({
                "time": " ".join(parts[-5:-2]) if len(parts) > 6 else " ".join(parts[3:6]),
                "title": user,
                "detail": f"{parts[1] if len(parts) > 1 else ''} {parts[2] if len(parts) > 2 else ''}".strip(),
                "kind": "login",
            })
    failed = run(["lastb", "-n", "10"], timeout=10)
    if failed.ok and failed.out.strip():
        for line in failed.lines(10):
            parts = line.split()
            if len(parts) >= 3 and parts[0] not in ("btmp",):
                items.append({
                    "time": " ".join(parts[-5:-2]),
                    "title": t("changes.failedLogin", user=parts[0]),
                    "detail": parts[2] if len(parts) > 2 else "",
                    "kind": "login-failed",
                })
        source = f"{source} + lastb" if source else "lastb"
    return {
        "id": "logins",
        "title": t("changes.logins"),
        "hint": t("changes.hintSource", source=source or t("changes.sourceUnavailable")),
        "available": bool(source),
        "items": items[:40],
    }


# --------------------------------------------------------------------------- #
#  Службы
# --------------------------------------------------------------------------- #

def _services_section() -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    source = ""
    if Path("/run/systemd/system").is_dir():
        res = run(["systemctl", "list-units", "--state=failed", "--no-pager", "--plain", "--no-legend"],
                  timeout=15)
        source = "systemctl --failed"
        if res.ok:
            for line in res.lines(50):
                parts = line.split(None, 4)
                if len(parts) >= 4:
                    items.append({
                        "time": "",
                        "title": parts[0],
                        "detail": f"{parts[2]} — {parts[4] if len(parts) > 4 else parts[3]}",
                        "kind": "failed-unit",
                    })
    return {
        "id": "services",
        "title": t("changes.services"),
        "hint": t("changes.hintSource", source=source or t("changes.sourceUnavailable")),
        "available": bool(source),
        "items": items[:50],
    }


# --------------------------------------------------------------------------- #
#  Журналы
# --------------------------------------------------------------------------- #

def _logs_section(hours: float) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    source = ""
    since = max(1, int(hours))
    res = run(["journalctl", "-p", "err", "--no-pager", "-o", "short-iso",
               "-n", "60", "--since", f"-{since}h"], timeout=20)
    if res.ok and res.out.strip():
        source = "journalctl -p err"
        for line in res.lines(60):
            stamp, _, rest = line.partition(" ")
            items.append({"time": stamp, "ts": _parse_iso_like(stamp) or 0,
                          "title": rest[:200], "detail": "", "kind": "log-error"})
    else:
        for path in ("/var/log/syslog", "/var/log/messages", "/var/log/kern.log"):
            if Path(path).is_file():
                source = path
                for line in _tail_lines_safe(Path(path), 2000):
                    if re.search(r"\b(error|crit|panic|fail)", line, re.I):
                        items.append({"time": line[:15], "title": line[16:220], "detail": "", "kind": "log-error"})
                break
    items.sort(key=lambda i: i.get("ts", 0), reverse=True)
    return {
        "id": "logs",
        "title": t("changes.logs"),
        "hint": t("changes.hintSource", source=source or t("changes.sourceUnavailable")),
        "available": bool(source),
        "items": items[:60],
    }


# --------------------------------------------------------------------------- #
#  Аудит действий в самой MegaTool
# --------------------------------------------------------------------------- #

def _audit_section(hours: float) -> Dict[str, Any]:
    cutoff = time.time() - hours * 3600
    entries = read_jsonl(AUDIT_LOG, limit=1000)
    items = []
    for entry in reversed(entries):
        ts = entry.get("ts_value")
        if ts and ts < cutoff:
            continue
        items.append({
            "time": entry.get("ts", ""),
            "ts": ts or 0,
            "kind": entry.get("action", "action"),
            "title": f"{entry.get('action', '')} — {entry.get('target', '')}".strip(" —"),
            "detail": entry.get("detail", ""),
        })
    return {
        "id": "audit",
        "title": t("changes.audit"),
        "hint": t("changes.hintAuditLog", path=AUDIT_LOG),
        "available": True,
        "items": items[:100],
    }


def configure_audit(max_mb: float, keep: int) -> None:
    """Задаёт лимит размера журнала действий (МБ) и число хранимых архивов."""
    global _AUDIT_MAX_MB, _AUDIT_KEEP
    _AUDIT_MAX_MB = float(max_mb)
    _AUDIT_KEEP = max(0, int(keep))


def _rotate_audit() -> None:
    """Сдвигает архивные копии журнала, если текущий файл перерос лимит.

    Схема: audit.jsonl → .1, .1 → .2 и так далее; архив с номером keep
    (самый старый) и любые «хвосты» сверх лимита удаляются. Ошибки файловой
    системы игнорируются: журнал не должен ломать основную операцию.
    """
    try:
        path = Path(AUDIT_LOG)
        if not path.is_file():
            return
        if path.stat().st_size <= _AUDIT_MAX_MB * 1024 * 1024:
            return
        if _AUDIT_KEEP <= 0:
            path.unlink()
            return
        prefix = path.name + "."
        # Убираем самый старый архив и возможные лишние копии сверх лимита.
        for archive in path.parent.glob(prefix + "*"):
            suffix = archive.name[len(prefix):]
            if suffix.isdigit() and int(suffix) >= _AUDIT_KEEP:
                archive.unlink()
        for index in range(_AUDIT_KEEP - 1, 0, -1):
            source = Path(f"{AUDIT_LOG}.{index}")
            if source.exists():
                os.replace(source, Path(f"{AUDIT_LOG}.{index + 1}"))
        os.replace(path, Path(f"{AUDIT_LOG}.1"))
    except OSError:
        pass


def audit(action: str, target: str = "", detail: str = "", ok: bool = True, extra: Optional[Dict[str, Any]] = None) -> None:
    """Записывает действие пользователя в журнал аудита."""
    payload = {
        "ts": iso(),
        "ts_value": time.time(),
        "action": action,
        "target": target,
        "detail": detail,
        "ok": ok,
    }
    if extra:
        payload.update(extra)
    try:
        _rotate_audit()
        append_jsonl(AUDIT_LOG, payload)
    except OSError:
        pass


def read_audit(limit: int = 200) -> List[Dict[str, Any]]:
    return list(reversed(read_jsonl(AUDIT_LOG, limit=limit)))


# --------------------------------------------------------------------------- #
#  Помощники
# --------------------------------------------------------------------------- #

def _read_safe(path: Path, max_bytes: int = 1_000_000) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    return data[-max_bytes:].decode("utf-8", errors="replace")


def _tail_lines_safe(path: Path, count: int) -> List[str]:
    return _read_safe(path).splitlines()[-count:]


def _parse_local_ts(text: str) -> Optional[float]:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return time.mktime(time.strptime(text.strip(), fmt))
        except ValueError:
            continue
    return _parse_iso_like(text)


def _parse_iso_like(text: str) -> Optional[float]:
    if not text:
        return None
    text = str(text).strip().replace("Z", "+00:00")
    try:
        from datetime import datetime

        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S"):
        try:
            from datetime import datetime

            return datetime.strptime(text[:19], fmt).timestamp()
        except ValueError:
            continue
    return None

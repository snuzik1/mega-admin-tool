"""Работа с контейнерами: Docker и Podman через CLI, без внешних Python-SDK.

Такой подход даёт два плюса: приложение не требует докер-библиотек, а поведение
одинаково и для `docker`, и для `podman` (включая rootless). Если движка нет —
модуль честно сообщает об этом, и остальное приложение продолжает работать.

Все деструктивные операции ограничены: идентификатор контейнера проверяется
регуляркой, а compose-файлы должны лежать внутри рабочего каталога MegaTool.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .config import APPS_DIR, CONFIGS_DIR, HOME_DIR, Config
from .i18n import t
from .util import (
    CmdResult,
    human_bytes,
    is_within,
    iso,
    json_any,
    json_lines,
    run,
    safe_id,
    which,
)

COMPOSE_FILENAMES = (
    "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml",
    "podman-compose.yml", "podman-compose.yaml",
)
ACTIONS = {"start", "stop", "restart", "pause", "unpause", "kill", "rm", "rename"}
COMPOSE_ACTIONS = {"up", "down", "restart", "stop", "start", "ps", "logs", "pull", "build", "config", "top"}
_PCT_RE = re.compile(r"([\d.]+)\s*%")
_MEM_RE = re.compile(r"([\d.]+)\s*([KMGTP]?i?B)")


# --------------------------------------------------------------------------- #
#  Разбор строк вида "12.3MiB / 1.5GiB", "0.15%", "0.0.0.0:8080->80/tcp"
# --------------------------------------------------------------------------- #

def _get(obj: Dict[str, Any], *names: str, default: Any = None) -> Any:
    """Регистронезависимый доступ к ключу (docker и podman называют поля по-разному)."""
    if not isinstance(obj, dict):
        return default
    lower = {str(k).lower(): v for k, v in obj.items()}
    for name in names:
        if name.lower() in lower:
            return lower[name.lower()]
    return default


def parse_percent(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return round(float(value), 1)
    match = _PCT_RE.search(str(value))
    return round(float(match.group(1)), 1) if match else 0.0


def parse_bytes_pair(value: Any) -> Tuple[float, float]:
    """«12.3MiB / 1.5GiB» -> (12.3 МБ, 1.5 ГБ)."""
    if not value:
        return (0.0, 0.0)
    if isinstance(value, (int, float)):
        return (float(value), 0.0)
    parts = [p.strip() for p in str(value).split("/")] if "/" in str(value) else [str(value)]
    out: List[float] = []
    for part in parts[:2]:
        match = _MEM_RE.search(part)
        if not match:
            out.append(0.0)
            continue
        number = float(match.group(1))
        unit = match.group(2).lower().replace("ib", "b").upper().rstrip("B")
        factor = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4, "P": 1024 ** 5}.get(unit, 1)
        out.append(number * factor)
    while len(out) < 2:
        out.append(0.0)
    return (out[0], out[1])


def parse_ports(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, list):
        result = []
        for item in value:
            if isinstance(item, dict):
                host = item.get("host_port") or item.get("hostPort")
                container = item.get("container_port") or item.get("containerPort")
                proto = item.get("protocol", "tcp")
                ip = item.get("host_ip") or item.get("hostIP") or "0.0.0.0"
                if host:
                    result.append(f"{ip}:{host}->{container}/{proto}")
                elif container:
                    result.append(f"{container}/{proto}")
            else:
                result.append(str(item))
        return result
    return [p.strip() for p in str(value).split(",") if p.strip()]


def parse_created(running_for: Optional[str], created_at: Optional[str], created: Any = None) -> Optional[float]:
    """Пытается получить timestamp создания из "RunningFor"/"Created". """
    if isinstance(created, (int, float)) and created > 1_000_000_000:
        return float(created)
    for text in (created_at, running_for):
        if not text:
            continue
        cleaned = re.sub(r"\s*windows?\s*.*", "", str(text), flags=re.I).strip()
        try:
            from datetime import datetime, timedelta

            cleaned = cleaned.split("+")[0].split(".")[0].strip()
            dt = datetime.strptime(cleaned, "%Y-%m-%d %H:%M:%S")
            return dt.timestamp()
        except ValueError:
            continue
    return None


def human_uptime(text: Optional[str]) -> str:
    return str(text or "").replace("About ", "").replace(" ago", "")


# --------------------------------------------------------------------------- #
#  Один движок (docker или podman)
# --------------------------------------------------------------------------- #

@dataclass
class EngineInfo:
    kind: str
    binary: Optional[str] = None
    version: str = ""
    server_version: str = ""
    available: bool = False
    daemon_ok: bool = False
    error: str = ""
    compose: str = ""
    checked_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "binary": self.binary,
            "version": self.version,
            "server_version": self.server_version,
            "available": self.available,
            "daemon_ok": self.daemon_ok,
            "error": self.error,
            "compose": self.compose,
            "checked": iso(self.checked_at) if self.checked_at else None,
        }


class ContainerEngine:
    def __init__(self, kind: str, cfg: Config) -> None:
        self.kind = kind
        self.cfg = cfg
        self._info: Optional[EngineInfo] = None
        self._lock = threading.RLock()
        self._compose_cmd: Optional[List[str]] = None

    # ------------------------------------------------------------------ #
    @property
    def binary(self) -> Optional[str]:
        return which(self.kind, self.cfg.get(f"engines.{self.kind}", "") or None)

    def info(self, refresh: bool = False) -> EngineInfo:
        with self._lock:
            if self._info and not refresh and time.time() - self._info.checked_at < 60:
                return self._info
            info = EngineInfo(kind=self.kind, checked_at=time.time())
            binary = self.binary
            info.binary = binary
            if not binary:
                info.error = t("containers.notFound", engine=self.kind)
                self._info = info
                return info
            ver = run([binary, "--version"], timeout=15)
            if ver.ok:
                info.version = ver.out.strip() or ver.err.strip()
            info.available = ver.ok
            server = run([binary, "version", "--format", "{{.Server.Version}}"], timeout=15)
            if server.ok and server.out.strip():
                info.server_version = server.out.strip()
                info.daemon_ok = True
            else:
                info.daemon_ok = False
                info.error = (server.err or "").strip()[:300] or t("containers.daemonSilent")
            if info.available:
                info.compose = " ".join(self.compose_argv() or [])
            self._info = info
            return info

    def require(self) -> Tuple[bool, str]:
        info = self.info()
        if not info.available:
            return False, t("containers.unavailable", engine=self.kind,
                            reason=info.error or t("containers.notFoundShort"))
        if not info.daemon_ok:
            return False, t("containers.daemonDown", engine=self.kind, error=info.error)
        return True, ""

    # ------------------------------------------------------------------ #
    #  Контейнеры
    # ------------------------------------------------------------------ #
    def list(self, all_containers: bool = True) -> Tuple[List[Dict[str, Any]], str]:
        info = self.info()
        if not info.available or not info.binary:
            return [], info.error or t("containers.engineDown", engine=self.kind)
        args = [info.binary, "ps", "--format", "{{json .}}"]
        if all_containers:
            args.insert(2, "--all")
        res = run(args, timeout=25)
        if not res.ok:
            return [], (res.err or res.error or t("containers.listFailed")).strip()[:400]
        if self.kind == "podman":
            data = json_any(res.out)
            raw = data if isinstance(data, list) else json_lines(res.out)
        else:
            raw = json_lines(res.out)
        return [self._normalize_container(item) for item in raw], ""

    def _normalize_container(self, item: Dict[str, Any]) -> Dict[str, Any]:
        labels = _get(item, "Labels", default={}) or {}
        if isinstance(labels, str):
            parsed: Dict[str, str] = {}
            for pair in labels.split(","):
                key, _, value = pair.partition("=")
                if key.strip():
                    parsed[key.strip()] = value.strip()
            labels = parsed
        state = str(_get(item, "State", default="")).lower()
        status = str(_get(item, "Status", default=""))
        names = _get(item, "Names", "Name", default=[])
        if isinstance(names, list):
            name = names[0] if names else ""
        else:
            name = str(names or "")
        health = ""
        for marker in ("healthy", "unhealthy", "starting"):
            if marker in status.lower():
                health = marker
                break
        ports = parse_ports(_get(item, "Ports", default=""))
        project = labels.get("com.docker.compose.project") or labels.get("io.podman.compose.project") or ""
        service = labels.get("com.docker.compose.service") or labels.get("io.podman.compose.service") or ""
        created = parse_created(_get(item, "RunningFor"), _get(item, "CreatedAt"), _get(item, "Created"))
        return {
            "id": str(_get(item, "ID", "Id", "ContainerID", default="")),
            "short_id": str(_get(item, "ID", "Id", "ContainerID", default=""))[:12],
            "name": name.lstrip("/"),
            "image": str(_get(item, "Image", default="")),
            "command": str(_get(item, "Command", "Cmd", default=""))[:200],
            "state": state,
            "status": status,
            "health": health,
            "ports": ports,
            "created": created,
            "created_iso": iso(created) if created else None,
            "uptime": human_uptime(_get(item, "RunningFor") or status),
            "size": str(_get(item, "Size", default="")),
            "labels": labels,
            "project": project,
            "service": service,
            "engine": self.kind,
            "exit_code": item.get("ExitCode"),
        }

    def stats(self, all_containers: bool = True) -> Tuple[Dict[str, Dict[str, Any]], str]:
        info = self.info()
        if not info.available or not info.binary:
            return {}, info.error or t("containers.engineDown", engine=self.kind)
        args = [info.binary, "stats", "--no-stream", "--format", "{{json .}}"]
        if all_containers and self.kind == "docker":
            args.insert(2, "--all")
        res = run(args, timeout=40)
        if not res.ok:
            return {}, (res.err or res.error or t("containers.statsFailed")).strip()[:400]
        if self.kind == "podman":
            data = json_any(res.out)
            raw = data if isinstance(data, list) else json_lines(res.out)
        else:
            raw = json_lines(res.out)
        out: Dict[str, Dict[str, Any]] = {}
        for item in raw:
            cid = str(_get(item, "Container", "ID", "Id", default=""))
            used, limit = parse_bytes_pair(_get(item, "MemUsage", "MemUsageBytes", "mem_usage", default=""))
            name = str(_get(item, "Name", "name", default=""))
            entry = {
                "id": cid,
                "name": name,
                "cpu_pct": parse_percent(_get(item, "CPUPerc", "CPU", "cpu_percent", default=0)),
                "mem_pct": parse_percent(_get(item, "MemPerc", "mem_perc", default=0)),
                "mem_used": used,
                "mem_limit": limit,
                "mem_used_human": human_bytes(used),
                "net_io": str(_get(item, "NetIO", default="")),
                "block_io": str(_get(item, "BlockIO", default="")),
                "pids": _get(item, "PIDs", "pids", default=0),
            }
            out[cid] = entry
            if name:
                out[name] = entry
        return out, ""

    def inspect(self, cid: str) -> Dict[str, Any]:
        ok, err = self.require()
        cid = safe_id(cid)
        if not ok or not cid:
            return {"ok": False, "error": err or t("containers.badId")}
        res = run([self.binary or self.kind, "inspect", cid], timeout=25)
        if not res.ok:
            return {"ok": False, "error": (res.err or res.error or t("containers.inspectFailed")).strip()[:400]}
        data = json_any(res.out)
        return {"ok": True, "data": data}

    def logs(self, cid: str, tail: int = 200, timestamps: bool = False) -> Dict[str, Any]:
        ok, err = self.require()
        cid = safe_id(cid)
        if not ok or not cid:
            return {"ok": False, "error": err or t("containers.badId")}
        args = [self.binary or self.kind, "logs", "--tail", str(max(1, min(5000, tail)))]
        if timestamps:
            args.append("--timestamps")
        args.append(cid)
        res = run(args, timeout=25)
        text = (res.out or "") + (("\n" + res.err) if res.err else "")
        return {"ok": res.ok or bool(text.strip()), "text": text, "lines": text.splitlines()[-5000:],
                "error": "" if res.ok else (res.error or "") }

    def action(self, cid: str, op: str, new_name: str = "") -> Dict[str, Any]:
        ok, err = self.require()
        cid = safe_id(cid)
        if op not in ACTIONS:
            return {"ok": False, "error": t("containers.opNotSupported", op=op)}
        if not ok or not cid:
            return {"ok": False, "error": err or t("containers.badId")}
        if not self.cfg.get("safety.allow_container_actions", True):
            return {"ok": False, "error": t("containers.actionsDisabled")}
        args = [self.binary or self.kind, op]
        if op == "restart":
            args += ["--time", "10"] if self.kind == "docker" else ["--time", "10"]
        if op == "rm":
            args.append("--force")
        if op == "rename":
            target = safe_id(new_name)
            if not target:
                return {"ok": False, "error": t("containers.badName")}
            args.append(cid)
            args.append(target)
        else:
            args.append(cid)
        res = run(args, timeout=90)
        return {
            "ok": res.ok,
            "out": (res.out or "").strip(),
            "error": "" if res.ok else (res.err or res.error or t("containers.opFailed")).strip()[:400],
        }

    # ------------------------------------------------------------------ #
    #  Ресурсы движка
    # ------------------------------------------------------------------ #
    def df(self) -> Dict[str, Any]:
        info = self.info()
        if not info.available or not info.binary:
            return {"ok": False, "error": info.error or t("containers.engineUnavailable"), "items": []}
        if self.kind == "docker":
            res = run([info.binary, "system", "df", "--format", "{{json .}}"], timeout=30)
            items = json_lines(res.out) if res.ok else []
        else:
            res = run([info.binary, "system", "df", "--format", "json"], timeout=30)
            data = json_any(res.out)
            items = data if isinstance(data, list) else []
        normalized = []
        for item in items:
            normalized.append({
                "type": _get(item, "Type", "type", default="?"),
                "total": _get(item, "TotalCount", "total", default=0),
                "active": _get(item, "Active", "active", default=0),
                "size": _get(item, "Size", "size", default=""),
                "reclaimable": _get(item, "Reclaimable", "reclaimable", default=""),
            })
        return {"ok": True, "items": normalized, "error": "" if res.ok else (res.error or "")}

    def images(self, limit: int = 60) -> List[Dict[str, Any]]:
        info = self.info()
        if not info.available or not info.binary:
            return []
        res = run([info.binary, "images", "--format", "{{json .}}"], timeout=30)
        items = json_lines(res.out) if self.kind == "docker" else (json_any(res.out) or [])
        out = []
        for item in (items if isinstance(items, list) else []):
            out.append({
                "id": str(_get(item, "ID", "Id", default=""))[:20],
                "repository": str(_get(item, "Repository", "repository", default="")),
                "tag": str(_get(item, "Tag", "tag", default="")),
                "size": str(_get(item, "Size", "size", default="")),
                "created": str(_get(item, "CreatedSince", "Created", "created", default="")),
            })
        return out[:limit]

    def volumes(self, limit: int = 60) -> List[Dict[str, Any]]:
        info = self.info()
        if not info.available or not info.binary:
            return []
        res = run([info.binary, "volume", "ls", "--format", "{{json .}}"], timeout=30)
        items = json_lines(res.out) if self.kind == "docker" else (json_any(res.out) or [])
        out = []
        for item in (items if isinstance(items, list) else []):
            out.append({
                "name": str(_get(item, "Name", "name", default="")),
                "driver": str(_get(item, "Driver", "driver", default="")),
                "mountpoint": str(_get(item, "Mountpoint", "mountpoint", default="")),
            })
        return sorted(out, key=lambda v: v["name"])[:limit]

    def networks(self, limit: int = 60) -> List[Dict[str, Any]]:
        info = self.info()
        if not info.available or not info.binary:
            return []
        res = run([info.binary, "network", "ls", "--format", "{{json .}}"], timeout=30)
        items = json_lines(res.out) if self.kind == "docker" else (json_any(res.out) or [])
        out = []
        for item in (items if isinstance(items, list) else []):
            out.append({
                "id": str(_get(item, "ID", "Id", default=""))[:12],
                "name": str(_get(item, "Name", "name", default="")),
                "driver": str(_get(item, "Driver", "driver", default="")),
                "scope": str(_get(item, "Scope", "scope", default="")),
            })
        return out[:limit]

    # ------------------------------------------------------------------ #
    #  Compose
    # ------------------------------------------------------------------ #
    def compose_argv(self) -> Optional[List[str]]:
        """Определяет доступную команду compose для этого движка."""
        if self._compose_cmd is not None:
            return self._compose_cmd
        binary = self.binary
        if not binary:
            return None
        if self.kind == "docker":
            for candidate in ([binary, "compose"], ["docker-compose"]):
                probe = run([*candidate, "version"], timeout=20)
                if probe.ok:
                    self._compose_cmd = candidate
                    return candidate
            return None
        for candidate in (["podman-compose"], [binary, "compose"]):
            probe = run([*candidate, "version"], timeout=20)
            if probe.ok:
                self._compose_cmd = candidate
                return candidate
        return None

    def compose_files_in(self, directory: Path) -> List[Path]:
        return [directory / name for name in COMPOSE_FILENAMES if (directory / name).is_file()]

    def find_projects(self, roots: Optional[Iterable[Path]] = None) -> List[Dict[str, Any]]:
        """Находит compose-проекты в рабочем каталоге приложения."""
        roots = list(roots or [APPS_DIR, CONFIGS_DIR])
        projects: List[Dict[str, Any]] = []
        for root in roots:
            if not root.is_dir():
                continue
            for directory in [root, *[p for p in root.iterdir() if p.is_dir()]]:
                files = self.compose_files_in(directory)
                if not files:
                    continue
                projects.append({
                    "name": directory.name,
                    "dir": str(directory),
                    "files": [str(f) for f in files],
                    "engine": self.kind,
                })
        return sorted(projects, key=lambda p: p["name"])

    def _compose_guard(self, files: List[Path], cwd: Path) -> Optional[str]:
        allowed_roots = [HOME_DIR.resolve(), CONFIGS_DIR.resolve(), APPS_DIR.resolve()]
        for path in [*files, cwd]:
            resolved = Path(path).resolve()
            if not any(is_within(resolved, root) for root in allowed_roots):
                return t("containers.pathOutside", path=resolved)
        for path in files:
            if path.name not in COMPOSE_FILENAMES and path.suffix not in (".yml", ".yaml"):
                return t("containers.composeExpected", name=path.name)
        return None

    def compose_command(self, directory: Path, op: str, service: str = "",
                        files: Optional[List[Path]] = None,
                        extra_args: Optional[List[str]] = None) -> Tuple[Optional[List[str]], str]:
        """Собирает argv для compose-операции (используется и для фоновых задач)."""
        argv = self.compose_argv()
        if not argv:
            return None, t("containers.composeMissing", engine=self.kind)
        if op not in COMPOSE_ACTIONS:
            return None, t("containers.opNotSupported", op=op)
        files = files or self.compose_files_in(directory)
        if not files:
            return None, t("containers.noComposeFile")
        guard = self._compose_guard(files, directory)
        if guard:
            return None, guard
        cmd = list(argv)
        for path in files:
            cmd += ["-f", str(path)]
        cmd.append(op)
        if service:
            safe_service = safe_id(service)
            if not safe_service:
                return None, t("containers.badService")
            cmd.append(safe_service)
        if op == "up":
            cmd += ["-d", "--remove-orphans"]
        if op == "logs":
            cmd += ["--tail", "200"]
        if extra_args:
            allowed_flags = {"--build", "--force-recreate", "--pull", "--no-deps"}
            cmd += [a for a in extra_args
                    if isinstance(a, str) and (not a.startswith("-") or a in allowed_flags)]
        return cmd, ""

    def compose(self, directory: Path, op: str, service: str = "", files: Optional[List[Path]] = None,
                extra_args: Optional[List[str]] = None) -> CmdResult:
        cmd, error = self.compose_command(directory, op, service=service, files=files, extra_args=extra_args)
        if not cmd:
            return CmdResult(False, 2, err=error, error=error)
        return run(cmd, timeout=300, cwd=directory)

    def compose_ps(self, directory: Path, files: Optional[List[Path]] = None) -> Dict[str, Any]:
        files = files or self.compose_files_in(directory)
        argv = self.compose_argv()
        if not argv:
            return {"ok": False, "error": t("containers.composeUnavailable"), "items": []}
        guard = self._compose_guard(files, directory)
        if guard:
            return {"ok": False, "error": guard, "items": []}
        cmd = list(argv)
        for path in files:
            cmd += ["-f", str(path)]
        cmd += ["ps", "--format", "json"]
        res = run(cmd, timeout=60, cwd=directory)
        items = []
        if res.ok:
            data = json_any(res.out)
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                items = [data]
            elif res.out.strip():
                items = json_lines(res.out)
        normalized = []
        for item in items:
            state = str(_get(item, "State", "state", default=""))
            normalized.append({
                "name": str(_get(item, "Name", "name", default="")),
                "service": str(_get(item, "Service", "service", default="")),
                "state": state,
                "status": str(_get(item, "Status", "status", default="")),
                "health": str(_get(item, "Health", "health", default="")),
                "ports": parse_ports(_get(item, "Publishers", "Ports", "ports", default="")),
                "exit_code": _get(item, "ExitCode", "exit_code", default=None),
            })
        return {"ok": res.ok, "items": normalized,
                "error": "" if res.ok else (res.err or res.error or "")[:400]}


# --------------------------------------------------------------------------- #
#  Реестр движков
# --------------------------------------------------------------------------- #

class ContainerRegistry:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.engines: Dict[str, ContainerEngine] = {
            "docker": ContainerEngine("docker", cfg),
            "podman": ContainerEngine("podman", cfg),
        }

    # ------------------------------------------------------------------ #
    def summary(self, refresh: bool = False) -> Dict[str, Any]:
        preferred = self.cfg.get("engines.preferred_container_engine", "auto") or "auto"
        data = {name: engine.info(refresh=refresh).to_dict() for name, engine in self.engines.items()}
        active = self.preferred()
        return {
            "engines": data,
            "preferred": preferred,
            "active": active.kind if active else None,
            "available_any": any(item["available"] and item["daemon_ok"] for item in data.values()),
        }

    def preferred(self) -> Optional[ContainerEngine]:
        setting = self.cfg.get("engines.preferred_container_engine", "auto") or "auto"
        if setting in self.engines:
            info = self.engines[setting].info()
            return self.engines[setting] if info.available else None
        for name in ("docker", "podman"):
            engine = self.engines[name]
            if engine.info().available:
                return engine
        return None

    def get(self, kind: Optional[str]) -> Optional[ContainerEngine]:
        if kind and kind in self.engines:
            return self.engines[kind]
        return self.preferred()

    # ------------------------------------------------------------------ #
    def containers(self, kind: Optional[str] = None, all_containers: bool = True) -> Dict[str, Any]:
        engine = self.get(kind)
        items: List[Dict[str, Any]] = []
        stats: Dict[str, Dict[str, Any]] = {}
        errors: List[str] = []
        engines = [engine] if engine else [e for e in self.engines.values() if e.info().available]
        for item_engine in [e for e in engines if e]:
            listed, err = item_engine.list(all_containers=all_containers)
            if err:
                errors.append(f"{item_engine.kind}: {err}")
            items.extend(listed)
            if listed:
                stat_map, stat_err = item_engine.stats(all_containers=all_containers)
                if stat_err:
                    errors.append(f"{item_engine.kind} stats: {stat_err}")
                stats.update(stat_map)
        for item in items:
            stat = stats.get(item["id"]) or stats.get(item["name"]) or {}
            item["cpu_pct"] = stat.get("cpu_pct", 0.0)
            item["mem_pct"] = stat.get("mem_pct", 0.0)
            item["mem_used"] = stat.get("mem_used", 0.0)
            item["mem_used_human"] = stat.get("mem_used_human", "—")
            item["mem_limit"] = stat.get("mem_limit", 0.0)
            item["net_io"] = stat.get("net_io", "")
            item["block_io"] = stat.get("block_io", "")
            item["pids"] = stat.get("pids", 0)
        running = [c for c in items if c["state"] == "running"]
        totals = {
            "total": len(items),
            "running": len(running),
            "stopped": len([c for c in items if c["state"] in ("exited", "created", "configured", "stopped")]),
            "paused": len([c for c in items if c["state"] == "paused"]),
            "restarting": len([c for c in items if c["state"] == "restarting"]),
            "unhealthy": len([c for c in items if c["health"] == "unhealthy"]),
            "cpu_sum": round(sum(c.get("cpu_pct") or 0 for c in items), 1),
            "mem_sum": sum(c.get("mem_used") or 0 for c in items),
        }
        totals["mem_sum_human"] = human_bytes(totals["mem_sum"])
        return {"items": items, "totals": totals, "errors": errors, "engine": engine.kind if engine else None}

    def projects(self, kind: Optional[str] = None) -> List[Dict[str, Any]]:
        engine = self.get(kind)
        if not engine:
            return []
        projects = engine.find_projects()
        for project in projects:
            directory = Path(project["dir"])
            status = engine.compose_ps(directory, files=[Path(f) for f in project["files"]])
            project["state"] = status.get("items", [])
            project["state_error"] = status.get("error", "")
            project["running"] = len([s for s in status.get("items", []) if s.get("state") == "running"])
            project["total"] = len(status.get("items", []))
        return projects

    # ------------------------------------------------------------------ #
    def container_alerts(self) -> List[Dict[str, Any]]:
        """Провайдер алертов для коллектора: проблемы контейнеров."""
        alerts: List[Dict[str, Any]] = []
        engine = self.preferred()
        if not engine:
            return alerts
        listed, err = engine.list(all_containers=True)
        if err:
            alerts.append({
                "id": "containers-engine",
                "severity": "warning",
                "title": t("containers.alertsEngine", engine=engine.kind),
                "detail": err,
            })
            return alerts
        for item in listed:
            if item["state"] == "restarting":
                alerts.append({
                    "id": f"container-restart-{item['short_id']}",
                    "severity": "critical",
                    "title": t("containers.alertsRestarting", name=item["name"]),
                    "detail": item["status"],
                })
            elif item["health"] == "unhealthy":
                alerts.append({
                    "id": f"container-unhealthy-{item['short_id']}",
                    "severity": "warning",
                    "title": t("containers.alertsUnhealthy", name=item["name"]),
                    "detail": item["status"],
                })
            elif item["state"] == "exited" and item.get("exit_code"):
                alerts.append({
                    "id": f"container-exit-{item['short_id']}",
                    "severity": "warning" if item["exit_code"] not in (0, 143, 137) else "info",
                    "title": t("containers.alertsExited", name=item["name"]),
                    "detail": t("containers.exitCode", status=item["status"], code=item["exit_code"]),
                })
        return alerts

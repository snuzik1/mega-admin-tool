"""HTTP API MegaTool.

Все обработчики обращаются к службам через `request.app.state.rt` (см. runtime.py).
Изменяющие операции пишутся в журнал аудита (см. changes.audit).
"""

from __future__ import annotations

import base64
import ipaddress
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from fastapi import APIRouter, Body, HTTPException, Query, Request

from . import __version__
from . import tls as tls_support
from .agents import MAX_AGENTS, normalize_url
from .appfiles import Workspace, get_template, list_templates, nginx_test, validate
from .balancer import ALGORITHMS
from .changes import audit, collect as collect_changes, read_audit, configure_audit
from .checks import clean_checks
from .config import HOME_DIR, UI_DENSITIES, UI_LANGUAGES, UI_THEMES
from .hosts import clean_hosts
from .i18n import set_language, t
from .recipes import catalog_stats, categories, get_recipe, kinds, list_recipes
from .security import is_loopback
from .util import human_bytes, iso, safe_id, which

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------------- #
#  Помощники
# --------------------------------------------------------------------------- #

def rt(request: Request):
    state = getattr(request.app.state, "rt", None)
    if state is None:  # pragma: no cover — возможен только при неверном запуске
        raise HTTPException(500, t("api.notInitialized"))
    return state


def _ok(data: Any = None, **extra: Any) -> Dict[str, Any]:
    payload = {"ok": True}
    if data is not None:
        payload["data"] = data
    payload.update(extra)
    return payload


def _fail(message: str, **extra: Any) -> Dict[str, Any]:
    payload = {"ok": False, "error": message}
    payload.update(extra)
    return payload


def _as_dict(body: Any) -> Dict[str, Any]:
    if isinstance(body, dict):
        return body
    return {}


# Границы числовых настроек мониторинга: (минимум, максимум)
_MONITOR_LIMITS: Dict[str, Tuple[float, float]] = {
    "interval_ms": (500, 60000),
    "history_points": (60, 200000),
    "container_poll_sec": (5, 3600),
}

# Пороги предупреждений: (минимум, максимум), значения могут быть дробными
_THRESHOLD_LIMITS: Dict[str, Tuple[float, float]] = {
    "cpu_pct": (0, 100),
    "mem_pct": (0, 100),
    "swap_pct": (0, 100),
    "disk_pct": (0, 100),
    "load_per_core": (0, 1000),
    "temp_c": (0, 200),
    "sustain_sec": (1, 86400),
}


def _clean_monitor(raw: Any) -> Dict[str, Any]:
    """Отбрасывает лишние поля и приводит числовые настройки к допустимым границам.

    Клиент может прислать что угодно — сервер обязан защититься сам, чтобы
    опрос метрик не превратился в нагрузку на процессор.
    """
    data = _as_dict(raw)
    out: Dict[str, Any] = {}

    for key, (low, high) in _MONITOR_LIMITS.items():
        if key not in data:
            continue
        try:
            value = float(data[key])
        except (TypeError, ValueError):
            continue
        out[key] = int(min(high, max(low, value)))

    if isinstance(data.get("thresholds"), dict):
        thresholds: Dict[str, float] = {}
        for key, (low, high) in _THRESHOLD_LIMITS.items():
            if key not in data["thresholds"]:
                continue
            try:
                value = float(data["thresholds"][key])
            except (TypeError, ValueError):
                continue
            thresholds[key] = round(min(high, max(low, value)), 2)
        if thresholds:
            out["thresholds"] = thresholds

    for key in ("watch_paths", "extra_roots"):
        if isinstance(data.get(key), list):
            # Пути важны как строки; пустые значения отбрасываем, чтобы
            # сканер не ходил по пустым путям и не тратил время зря.
            out[key] = [str(item).strip() for item in data[key] if str(item).strip()][:32]

    return out


def _clean_agents(raw: Any, previous: Any) -> Tuple[List[Dict[str, Any]], str]:
    """Проверяет список агентов мониторинга.

    Пустой токен у существующего агента означает «оставить прежний»: интерфейс
    не получает секреты, поэтому не может отправить их обратно.
    """
    if not isinstance(raw, list):
        return [], t("agents.badList")
    old = {
        str(item.get("id")): item
        for item in (previous or [])
        if isinstance(item, dict) and item.get("id")
    }
    out: List[Dict[str, Any]] = []
    used: Set[str] = set()
    for item in raw[:MAX_AGENTS]:
        if not isinstance(item, dict):
            return [], t("agents.badList")
        url = normalize_url(item.get("url"))
        if not url:
            return [], t("agents.badUrl")
        name = str(item.get("name") or "").strip()[:80] or url
        agent_id = str(item.get("id") or "").strip()
        if not agent_id or agent_id in used:
            agent_id = f"agent-{uuid.uuid4().hex[:8]}"
        used.add(agent_id)
        token = str(item.get("token") or "")
        if not token and agent_id in old:
            token = str(old[agent_id].get("token") or "")
        out.append({"id": agent_id, "name": name, "url": url, "token": token})
    return out, ""


# Границы настроек журнала задач: (минимум, максимум)
_JOBS_LIMITS: Dict[str, Tuple[int, int]] = {
    "keep": (10, 500),
    "max_active": (1, 64),
}


def _clean_jobs(raw: Any) -> Dict[str, int]:
    """Не даёт испортить лимиты задач через PATCH настроек."""
    data = _as_dict(raw)
    out: Dict[str, int] = {}
    for key, (low, high) in _JOBS_LIMITS.items():
        if key not in data:
            continue
        try:
            number = float(data[key])
            if number != number or number in (float("inf"), float("-inf")):
                continue
            value = int(number)
        except (TypeError, ValueError, OverflowError):
            continue
        out[key] = min(high, max(low, value))
    return out


# --------------------------------------------------------------------------- #
#  Безопасная очистка остальных секций настроек
# --------------------------------------------------------------------------- #

_SETTINGS_SECTIONS = {
    "server", "monitor", "engines", "safety", "ui", "k8s", "ansible",
    "jobs", "agents", "audit", "checks", "hosts",
}
_ENGINE_KEYS = (
    "docker", "podman", "kubectl", "ansible", "ansible_playbook",
    "ansible_inventory", "nginx", "ssh", "scp",
)
_SAFETY_KEYS = (
    "allow_process_kill", "allow_container_actions", "allow_k8s_delete",
    "allow_file_delete", "allow_remote_exec",
)
_ALLOWED_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._:-]{0,251}[A-Za-z0-9])?$")


def _clean_allowed_host(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("["):
        # [2001:db8::1]:8787 — порт допустим только после закрывающей скобки.
        if "]" not in text:
            return ""
        rest = text[text.index("]") + 1:]
        if rest and not (rest.startswith(":") and rest[1:].isdigit()):
            return ""
        text = text[1:text.index("]")]
    elif text.count(":") == 1 and text.rsplit(":", 1)[1].isdigit():
        text = text.rsplit(":", 1)[0]
    if not text:
        return ""
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        pass
    if ":" in text or len(text) > 253 or not _ALLOWED_HOST_RE.fullmatch(text):
        return ""
    return text


def _clean_path_setting(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 4096 or any(ord(char) < 32 for char in text):
        return ""
    return text


def _clean_server(raw: Any) -> Dict[str, Any]:
    data = _as_dict(raw)
    out: Dict[str, Any] = {}
    if "host" in data:
        host = str(data.get("host") or "").strip()
        if host and len(host) <= 255 and not any(char.isspace() for char in host) \
                and "/" not in host and "\\" not in host:
            out["host"] = host
    if "port" in data:
        out["port"] = data["port"]
    if "open_browser" in data:
        out["open_browser"] = data.get("open_browser") is True
    if "allow_insecure_remote" in data:
        out["allow_insecure_remote"] = data.get("allow_insecure_remote") is True
    if "allowed_hosts" in data:
        values = data.get("allowed_hosts")
        if isinstance(values, list):
            hosts = []
            for item in values[:64]:
                name = _clean_allowed_host(item)
                if name and name not in hosts:
                    hosts.append(name)
            out["allowed_hosts"] = hosts
    if "tls" in data:
        raw_tls = _as_dict(data.get("tls"))
        tls: Dict[str, Any] = {}
        if "enabled" in raw_tls:
            tls["enabled"] = raw_tls.get("enabled") is True
        for key in ("certfile", "keyfile"):
            if key in raw_tls:
                tls[key] = _clean_path_setting(raw_tls.get(key))
        out["tls"] = tls
    return out


def _merged_server(cfg, patch: Dict[str, Any]) -> Dict[str, Any]:
    current = _as_dict(cfg.get("server", {}))
    merged = dict(current)
    merged.update(patch)
    current_tls = _as_dict(current.get("tls", {}))
    patch_tls = _as_dict(patch.get("tls", {}))
    merged["tls"] = {**current_tls, **patch_tls}
    return merged


def _server_config_error(cfg, server: Dict[str, Any]) -> str:
    host = str(server.get("host") or "127.0.0.1")
    tls = _as_dict(server.get("tls", {}))
    enabled = tls.get("enabled") is True
    allow_insecure = server.get("allow_insecure_remote") is True
    if not is_loopback(host) and not enabled and not allow_insecure:
        return t("tls.externalRequired")
    if enabled:
        cert, key = tls_support.cert_paths_for(cfg, tls)
        try:
            tls_support.validate_files(cert, key)
        except tls_support.TlsError as exc:
            return t("api.tlsInvalid", error=str(exc))
    return ""


def _clean_ui(raw: Any) -> Dict[str, Any]:
    data = _as_dict(raw)
    out: Dict[str, Any] = {}
    if data.get("theme") in UI_THEMES:
        out["theme"] = data["theme"]
    if data.get("density") in UI_DENSITIES:
        out["density"] = data["density"]
    if data.get("language") in UI_LANGUAGES:
        out["language"] = data["language"]
    if "notifications" in data:
        out["notifications"] = data.get("notifications") is True
    return out


def _clean_safety(raw: Any) -> Dict[str, bool]:
    data = _as_dict(raw)
    return {key: data[key] is True for key in _SAFETY_KEYS if key in data}


def _clean_engines(raw: Any) -> Dict[str, str]:
    data = _as_dict(raw)
    out: Dict[str, str] = {}
    for key in _ENGINE_KEYS:
        if key in data:
            out[key] = str(data.get(key) or "").strip()[:1024]
    preferred = str(data.get("preferred_container_engine") or "").strip().lower()
    if preferred in ("auto", "docker", "podman"):
        out["preferred_container_engine"] = preferred
    return out


def _clean_audit(raw: Any) -> Dict[str, Any]:
    data = _as_dict(raw)
    out: Dict[str, Any] = {}
    if "max_mb" in data:
        try:
            value = float(data["max_mb"])
            if value == value and value not in (float("inf"), float("-inf")):
                out["max_mb"] = round(min(1024.0, max(1.0, value)), 1)
        except (TypeError, ValueError):
            pass
    if "keep" in data:
        try:
            out["keep"] = min(50, max(1, int(float(data["keep"]))))
        except (TypeError, ValueError, OverflowError):
            pass
    return out


def _clean_k8s(raw: Any) -> Dict[str, str]:
    data = _as_dict(raw)
    return {key: str(data.get(key) or "").strip()[:253]
            for key in ("context", "namespace") if key in data}


def _clean_ansible(raw: Any) -> Dict[str, str]:
    data = _as_dict(raw)
    return {key: str(data.get(key) or "").strip()[:1024]
            for key in ("inventory", "playbooks_dir") if key in data}


def _clean_settings_patch(data: Any, runtime) -> Tuple[Dict[str, Any], str]:
    """Единая безопасная очистка PATCH и импорта настроек."""
    body = _as_dict(data)
    patch = {key: value for key, value in body.items() if key in _SETTINGS_SECTIONS}
    if not patch:
        return {}, t("api.nothingToUpdate")

    # Секции-словари не должны приходить строками/массивами: иначе .items()
    # в старом коде мог уронить сервер, а конфиг — испортить.
    for key in _SETTINGS_SECTIONS - {"agents", "checks", "hosts"}:
        if key in patch and not isinstance(patch[key], dict):
            return {}, t("api.settingsObjectExpected")

    if "server" in patch:
        server = _clean_server(patch["server"])
        if "port" in server:
            try:
                port = int(server["port"])
            except (TypeError, ValueError):
                return {}, t("api.portMustBeNumber")
            if not 1 <= port <= 65535:
                return {}, t("api.portOutOfRange")
            server["port"] = port
        server_error = _server_config_error(runtime.cfg, _merged_server(runtime.cfg, server))
        if server_error:
            return {}, server_error
        patch["server"] = server
    if "monitor" in patch:
        patch["monitor"] = _clean_monitor(patch["monitor"])
    if "jobs" in patch:
        patch["jobs"] = _clean_jobs(patch["jobs"])
    if "audit" in patch:
        patch["audit"] = _clean_audit(patch["audit"])
    if "ui" in patch:
        patch["ui"] = _clean_ui(patch["ui"])
    if "engines" in patch:
        patch["engines"] = _clean_engines(patch["engines"])
    if "safety" in patch:
        patch["safety"] = _clean_safety(patch["safety"])
    if "k8s" in patch:
        patch["k8s"] = _clean_k8s(patch["k8s"])
    if "ansible" in patch:
        patch["ansible"] = _clean_ansible(patch["ansible"])
    if "agents" in patch:
        cleaned, error = _clean_agents(patch["agents"], runtime.cfg.get("agents", []))
        if error:
            return {}, error
        patch["agents"] = cleaned
    if "checks" in patch:
        cleaned, error = clean_checks(patch["checks"])
        if error:
            return {}, error
        patch["checks"] = cleaned
    if "hosts" in patch:
        cleaned, error = clean_hosts(patch["hosts"])
        if error:
            return {}, error
        patch["hosts"] = cleaned
    return patch, ""


def _apply_settings(runtime, patch: Dict[str, Any]) -> None:
    """Применяет настройки, которые не требуют перезапуска процесса."""
    if "ui" in patch:
        set_language(runtime.cfg.get("ui.language", "ru"))
    if "monitor" in patch:
        runtime.collector.interval = max(0.5, int(runtime.cfg.get("monitor.interval_ms", 2000)) / 1000.0)
    if "jobs" in patch:
        runtime.jobs.keep = int(_number_setting(runtime.cfg.get("jobs.keep"), 60, 10, 500))
        runtime.jobs.max_active = int(_number_setting(runtime.cfg.get("jobs.max_active"), 8, 1, 64))
    if "audit" in patch:
        configure_audit(
            _number_setting(runtime.cfg.get("audit.max_mb"), 5.0, 1.0, 1024.0),
            int(_number_setting(runtime.cfg.get("audit.keep"), 3, 1, 50)),
        )
    if "checks" in patch:
        runtime.checks.reload()
    if "hosts" in patch:
        runtime.hosts.reload()


def _number_setting(value: Any, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if number != number or number in (float("inf"), float("-inf")):
        number = default
    return min(high, max(low, number))


# --------------------------------------------------------------------------- #
#  Состояние и здоровье
# --------------------------------------------------------------------------- #

@router.get("/health")
def health(request: Request) -> Dict[str, Any]:
    runtime = rt(request)
    # Публичный health-check не должен раскрывать домашний путь пользователя.
    notes = [
        note for note in runtime.notes
        if not (isinstance(note, dict) and note.get("key") == "notes.home")
    ]
    return {
        "ok": True,
        "app": "MegaTool",
        "version": __version__,
        "uptime": round(time.time() - runtime.started_at, 1),
        "monitor": runtime.collector.state.get("running", False),
        "notes": notes,
    }


@router.get("/state")
def state(request: Request) -> Dict[str, Any]:
    runtime = rt(request)
    return {"ok": True, "data": runtime.state()}


@router.get("/info")
def info(request: Request, refresh: bool = False) -> Dict[str, Any]:
    from .systeminfo import static_info

    runtime = rt(request)
    return {"ok": True, "data": static_info(refresh=refresh), "workspace": runtime.workspace.usage()}


@router.get("/audit")
def audit_log(request: Request, limit: int = Query(200, ge=1, le=2000)) -> Dict[str, Any]:
    rt(request)
    return {"ok": True, "items": read_audit(limit)}


# --------------------------------------------------------------------------- #
#  Мониторинг
# --------------------------------------------------------------------------- #

@router.get("/metrics/snapshot")
def metrics_snapshot(request: Request, details: bool = True) -> Dict[str, Any]:
    runtime = rt(request)
    return {"ok": True, "data": runtime.collector.snapshot(with_details=details)}


@router.get("/metrics/history")
def metrics_history(request: Request, minutes: int = Query(30, ge=1, le=1440),
                    series: str = "") -> Dict[str, Any]:
    runtime = rt(request)
    keys = [s.strip() for s in series.split(",") if s.strip()] or None
    return {"ok": True, "data": runtime.collector.history(minutes=minutes, series=keys)}


@router.get("/processes")
def processes(request: Request, sort: str = "cpu", limit: int = Query(50, ge=1, le=500),
              q: str = "") -> Dict[str, Any]:
    runtime = rt(request)
    items = runtime.collector.processes(sort=sort, limit=limit, query=q)
    return {"ok": True, "items": items, "count": len(items)}


@router.get("/processes/{pid}")
def process_detail(request: Request, pid: int) -> Dict[str, Any]:
    runtime = rt(request)
    detail = runtime.collector.process_detail(pid)
    if detail is None:
        return _fail(t("api.processNotFound"))
    return _ok(detail)


@router.post("/processes/{pid}/kill")
def process_kill(request: Request, pid: int, force: bool = False) -> Dict[str, Any]:
    runtime = rt(request)
    result = runtime.collector.kill_process(pid, force=force)
    audit("process.kill", target=str(pid), detail="force" if force else "terminate",
          ok=bool(result.get("ok")), extra={"error": result.get("error", "")})
    return result


@router.get("/connections")
def connections(request: Request, limit: int = Query(100, ge=1, le=1000)) -> Dict[str, Any]:
    runtime = rt(request)
    return {"ok": True, "items": runtime.collector.connections(limit=limit),
            "summary": runtime.collector.connection_summary()}


@router.get("/alerts")
def alerts(request: Request) -> Dict[str, Any]:
    runtime = rt(request)
    return {"ok": True, "items": runtime.collector.active_alerts()}


@router.post("/alerts/{alert_id}/dismiss")
def dismiss_alert(request: Request, alert_id: str) -> Dict[str, Any]:
    runtime = rt(request)
    removed = runtime.collector.clear_alert(alert_id)
    audit("alert.dismiss", target=alert_id, ok=removed)
    return _ok({"dismissed": removed})


@router.get("/changes")
def changes(request: Request, hours: float = Query(24.0, ge=0.1, le=720.0),
            files: int = Query(150, ge=1, le=500)) -> Dict[str, Any]:
    runtime = rt(request)
    return {"ok": True, "data": collect_changes(runtime.cfg, hours=hours, file_limit=files)}


# --------------------------------------------------------------------------- #
#  Контейнеры
# --------------------------------------------------------------------------- #

@router.get("/engines")
def engines(request: Request, refresh: bool = False) -> Dict[str, Any]:
    runtime = rt(request)
    return {"ok": True, "data": runtime.registry.summary(refresh=refresh)}


@router.post("/engines/refresh")
def engines_refresh(request: Request) -> Dict[str, Any]:
    runtime = rt(request)
    summary = runtime.registry.summary(refresh=True)
    audit("engines.refresh", detail="; ".join(
        f"{name}: {'ok' if info['available'] else t('api.no')}" for name, info in summary["engines"].items()))
    return {"ok": True, "data": summary}


@router.get("/containers")
def containers(request: Request, engine: str = "", all: bool = True,
               stats: bool = True) -> Dict[str, Any]:
    runtime = rt(request)
    data = runtime.registry.containers(kind=engine or None, all_containers=all)
    if not stats:
        for item in data["items"]:
            item["cpu_pct"] = None
    return {"ok": True, "data": data}


@router.get("/containers/{cid}")
def container_inspect(request: Request, cid: str, engine: str = "") -> Dict[str, Any]:
    runtime = rt(request)
    target = runtime.registry.get(engine or None)
    if not target:
        return _fail(t("api.engineUnavailable"))
    return target.inspect(cid)


@router.get("/containers/{cid}/logs")
def container_logs(request: Request, cid: str, engine: str = "", tail: int = Query(200, ge=1, le=5000),
                   timestamps: bool = False) -> Dict[str, Any]:
    runtime = rt(request)
    target = runtime.registry.get(engine or None)
    if not target:
        return _fail(t("api.engineUnavailable"))
    return target.logs(cid, tail=tail, timestamps=timestamps)


@router.post("/containers/{cid}/{action}")
def container_action(request: Request, cid: str, action: str, engine: str = "",
                     body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    runtime = rt(request)
    target = runtime.registry.get(engine or None)
    if not target:
        return _fail(t("api.engineUnavailable"))
    if action == "inspect":
        return target.inspect(cid)
    result = target.action(cid, action, new_name=_as_dict(body).get("new_name", ""))
    audit(f"container.{action}", target=f"{target.kind}/{cid}", ok=bool(result.get("ok")),
          detail=str(result.get("error", ""))[:200])
    return result


@router.get("/engine/df")
def engine_df(request: Request, engine: str = "") -> Dict[str, Any]:
    runtime = rt(request)
    target = runtime.registry.get(engine or None)
    if not target:
        return _fail(t("api.engineUnavailable"))
    data = target.df()
    data["engine"] = target.kind
    return data


@router.get("/engine/{resource}")
def engine_resource(request: Request, resource: str, engine: str = "",
                    limit: int = Query(60, ge=1, le=500)) -> Dict[str, Any]:
    runtime = rt(request)
    target = runtime.registry.get(engine or None)
    if not target:
        return _fail(t("api.engineUnavailable"))
    if resource == "images":
        return {"ok": True, "items": target.images(limit)}
    if resource == "volumes":
        return {"ok": True, "items": target.volumes(limit)}
    if resource == "networks":
        return {"ok": True, "items": target.networks(limit)}
    return _fail(t("api.unknownResource", resource=resource))


@router.get("/compose/projects")
def compose_projects(request: Request, engine: str = "") -> Dict[str, Any]:
    runtime = rt(request)
    projects = runtime.registry.projects(kind=engine or None)
    return {"ok": True, "items": projects, "count": len(projects)}


@router.get("/compose/ps")
def compose_ps(request: Request, dir: str, engine: str = "") -> Dict[str, Any]:
    runtime = rt(request)
    target = runtime.registry.get(engine or None)
    if not target:
        return _fail(t("api.engineUnavailable"))
    try:
        directory = runtime.workspace.resolve(dir, must_exist=True)
    except (PermissionError, FileNotFoundError) as exc:
        return _fail(str(exc))
    return target.compose_ps(directory)


@router.post("/compose/{action}")
def compose_action(request: Request, action: str, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    runtime = rt(request)
    data = _as_dict(body)
    directory = data.get("dir", "")
    if not directory:
        return _fail(t("api.projectDirRequired"))
    target = runtime.registry.get(data.get("engine") or None)
    if not target:
        return _fail(t("api.engineUnavailable"))
    try:
        path = runtime.workspace.resolve(directory, must_exist=True)
    except (PermissionError, FileNotFoundError) as exc:
        return _fail(str(exc))
    argv, error = target.compose_command(path, action, service=data.get("service", ""))
    if not argv:
        return _fail(error)
    job = runtime.jobs.submit(title=f"compose {action}: {path.name}", cmd=argv, cwd=str(path),
                              kind="compose", meta={"dir": str(path), "action": action})
    audit(f"compose.{action}", target=str(path), ok=True, detail=t("api.jobStarted", job=job.id))
    return _ok({"job": job.to_dict()})


# --------------------------------------------------------------------------- #
#  Kubernetes
# --------------------------------------------------------------------------- #

@router.get("/k8s/status")
def k8s_status(request: Request) -> Dict[str, Any]:
    runtime = rt(request)
    return {"ok": True, "data": {
        **runtime.k8s.available(),
        "context": runtime.k8s.context,
        "namespace": runtime.k8s.namespace,
        "contexts": runtime.k8s.contexts() if runtime.k8s.available().get("available") else {"items": []},
    }}


@router.post("/k8s/target")
def k8s_target(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    runtime = rt(request)
    data = _as_dict(body)
    result = runtime.k8s.set_target(context=data.get("context", ""), namespace=data.get("namespace", ""))
    runtime.k8s_cached.invalidate()
    audit("k8s.target", target=f"{data.get('context', '')}/{data.get('namespace', '')}",
          ok=bool(result.get("ok")), detail=str(result.get("error", "")))
    return result


@router.get("/k8s/overview")
def k8s_overview(request: Request) -> Dict[str, Any]:
    runtime = rt(request)
    return _ok(runtime.k8s_cached.get("overview", runtime.k8s.overview, ttl=5))


@router.get("/k8s/logs")
def k8s_logs(request: Request, pod: str, namespace: str = "", container: str = "",
             tail: int = Query(300, ge=1, le=5000)) -> Dict[str, Any]:
    runtime = rt(request)
    return runtime.k8s.logs(pod, namespace=namespace, container=container, tail=tail)


@router.get("/k8s/describe")
def k8s_describe(request: Request, kind: str, name: str, namespace: str = "") -> Dict[str, Any]:
    runtime = rt(request)
    return runtime.k8s.describe(kind, name, namespace=namespace)


@router.get("/k8s/{resource}")
def k8s_resource(request: Request, resource: str, namespace: str = "",
                 limit: int = Query(200, ge=1, le=2000)) -> Dict[str, Any]:
    runtime = rt(request)
    handler = {
        "nodes": lambda: runtime.k8s.nodes(),
        "pods": lambda: runtime.k8s.pods(namespace=namespace or None),
        "deployments": lambda: runtime.k8s.deployments(namespace=namespace or None),
        "services": lambda: runtime.k8s.services(namespace=namespace or None),
        "events": lambda: runtime.k8s.events(limit=limit, namespace=namespace or None),
        "namespaces": lambda: runtime.k8s.namespaces(),
        "top": lambda: {"nodes": runtime.k8s.top_nodes(), "pods": runtime.k8s.top_pods()},
        "contexts": lambda: runtime.k8s.contexts(),
    }.get(resource)
    if not handler:
        return _fail(t("api.unknownResource", resource=resource))
    if not runtime.k8s.available().get("available"):
        return _fail(t("api.kubectlNotFound"))
    cache_key = f"{resource}:{namespace}:{limit}"
    return _ok(runtime.k8s_cached.get(cache_key, handler, ttl=5))


@router.post("/k8s/{action}")
def k8s_action(request: Request, action: str, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    runtime = rt(request)
    data = _as_dict(body)
    namespace = data.get("namespace", "")
    kind = data.get("kind", "")
    name = data.get("name", "")
    if action == "scale":
        result = runtime.k8s.scale(kind, name, int(data.get("replicas", 1)), namespace=namespace)
    elif action == "restart":
        result = runtime.k8s.rollout_restart(kind or "deployment", name, namespace=namespace)
    elif action == "undo":
        result = runtime.k8s.rollout_undo(kind or "deployment", name, namespace=namespace)
    elif action == "delete":
        result = runtime.k8s.delete(kind, name, namespace=namespace)
    elif action == "apply":
        path = data.get("path", "")
        try:
            target = runtime.workspace.resolve(path, must_exist=True)
        except (PermissionError, FileNotFoundError) as exc:
            return _fail(str(exc))
        result = runtime.k8s.apply_file(target, dry_run=bool(data.get("dry_run")))
    elif action == "delete-file":
        path = data.get("path", "")
        try:
            target = runtime.workspace.resolve(path, must_exist=True)
        except (PermissionError, FileNotFoundError) as exc:
            return _fail(str(exc))
        result = runtime.k8s.delete_file(target, dry_run=bool(data.get("dry_run")))
    else:
        return _fail(t("api.unknownAction", action=action))
    runtime.k8s_cached.invalidate()
    audit(f"k8s.{action}", target=f"{kind}/{name}".strip("/"), ok=bool(result.get("ok")),
          detail=(str(result.get("error", "")) or str(result.get("out", "")))[:200])
    return result


# --------------------------------------------------------------------------- #
#  Ansible
# --------------------------------------------------------------------------- #

@router.get("/ansible/status")
def ansible_status(request: Request, refresh: bool = False) -> Dict[str, Any]:
    runtime = rt(request)
    return {"ok": True, "data": runtime.ansible.status(refresh=refresh)}


@router.get("/ansible/inventory")
def ansible_inventory(request: Request) -> Dict[str, Any]:
    runtime = rt(request)
    tree = runtime.ansible.inventory_tree()
    tree["path"] = str(runtime.ansible.inventory_path())
    try:
        tree["content"] = runtime.ansible.inventory_path().read_text(encoding="utf-8")
    except OSError:
        tree["content"] = ""
    return {"ok": bool(tree.get("ok")), "data": tree}


@router.post("/ansible/inventory")
def ansible_inventory_save(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    runtime = rt(request)
    content = str(_as_dict(body).get("content", ""))
    if not content.strip():
        return _fail(t("api.emptyInventory"))
    result = runtime.ansible.write_inventory(content)
    audit("ansible.inventory.save", target=str(runtime.ansible.inventory_path()), ok=True)
    tree = runtime.ansible.inventory_tree()
    return _ok({"written": result, "tree": tree})


@router.get("/ansible/playbooks")
def ansible_playbooks(request: Request) -> Dict[str, Any]:
    runtime = rt(request)
    items = runtime.ansible.playbooks()
    return {"ok": True, "items": items, "dir": str(runtime.ansible.playbooks_dir())}


@router.post("/ansible/run")
def ansible_run(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    runtime = rt(request)
    data = _as_dict(body)
    extra_vars = data.get("extra_vars") or {}
    if isinstance(extra_vars, list):
        from .util import split_kv

        extra_vars = split_kv(extra_vars)
    result = runtime.ansible.run_playbook(
        runtime.jobs,
        playbook=data.get("playbook", ""),
        limit=data.get("limit", ""),
        tags=data.get("tags", ""),
        skip_tags=data.get("skip_tags", ""),
        check=bool(data.get("check")),
        diff=bool(data.get("diff")),
        extra_vars=extra_vars,
        verbosity=int(data.get("verbosity", 0) or 0),
        inventory=data.get("inventory", ""),
    )
    audit("ansible.run", target=data.get("playbook", ""), ok=bool(result.get("ok")),
          detail=f"limit={data.get('limit', '')} check={bool(data.get('check'))} "
                 + str(result.get("error", ""))[:120])
    return result


@router.post("/ansible/ping")
def ansible_ping(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    runtime = rt(request)
    data = _as_dict(body)
    result = runtime.ansible.adhoc(runtime.jobs, module=data.get("module", "ping") or "ping",
                                   pattern=data.get("pattern", "all") or "all",
                                   args=data.get("args", ""), inventory=data.get("inventory", ""))
    audit("ansible.adhoc", target=data.get("pattern", "all"), ok=bool(result.get("ok")))
    return result


@router.post("/ansible/check")
def ansible_check(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    runtime = rt(request)
    return runtime.ansible.syntax_check(_as_dict(body).get("playbook", ""))


# --------------------------------------------------------------------------- #
#  Задачи
# --------------------------------------------------------------------------- #

@router.get("/jobs")
def jobs_list(request: Request, limit: int = Query(40, ge=1, le=200), kind: str = "") -> Dict[str, Any]:
    runtime = rt(request)
    return {"ok": True, "items": runtime.jobs.list(limit=limit, kind=kind or None),
            "running": runtime.jobs.active_count()}


@router.get("/jobs/{job_id}")
def job_detail(request: Request, job_id: str, tail: int = Query(500, ge=1, le=4000)) -> Dict[str, Any]:
    runtime = rt(request)
    return runtime.jobs.log(job_id, tail=tail)


@router.post("/jobs/{job_id}/cancel")
def job_cancel(request: Request, job_id: str) -> Dict[str, Any]:
    runtime = rt(request)
    result = runtime.jobs.cancel(job_id)
    audit("job.cancel", target=job_id, ok=bool(result.get("ok")), detail=str(result.get("error", "")))
    return result


@router.post("/jobs/cancel-all")
def jobs_cancel_all(request: Request) -> Dict[str, Any]:
    runtime = rt(request)
    count = runtime.jobs.cancel_all()
    audit("job.cancel_all", detail=t("api.jobsCancelled", count=count))
    return _ok({"cancelled": count})


# --------------------------------------------------------------------------- #
#  Балансировщик нагрузки
# --------------------------------------------------------------------------- #

@router.get("/balancers")
def balancers_list(request: Request) -> Dict[str, Any]:
    runtime = rt(request)
    return {"ok": True, "items": runtime.balancers.list_all(), "algorithms": ALGORITHMS}


@router.post("/balancers")
def balancer_create(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    runtime = rt(request)
    result = runtime.balancers.create(_as_dict(body))
    audit("balancer.create", target=_as_dict(body).get("name", ""), ok=bool(result.get("ok")),
          detail=str(result.get("error", "")))
    return result


@router.patch("/balancers/{name}")
def balancer_update(request: Request, name: str, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    runtime = rt(request)
    result = runtime.balancers.update(name, _as_dict(body))
    audit("balancer.update", target=name, ok=bool(result.get("ok")), detail=str(result.get("error", "")))
    return result


@router.delete("/balancers/{name}")
async def balancer_delete(request: Request, name: str) -> Dict[str, Any]:
    runtime = rt(request)
    result = await runtime.balancers.delete(name)
    audit("balancer.delete", target=name, ok=bool(result.get("ok")))
    return result


@router.post("/balancers/{name}/start")
async def balancer_start(request: Request, name: str) -> Dict[str, Any]:
    runtime = rt(request)
    result = await runtime.balancers.start(name)
    audit("balancer.start", target=name, ok=bool(result.get("ok")), detail=str(result.get("error", "")))
    return result


@router.post("/balancers/{name}/stop")
async def balancer_stop(request: Request, name: str) -> Dict[str, Any]:
    runtime = rt(request)
    result = await runtime.balancers.stop(name)
    audit("balancer.stop", target=name, ok=bool(result.get("ok")))
    return result


@router.get("/balancers/{name}/stats")
def balancer_stats(request: Request, name: str) -> Dict[str, Any]:
    runtime = rt(request)
    balancer = runtime.balancers.get(name)
    if not balancer:
        return _fail(t("api.balancerNotFound"))
    return {"ok": True, "data": balancer.to_dict(), "history": balancer.history()}


@router.post("/balancers/{name}/probe")
async def balancer_probe(request: Request, name: str) -> Dict[str, Any]:
    """Немедленная проверка всех бэкендов (не дожидаясь интервала)."""
    runtime = rt(request)
    balancer = runtime.balancers.get(name)
    if not balancer:
        return _fail(t("api.balancerNotFound"))
    import asyncio

    await asyncio.gather(*[balancer._check_target(target) for target in list(balancer.targets)])
    audit("balancer.probe", target=name, ok=True)
    return _ok({"targets": [t.to_dict() for t in balancer.targets]})


@router.get("/balancers/{name}/export")
def balancer_export(request: Request, name: str, format: str = "nginx") -> Dict[str, Any]:
    runtime = rt(request)
    result = runtime.balancers.export(name, fmt=format, write_file=False)
    audit("balancer.export", target=f"{name} ({format})", ok=bool(result.get("ok")))
    return result


@router.post("/balancers/{name}/export")
def balancer_export_write(request: Request, name: str, format: str = "nginx") -> Dict[str, Any]:
    """Запись конфига — только POST: GET не должен иметь побочных эффектов (CSRF)."""
    runtime = rt(request)
    result = runtime.balancers.export(name, fmt=format, write_file=True)
    audit("balancer.export", target=f"{name} ({format})", ok=bool(result.get("ok")))
    return result


# --------------------------------------------------------------------------- #
#  Каталог приложений
# --------------------------------------------------------------------------- #

@router.get("/catalog/recipes")
def catalog_recipes(request: Request, q: str = "", category: str = "", kind: str = "") -> Dict[str, Any]:
    rt(request)
    return {"ok": True, "items": list_recipes(query=q, category=category, kind=kind),
            "categories": categories(), "kinds": kinds(), "stats": catalog_stats()}


@router.get("/catalog/recipe/{recipe_id}")
def catalog_recipe(request: Request, recipe_id: str) -> Dict[str, Any]:
    rt(request)
    recipe = get_recipe(recipe_id)
    if not recipe:
        return _fail(t("api.recipeNotFound"))
    return _ok(recipe)


@router.post("/catalog/deploy")
def catalog_deploy(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    runtime = rt(request)
    data = _as_dict(body)
    result = runtime.catalog.deploy(
        recipe_id=data.get("recipe_id", ""),
        name=data.get("name", ""),
        values=data.get("values") or {},
        start=bool(data.get("start")),
        engine=data.get("engine", ""),
    )
    audit("catalog.deploy", target=f"{data.get('recipe_id', '')} → {result.get('slug', '')}",
          ok=bool(result.get("ok")), detail=str(result.get("error", ""))[:200])
    return result


@router.get("/catalog/deployed")
def catalog_deployed(request: Request, status: bool = True) -> Dict[str, Any]:
    runtime = rt(request)
    return {"ok": True, "items": runtime.catalog.deployed(with_status=status),
            "stats": runtime.catalog.stats()}


@router.get("/catalog/deployed/{slug}/logs")
def catalog_logs(request: Request, slug: str, tail: int = Query(200, ge=1, le=5000),
                 service: str = "") -> Dict[str, Any]:
    runtime = rt(request)
    return runtime.catalog.logs(slug, tail=tail, service=service)


@router.post("/catalog/deployed/{slug}/{action}")
def catalog_action(request: Request, slug: str, action: str) -> Dict[str, Any]:
    runtime = rt(request)
    result = runtime.catalog.action(slug, action)
    audit(f"catalog.{action}", target=slug, ok=bool(result.get("ok")),
          detail=str(result.get("error", ""))[:200])
    return result


@router.post("/catalog/deployed/{slug}/apply")
def catalog_apply(request: Request, slug: str, dry_run: bool = False) -> Dict[str, Any]:
    runtime = rt(request)
    result = runtime.catalog.apply_manifests(slug, dry_run=dry_run)
    audit("catalog.k8s_apply", target=slug, ok=bool(result.get("ok")),
          detail="dry-run" if dry_run else "apply")
    return result


@router.delete("/catalog/deployed/{slug}")
def catalog_remove(request: Request, slug: str, delete_files: bool = True,
                   stop_first: bool = True) -> Dict[str, Any]:
    runtime = rt(request)
    result = runtime.catalog.remove(slug, delete_files=delete_files, stop_first=stop_first)
    audit("catalog.remove", target=slug, ok=bool(result.get("ok")),
          detail=f"files={delete_files}", extra={"error": result.get("error", "")})
    return result


# --------------------------------------------------------------------------- #
#  Файлы и конфиги
# --------------------------------------------------------------------------- #

@router.get("/files/roots")
def files_roots(request: Request) -> Dict[str, Any]:
    runtime = rt(request)
    return {"ok": True, "items": runtime.workspace.roots(), "home": str(HOME_DIR)}


@router.get("/files/tree")
def files_tree(request: Request, root: str = "", depth: int = Query(3, ge=1, le=8)) -> Dict[str, Any]:
    runtime = rt(request)
    try:
        return runtime.workspace.tree(root=root, depth=depth)
    except (PermissionError, FileNotFoundError) as exc:
        return _fail(str(exc))


@router.get("/files/read")
def files_read(request: Request, path: str) -> Dict[str, Any]:
    runtime = rt(request)
    try:
        return runtime.workspace.read(path)
    except (PermissionError, FileNotFoundError) as exc:
        return _fail(str(exc))


@router.post("/files/write")
def files_write(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    runtime = rt(request)
    data = _as_dict(body)
    try:
        result = runtime.workspace.write(data.get("path", ""), data.get("content", ""),
                                        backup=bool(data.get("backup", True)))
    except (PermissionError, FileNotFoundError) as exc:
        return _fail(str(exc))
    audit("files.write", target=data.get("path", ""), ok=bool(result.get("ok")),
          detail=t("api.charsCount", count=len(data.get("content", ""))))
    return result


@router.post("/files/mkdir")
def files_mkdir(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    runtime = rt(request)
    try:
        result = runtime.workspace.mkdir(_as_dict(body).get("path", ""))
    except (PermissionError, FileNotFoundError) as exc:
        return _fail(str(exc))
    audit("files.mkdir", target=_as_dict(body).get("path", ""), ok=True)
    return result


@router.post("/files/rename")
def files_rename(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    runtime = rt(request)
    data = _as_dict(body)
    try:
        result = runtime.workspace.rename(data.get("path", ""), data.get("new_path", ""))
    except (PermissionError, FileNotFoundError) as exc:
        return _fail(str(exc))
    audit("files.rename", target=f"{data.get('path', '')} → {data.get('new_path', '')}",
          ok=bool(result.get("ok")))
    return result


@router.post("/files/delete")
def files_delete(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    runtime = rt(request)
    data = _as_dict(body)
    try:
        result = runtime.workspace.delete(data.get("path", ""), recursive=bool(data.get("recursive")))
    except (PermissionError, FileNotFoundError) as exc:
        return _fail(str(exc))
    audit("files.delete", target=data.get("path", ""), ok=bool(result.get("ok")),
          detail=f"recursive={bool(data.get('recursive'))}")
    return result


@router.post("/files/upload")
def files_upload(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    runtime = rt(request)
    data = _as_dict(body)
    raw = data.get("content_base64", "")
    try:
        payload = base64.b64decode(raw, validate=True)
    except Exception:
        return _fail(t("api.badBase64"))
    if len(payload) > 8 * 1024 * 1024:
        return _fail(t("api.fileTooLarge"))
    try:
        result = runtime.workspace.upload(data.get("path", ""), data.get("filename", t("api.defaultFilename")),
                                          payload)
    except (PermissionError, FileNotFoundError) as exc:
        return _fail(str(exc))
    audit("files.upload", target=result.get("path", ""), ok=True, detail=t("api.bytesCount", count=len(payload)))
    return result


@router.get("/files/search")
def files_search(request: Request, q: str, root: str = "", limit: int = Query(80, ge=1, le=500)) -> Dict[str, Any]:
    runtime = rt(request)
    try:
        return runtime.workspace.search(q, root=root, limit=limit)
    except (PermissionError, FileNotFoundError) as exc:
        return _fail(str(exc))


@router.post("/files/validate")
def files_validate(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    runtime = rt(request)
    data = _as_dict(body)
    result = validate(data.get("filename", "file.txt"), data.get("content", ""))
    if data.get("nginx_test") and data.get("path"):
        try:
            target = runtime.workspace.resolve(data["path"], must_exist=True)
        except (PermissionError, FileNotFoundError) as exc:
            result["nginx"] = {"ok": False, "error": str(exc)}
        else:
            binary = which("nginx", runtime.cfg.get("engines.nginx", "") or None)
            result["nginx"] = nginx_test(binary or "", target)
    return {"ok": True, "data": result}


@router.get("/templates")
def templates(request: Request) -> Dict[str, Any]:
    rt(request)
    return {"ok": True, "items": list_templates()}


@router.get("/templates/{template_id}")
def template(request: Request, template_id: str) -> Dict[str, Any]:
    rt(request)
    item = get_template(template_id)
    if not item:
        return _fail(t("api.templateNotFound"))
    return _ok(item)


# --------------------------------------------------------------------------- #
#  Настройки
# --------------------------------------------------------------------------- #

@router.get("/settings")
def settings_get(request: Request) -> Dict[str, Any]:
    runtime = rt(request)
    # Ответы этого раздела должны идти на языке, выбранном в настройках.
    set_language(runtime.cfg.get("ui.language", "ru"))
    return {
        "ok": True,
        "data": runtime.cfg.export(redact_secrets=True),
        "tls": tls_support.describe(runtime.cfg),
        "paths": {
            "home": str(HOME_DIR),
            "config": str(runtime.cfg.path),
            "audit": str(HOME_DIR / "logs" / "audit.jsonl"),
        },
        "workspace": runtime.workspace.usage(),
    }


@router.patch("/settings")
def settings_patch(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    runtime = rt(request)
    patch, error = _clean_settings_patch(body, runtime)
    if error:
        return _fail(error)
    runtime.cfg.update(patch)
    _apply_settings(runtime, patch)
    audit("settings.update", target=", ".join(patch.keys()), ok=True)
    return _ok(runtime.cfg.export(redact_secrets=True), restart_required=("server" in patch))


@router.post("/settings/tls/generate")
def settings_tls_generate(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """Создаёт самоподписанный сертификат и включает HTTPS после перезапуска."""
    runtime = rt(request)
    force = _as_dict(body).get("force") is True
    try:
        info = tls_support.generate_self_signed(runtime.cfg, force=force)
    except tls_support.TlsError as exc:
        audit("settings.tls.generate", target="", ok=False, detail=str(exc))
        return _fail(t("api.tlsGenerateFailed", error=str(exc)))
    runtime.cfg.update({"server": {"tls": {"enabled": True}}})
    info = tls_support.describe(runtime.cfg)
    audit("settings.tls.generate", target=info.get("certfile", ""), ok=True)
    return _ok(info, restart_required=True)


@router.get("/settings/export")
def settings_export(request: Request) -> Dict[str, Any]:
    runtime = rt(request)
    return {"ok": True, "data": runtime.cfg.export(redact_secrets=True)}


@router.post("/settings/import")
def settings_import(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    runtime = rt(request)
    data = _as_dict(body).get("config") or {}
    if not isinstance(data, dict):
        return _fail(t("api.settingsObjectExpected"))
    # Принимаем только известные секции и прогоняем их через ту же очистку,
    # что и PATCH: импорт не должен обходить валидацию или добавлять лишние ключи.
    known = {key: value for key, value in data.items() if key in _SETTINGS_SECTIONS}
    if not known:
        return _fail(t("api.nothingToUpdate"))
    safe, error = _clean_settings_patch(known, runtime)
    if error:
        audit("settings.import", target="validation", ok=False, detail=error)
        return _fail(error)
    runtime.cfg.update(safe)
    _apply_settings(runtime, safe)
    runtime.balancers.load()
    audit("settings.import", ok=True)
    return _ok(runtime.cfg.export(redact_secrets=True))


@router.post("/settings/engines/test")
def settings_engines_test(request: Request) -> Dict[str, Any]:
    runtime = rt(request)
    result = runtime.registry.summary(refresh=True)
    ansible = runtime.ansible.status(refresh=True)
    k8s_status = runtime.k8s.available()
    nginx_binary = which("nginx", runtime.cfg.get("engines.nginx", "") or None)
    return _ok({
        "engines": result,
        "ansible": ansible,
        "kubectl": k8s_status,
        "nginx": {"available": bool(nginx_binary), "binary": nginx_binary},
        "checked": iso(),
    })


# --------------------------------------------------------------------------- #
#  Служебное
# --------------------------------------------------------------------------- #

@router.get("/paths")
def paths(request: Request) -> Dict[str, Any]:
    rt(request)
    return _ok({
        "home": str(HOME_DIR),
        "apps": str(HOME_DIR / "apps"),
        "configs": str(HOME_DIR / "configs"),
        "runs": str(HOME_DIR / "runs"),
        "logs": str(HOME_DIR / "logs"),
    })


@router.get("/version")
def version() -> Dict[str, Any]:
    return {"ok": True, "version": __version__, "app": "MegaTool"}


__all__ = ["router", "validate", "Workspace", "safe_id", "human_bytes"]

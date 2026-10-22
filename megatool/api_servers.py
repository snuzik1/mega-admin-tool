"""HTTP API раздела «Серверы по SSH».

Подключается в megatool/server.py рядом с остальными роутерами:

    from .api_servers import router as servers_router
    ...
    app.include_router(servers_router)

Все операции, меняющие состояние, пишутся в журнал аудита
(servers.add / update / delete / check / run / exec / playbook).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Body, HTTPException, Request

from .changes import audit
from .i18n import t
from .provision import (
    MAX_COMMAND,
    ServersManager,
    build_playbook,
    build_script,
    check_server,
    select_tasks,
    ssh_available,
    submit_command_job,
    submit_script_job,
    task_catalog,
    validate_params,
)

router = APIRouter(prefix="/api/servers", tags=["servers"])


# --------------------------------------------------------------------------- #
#  Помощники
# --------------------------------------------------------------------------- #

def _runtime(request: Request) -> Any:
    runtime = getattr(request.app.state, "rt", None)
    if runtime is None:  # pragma: no cover — возможен только при неверном запуске
        raise HTTPException(500, t("api.notInitialized"))
    return runtime


def _manager(runtime: Any) -> ServersManager:
    """Возвращает общий менеджер серверов (создаёт и кэширует при первом обращении)."""
    manager = getattr(runtime, "servers", None)
    if isinstance(manager, ServersManager):
        return manager
    manager = ServersManager(runtime.cfg)
    try:
        runtime.servers = manager
    except Exception:  # pragma: no cover — Runtime допускает атрибуты
        pass
    return manager


def _remote_allowed(cfg: Any) -> bool:
    return bool(cfg.get("safety.allow_remote_exec", True))


def _fail(message: str, **extra: Any) -> Dict[str, Any]:
    return {"ok": False, "error": message, **extra}


def _body(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _task_ids(body: Dict[str, Any]) -> List[str]:
    raw = body.get("tasks")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(item) for item in raw if str(item).strip()]


def _server_or_error(manager: ServersManager, server_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    server = manager.get(server_id)
    if not server:
        return None, _fail(t("servers.notFound"))
    return server, None


# --------------------------------------------------------------------------- #
#  Серверы
# --------------------------------------------------------------------------- #

@router.get("")
def servers_list(request: Request) -> Dict[str, Any]:
    runtime = _runtime(request)
    manager = _manager(runtime)
    ssh = ssh_available(runtime.cfg)
    items = []
    for server in manager.list():
        item = dict(server)
        item["last_check"] = manager.last_report(str(server.get("id", "")))
        items.append(item)
    return {
        "ok": True,
        "items": items,
        "ssh": {"available": bool(ssh), "path": ssh or ""},
        "remote": _remote_allowed(runtime.cfg),
    }


@router.post("")
def servers_add(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    runtime = _runtime(request)
    manager = _manager(runtime)
    result = manager.add(_body(body))
    server = result.get("server") or {}
    audit("servers.add", target=str(server.get("name") or _body(body).get("host", "")),
          ok=bool(result.get("ok")), detail=str(result.get("error", ""))[:200])
    return result


@router.patch("/{sid}")
def servers_update(request: Request, sid: str, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    runtime = _runtime(request)
    manager = _manager(runtime)
    result = manager.update(sid, _body(body))
    server = result.get("server") or {}
    audit("servers.update", target=str(server.get("name") or sid),
          ok=bool(result.get("ok")), detail=str(result.get("error", ""))[:200])
    return result


@router.delete("/{sid}")
def servers_delete(request: Request, sid: str) -> Dict[str, Any]:
    runtime = _runtime(request)
    manager = _manager(runtime)
    server = manager.get(sid) or {}
    result = manager.delete(sid)
    audit("servers.delete", target=str(server.get("name") or sid), ok=bool(result.get("ok")),
          detail=str(result.get("error", ""))[:200])
    return result


# --------------------------------------------------------------------------- #
#  Каталог задач
# --------------------------------------------------------------------------- #

@router.get("/tasks")
def servers_tasks(request: Request, sid: str = "") -> Dict[str, Any]:
    """Локализованный каталог задач (без текста скриптов)."""
    runtime = _runtime(request)
    manager = _manager(runtime)
    report = manager.last_report(sid) if sid else None
    data = task_catalog(report)
    return {"ok": True, **data}


# --------------------------------------------------------------------------- #
#  Проверка и выполнение
# --------------------------------------------------------------------------- #

@router.post("/{sid}/check")
def servers_check(request: Request, sid: str) -> Dict[str, Any]:
    """Синхронная проверка доступности и инвентаря (timeout 30 с)."""
    runtime = _runtime(request)
    manager = _manager(runtime)
    server, error = _server_or_error(manager, sid)
    if error:
        audit("servers.check", target=sid, ok=False, detail="not found")
        return error
    if not _remote_allowed(runtime.cfg):
        audit("servers.check", target=str(server.get("name") or sid), ok=False, detail="disabled")
        return _fail(t("servers.disabled"))

    result = check_server(runtime.cfg, server, timeout=30)
    if result.get("ok"):
        manager.remember_report(sid, result)
    audit("servers.check", target=str(server.get("name") or sid), ok=bool(result.get("ok")),
          detail=str(result.get("error", ""))[:200])
    return result


@router.post("/{sid}/run")
def servers_run(request: Request, sid: str, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """Запускает набор задач: dry_run возвращает скрипт, иначе создаёт задачу."""
    runtime = _runtime(request)
    manager = _manager(runtime)
    data = _body(body)
    server, error = _server_or_error(manager, sid)
    if error:
        audit("servers.run", target=sid, ok=False, detail="not found")
        return error

    ids = _task_ids(data)
    check = validate_params(ids, _body(data.get("params")))
    if not check["ok"]:
        audit("servers.run", target=str(server.get("name") or sid), ok=False,
              detail=str(check.get("error", ""))[:200])
        return check

    try:
        script = build_script(server, ids, check["values"], runtime.cfg,
                              report=manager.last_report(sid))
    except ValueError as exc:
        return _fail(str(exc))

    if data.get("dry_run"):
        audit("servers.run", target=str(server.get("name") or sid), ok=True,
              detail="dry-run: " + ", ".join(ids))
        return {"ok": True, "script": script, "preview": True}

    if not _remote_allowed(runtime.cfg):
        audit("servers.run", target=str(server.get("name") or sid), ok=False, detail="disabled")
        return _fail(t("servers.disabled"))

    selected = select_tasks(ids)
    title = t("servers.jobRun", name=server.get("name") or server.get("host") or "",
              count=len(selected))
    result = submit_script_job(
        runtime.cfg, runtime.jobs, server, script, title,
        meta={"server": sid, "tasks": [task["id"] for task in selected]},
    )
    audit("servers.run", target=str(server.get("name") or sid), ok=bool(result.get("ok")),
          detail=", ".join(task["id"] for task in selected)[:200])
    return result


@router.post("/{sid}/exec")
def servers_exec(request: Request, sid: str, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """Свободная команда на сервере (риск высокий, UI подтверждает)."""
    runtime = _runtime(request)
    manager = _manager(runtime)
    data = _body(body)
    server, error = _server_or_error(manager, sid)
    if error:
        audit("servers.exec", target=sid, ok=False, detail="not found")
        return error

    command = str(data.get("command") or "").strip()
    if not command:
        return _fail(t("servers.emptyCommand"))
    if len(command) > MAX_COMMAND:
        return _fail(t("servers.commandTooLong", limit=MAX_COMMAND))
    if not _remote_allowed(runtime.cfg):
        audit("servers.exec", target=str(server.get("name") or sid), ok=False, detail="disabled")
        return _fail(t("servers.disabled"))

    title = t("servers.jobExec", name=server.get("name") or server.get("host") or "")
    result = submit_command_job(runtime.cfg, runtime.jobs, server, command, title,
                                meta={"server": sid, "command": command[:200]})
    audit("servers.exec", target=str(server.get("name") or sid), ok=bool(result.get("ok")),
          detail=command[:200])
    return result


@router.post("/{sid}/playbook")
def servers_playbook(request: Request, sid: str, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """Ansible-плейбук с теми же задачами (генерация локальная)."""
    runtime = _runtime(request)
    manager = _manager(runtime)
    data = _body(body)
    server, error = _server_or_error(manager, sid)
    if error:
        audit("servers.playbook", target=sid, ok=False, detail="not found")
        return error

    ids = _task_ids(data)
    check = validate_params(ids, _body(data.get("params")))
    if not check["ok"]:
        audit("servers.playbook", target=str(server.get("name") or sid), ok=False,
              detail=str(check.get("error", ""))[:200])
        return check

    try:
        text = build_playbook(server, ids, check["values"], runtime.cfg,
                             report=manager.last_report(sid))
    except ValueError as exc:
        return _fail(str(exc))

    audit("servers.playbook", target=str(server.get("name") or sid), ok=True,
          detail=", ".join(ids)[:200])
    filename = f"{server.get('id') or 'servers'}.yml"
    return {"ok": True, "yaml": text, "filename": filename}

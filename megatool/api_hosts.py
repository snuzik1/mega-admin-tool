"""HTTP API мониторинга серверов.

Инвентарь хранится в секции `hosts` файла config.json, а состояние
(uptime/простой/выключение) — в `hosts_state.json` (см. megatool/hosts.py).
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Body, Request

from .changes import audit
from .i18n import t

router = APIRouter(prefix="/api")


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


@router.get("/hosts")
def hosts_get(request: Request) -> Dict[str, Any]:
    manager = request.app.state.rt.hosts
    return _ok(manager.snapshot())


@router.put("/hosts")
def hosts_put(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    manager = request.app.state.rt.hosts
    _items, error = manager.save(body.get("hosts"))
    if error:
        audit("hosts.update", target="validation", ok=False, detail=error)
        return _fail(error)
    snapshot = manager.snapshot()
    audit("hosts.update", target=f"{len(snapshot.get('items', []))} hosts", ok=True)
    return _ok(snapshot)


@router.post("/hosts/run")
def hosts_run(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    manager = request.app.state.rt.hosts
    host_id = str(body.get("id") or "").strip()
    if host_id:
        result = manager.run(host_id)
        if result is None:
            return _fail(t("hosts.notFound"))
        audit("hosts.run", target=host_id, ok=True)
        return _ok(result)
    audit("hosts.run", target="all", ok=True)
    return _ok(manager.run_all())

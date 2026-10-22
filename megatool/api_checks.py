"""HTTP API проверок доступности.

Конфигурация хранится в секции `checks`, результаты и история — в памяти
менеджера проверок (см. megatool/checks.py).
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


@router.get("/checks")
def checks_get(request: Request) -> Dict[str, Any]:
    manager = request.app.state.rt.checks
    return _ok(manager.snapshot())


@router.put("/checks")
def checks_put(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    manager = request.app.state.rt.checks
    raw = body.get("checks")
    _items, error = manager.save(raw)
    if error:
        audit("checks.update", target="validation", ok=False, detail=error)
        return _fail(error)
    audit("checks.update", target=f"{len(manager.snapshot().get('items', []))} checks", ok=True)
    return _ok(manager.snapshot())


@router.post("/checks/run")
def checks_run(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    manager = request.app.state.rt.checks
    check_id = str(body.get("id") or "").strip()
    if check_id:
        result = manager.run(check_id)
        if result is None:
            return _fail(t("checks.notFound"))
        audit("checks.run", target=check_id, ok=True)
        return _ok(result)
    audit("checks.run", target="all", ok=True)
    return _ok(manager.run_all())

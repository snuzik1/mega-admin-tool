"""API удалённых агентов мониторинга."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Body, Request

from .agents import MAX_AGENTS, collect, fetch, normalize_url, pick_metrics
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


@router.get("/agents")
def agents_list(request: Request, force: bool = False) -> Dict[str, Any]:
    runtime = request.app.state.rt
    return _ok(collect(runtime.cfg, force=force))


@router.post("/agents/test")
def agents_test(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """Проверяет агента по адресу или по id сохранённого (тогда берём его токен)."""
    runtime = request.app.state.rt
    agent_id = str(body.get("id") or "").strip()
    url = str(body.get("url") or "").strip()
    token = str(body.get("token") or "")
    if agent_id:
        saved = next((item for item in (runtime.cfg.get("agents", []) or [])
                      if isinstance(item, dict) and str(item.get("id")) == agent_id), None)
        if not saved:
            return _fail(t("agents.notFound"))
        if not url:
            url = str(saved.get("url") or "")
        if not token:
            token = str(saved.get("token") or "")
    if len(runtime.cfg.get("agents", []) or []) >= MAX_AGENTS and not agent_id:
        return _fail(t("agents.tooMany", limit=MAX_AGENTS))
    target = normalize_url(url)
    if not target:
        return _fail(t("agents.badUrl"))
    try:
        payload = fetch(target, token)
    except Exception as exc:
        return _fail(t("agents.unreachable", error=str(exc)[:180]))
    return _ok(pick_metrics(payload), url=target)

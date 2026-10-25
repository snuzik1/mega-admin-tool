"""HTTP-аутентификация: вход, выход и статус сессии.

Cookie: HttpOnly + SameSite=Strict. CSRF дополнительно закрыт проверкой
Origin для изменяющих запросов в server.py. Сессии хранятся в памяти
процесса; после перезапуска MegaTool требуется повторный вход.
"""

from __future__ import annotations

import os
from typing import Any, Dict
from urllib.parse import parse_qs

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from .auth import COOKIE_NAME, SESSION_TTL_SECONDS, AuthManager
from .changes import audit
from .i18n import t

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _manager(request: Request) -> AuthManager:
    return request.app.state.auth


def _client(request: Request) -> str:
    return (request.client.host if request.client else "") or "unknown"


def _secure_cookie(request: Request) -> bool:
    if os.environ.get("MEGATOOL_SECURE_COOKIE", "") == "1":
        return True
    return request.url.scheme == "https"


async def _payload(request: Request) -> Dict[str, Any]:
    """JSON или application/x-www-form-urlencoded (удобно для curl)."""
    content_type = request.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        try:
            body = await request.json()
        except Exception:
            return {}
        return body if isinstance(body, dict) else {}
    try:
        raw = (await request.body()).decode("utf-8", errors="replace")
        parsed = parse_qs(raw, keep_blank_values=True)
    except Exception:
        return {}
    return {key: values[0] if values else "" for key, values in parsed.items()}


@router.get("/status")
async def auth_status(request: Request) -> Dict[str, Any]:
    manager = _manager(request)
    authenticated = bool(manager.configured and manager.authenticate(request.cookies.get(COOKIE_NAME)))
    return {
        "ok": True,
        "data": {
            "configured": manager.configured,
            "authenticated": authenticated,
            "username": manager.username if authenticated else "",
            "auth_required": True,
        },
    }


@router.post("/login")
async def auth_login(request: Request, response: Response):
    manager = _manager(request)
    if not manager.configured:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "error": t("auth.notConfigured", path=manager.path)},
        )

    key = _client(request)
    retry = manager.limiter.retry_after(key)
    if retry:
        return JSONResponse(
            status_code=429,
            content={"ok": False, "error": t("auth.tooMany"), "retry_after": retry},
            headers={"Retry-After": str(retry)},
        )

    payload = await _payload(request)
    username = str(payload.get("username") or manager.username)
    password = str(payload.get("password") or "")

    if not manager.verify(username, password):
        manager.limiter.fail(key)
        audit("auth.login", target=username, detail=f"ip={key}", ok=False)
        return JSONResponse(
            status_code=401,
            content={"ok": False, "error": t("auth.badCredentials")},
        )

    manager.limiter.reset(key)
    token = manager.sessions.create()
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="strict",
        secure=_secure_cookie(request),
        path="/",
    )
    audit("auth.login", target=username, detail=f"ip={key}", ok=True)
    return {"ok": True, "data": {"username": manager.username}}


@router.post("/logout")
async def auth_logout(request: Request, response: Response) -> Dict[str, Any]:
    manager = _manager(request)
    token = request.cookies.get(COOKIE_NAME)
    if token:
        manager.sessions.revoke(token)
    response.delete_cookie(COOKIE_NAME, path="/")
    audit("auth.logout", target=manager.username, detail=f"ip={_client(request)}", ok=True)
    return {"ok": True}

"""HTTP API пользователей, пресетов прав и входа.

Пароли принимаются только при создании/смене и никогда не возвращаются.
Сессии живут в памяти процесса; после перезапуска MegaTool нужно войти снова.
Мастер-токен из настроек всегда действует как администратор.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request

from . import localauth
from .access import SECTIONS
from .changes import audit
from .i18n import t
from .security import is_loopback

router = APIRouter(prefix="/api")


def _ok(data: Any = None, **extra: Any) -> dict[str, Any]:
    payload = {"ok": True}
    if data is not None:
        payload["data"] = data
    payload.update(extra)
    return payload


def _fail(message: str, **extra: Any) -> dict[str, Any]:
    payload = {"ok": False, "error": message}
    payload.update(extra)
    return payload


def _provided_token(request: Request) -> str:
    return (request.headers.get("x-megatool-token")
            or request.headers.get("x-megapet-token") or "")


def _user_public(principal: dict[str, Any]) -> dict[str, Any] | None:
    if principal.get("kind") != "user":
        return None
    return {
        "id": principal.get("user_id", ""),
        "username": principal.get("username", ""),
        "display_name": principal.get("display_name", ""),
        "preset_id": principal.get("preset_id", ""),
        "preset_name": principal.get("preset_name", ""),
        "owner": bool(principal.get("owner", False)),
        "local_account": principal.get("local_account", ""),
        "local_role": principal.get("local_role", ""),
    }


def _request_is_local(request: Request) -> bool:
    client = request.client.host if request.client else "local"
    return client in ("local", "testclient") or is_loopback(client)


def _auth_payload(
    token: str,
    principal: dict[str, Any] | None,
    auth_enabled: bool,
) -> dict[str, Any]:
    return {
        "token": token,
        "auth_enabled": auth_enabled,
        "mode": "user",
        "user": _user_public(principal or {}),
        "permissions": (principal or {}).get("permissions", {}),
        "sections": list(SECTIONS),
    }


# --------------------------------------------------------------------------- #
#  Вход / текущий пользователь
# --------------------------------------------------------------------------- #

@router.post("/auth/login")
def auth_login(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    runtime = request.app.state.rt
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    client = request.client.host if request.client else "local"
    token, principal, error = runtime.access.login(username, password, client)
    if error:
        audit("auth.login", target=username[:64], ok=False, detail=error[:160])
        return _fail(error)
    audit("auth.login", target=username[:64], ok=True)
    return _ok(_auth_payload(token, principal, runtime.auth.enabled))


@router.get("/auth/setup-status")
def auth_setup_status(request: Request) -> dict[str, Any]:
    """Публичный статус первого локального администратора."""
    runtime = request.app.state.rt
    users = runtime.access.user_count()
    account = localauth.current_account() if not users else None
    privileged = localauth.is_privileged(account)
    local = _request_is_local(request)
    available = not users and privileged and local
    reason = ""
    if users:
        reason = "done"
    elif not privileged:
        reason = "not_root"
    elif not local:
        reason = "not_local"
    data = {
        "setup_required": bool((runtime.auth.enabled
                                or runtime.cfg.get("auth.local_only", False))
                               and not users),
        "setup_available": available,
        "auth_enabled": runtime.auth.enabled,
        "local_only": bool(runtime.cfg.get("auth.local_only", False)),
        "users": users,
        "reason": reason,
    }
    if available:
        data.update({
            "os_user": (account or {}).get("username", ""),
            "os_role": localauth.display_role(account),
        })
    return _ok(data)


@router.post("/auth/setup")
def auth_setup(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """Первый вход: текущий root/администратор задаёт пароль MegaTool."""
    runtime = request.app.state.rt
    if not _request_is_local(request):
        return _fail(t("access.setupNeedsLocal"))
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    state, error = runtime.access.bootstrap_local_admin(username, password)
    if error:
        audit("auth.setup", target=username[:64], ok=False, detail=error[:160])
        return _fail(error)
    runtime.cfg.set("auth.enabled", True)
    runtime.cfg.set("auth.local_only", True)
    runtime.cfg.save()
    created = (state.get("users") or [{}])[0]
    actual_username = str(created.get("username") or username)
    client = request.client.host if request.client else "local"
    token, principal, login_error = runtime.access.login(actual_username, password, client)
    if login_error:
        audit("auth.setup", target=actual_username[:64], ok=False, detail=login_error[:160])
        return _fail(login_error)
    audit("auth.setup", target=actual_username[:64], ok=True)
    return _ok(_auth_payload(token, principal, True))


@router.post("/auth/password")
def auth_password(
    request: Request, body: dict[str, Any] = Body(default={})
) -> dict[str, Any]:
    """Смена собственного пароля: обязателен старый пароль MegaTool."""
    runtime = request.app.state.rt
    principal = getattr(request.state, "principal", None)
    if not principal or principal.get("kind") != "user":
        return _fail(t("access.passwordUserOnly"))
    client = request.client.host if request.client else "local"
    token, refreshed, error = runtime.access.change_password(
        str(principal.get("user_id") or ""),
        str(body.get("current_password") or ""),
        str(body.get("new_password") or ""),
        client,
    )
    if error:
        audit("auth.password", target=str(principal.get("username") or ""), ok=False,
              detail=error[:160])
        return _fail(error)
    audit("auth.password", target=str(principal.get("username") or ""), ok=True)
    return _ok(_auth_payload(token, refreshed, runtime.auth.enabled))


@router.post("/auth/logout")
def auth_logout(request: Request) -> dict[str, Any]:
    runtime = request.app.state.rt
    token = _provided_token(request)
    if token:
        runtime.access.logout(token)
    return _ok({"logged_out": True})


@router.get("/auth/me")
def auth_me(request: Request) -> dict[str, Any]:
    runtime = request.app.state.rt
    principal = getattr(request.state, "principal", None) or runtime.access.local_principal()
    return _ok({
        "auth_enabled": runtime.auth.enabled,
        "mode": principal.get("kind", "local"),
        "user": _user_public(principal),
        "permissions": principal.get("permissions", {}),
        "sections": list(SECTIONS),
    })


# --------------------------------------------------------------------------- #
#  Пользователи и пресеты
# --------------------------------------------------------------------------- #

@router.get("/access")
def access_get(request: Request) -> dict[str, Any]:
    runtime = request.app.state.rt
    return _ok(runtime.access.state())


@router.put("/access/presets")
def access_presets_put(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    runtime = request.app.state.rt
    principal = getattr(request.state, "principal", None)
    state, error = runtime.access.save_presets(body.get("presets"), principal)
    if error:
        audit("access.presets", target="validation", ok=False, detail=error[:200])
        return _fail(error)
    audit("access.presets", target=f"{len(state.get('presets', []))} presets", ok=True)
    return _ok(state)


@router.put("/access/users")
def access_users_put(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    runtime = request.app.state.rt
    principal = getattr(request.state, "principal", None)
    state, error = runtime.access.save_users(body.get("users"), principal)
    if error:
        audit("access.users", target="validation", ok=False, detail=error[:200])
        return _fail(error)
    audit("access.users", target=f"{len(state.get('users', []))} users", ok=True)
    return _ok(state)

"""FastAPI-приложение: API + статика интерфейса + WebSocket-потоки.

Один процесс, один порт: `/api/*` — данные, всё остальное — интерфейс из `web/`.
WebSocket-каналы: метрики в реальном времени и живой лог задач.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import queue
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import APP_DESCRIPTION, APP_NAME, __version__
from .agents import MAX_AGENTS, normalize_url
from .api import router
from .access import master_only, required_permission
from .api_access import router as access_router
from .api_agents import router as agents_router
from .api_bundles import router as bundles_router
from .api_checks import router as checks_router
from .api_hosts import router as hosts_router
from .api_servers import router as servers_router
from .config import Config, HOME_DIR
from .i18n import set_language, t
from .jobs import JobLimitError
from .runtime import build_runtime
from .security import is_loopback
from .util import iso

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

PUBLIC_API_PATHS = (
    "/api/health",
    "/api/auth/login",
    "/api/auth/setup",
    "/api/auth/setup-status",
    "/api/docs",
    "/api/openapi.json",
    "/api/redoc",
)

LOOPBACK_HOSTNAMES = {"127.0.0.1", "localhost", "::1", "[::1]"}

#: Максимальный размер тела запроса (файлы конфигов и импорт настроек — небольшие).
MAX_BODY_BYTES = 25 * 1024 * 1024

#: Защита от перебора токена: не более N ошибок за окно (секунды).
TOKEN_FAIL_WINDOW = 60.0
TOKEN_FAIL_LIMIT = 10

_token_failures: dict[str, deque[float]] = {}
_token_failures_lock = threading.Lock()


def _token_fail_count(client: str) -> int:
    """Число свежих ошибок токена для адреса; попутно чистит старые записи."""
    now = time.monotonic()
    with _token_failures_lock:
        items = _token_failures.setdefault(client, deque())
        while items and now - items[0] > TOKEN_FAIL_WINDOW:
            items.popleft()
        if not items:
            _token_failures.pop(client, None)
            return 0
        return len(items)


def _token_note_failure(client: str) -> None:
    with _token_failures_lock:
        _token_failures.setdefault(client, deque()).append(time.monotonic())


def _token_clear_failures(client: str) -> None:
    with _token_failures_lock:
        _token_failures.pop(client, None)


def _host_header_name(value: str) -> str:
    """Вытаскивает имя хоста из заголовка Host (без порта, понимает IPv6)."""
    value = (value or "").strip()
    if value.startswith("["):  # [::1]:8799
        return value[1:].split("]", 1)[0]
    if value.count(":") == 1:  # host:port
        return value.rsplit(":", 1)[0]
    return value


def _allowed_hostnames(cfg: Config) -> set[str]:
    names = {str(item).strip().lower() for item in (cfg.get("server.allowed_hosts", []) or []) if str(item).strip()}
    names |= LOOPBACK_HOSTNAMES
    bind_host = str(cfg.get("server.host", "127.0.0.1") or "127.0.0.1").lower()
    if bind_host:
        names.add(bind_host)
    return names


def _host_allowed(host_header: str, cfg: Config) -> bool:
    """Анти-DNS-rebinding: при прослушивании только loopback принимаем запросы
    лишь с loopback-имён Host (плюс явно разрешённые server.allowed_hosts).

    Если сервер сознательно открыт наружу (host не loopback), ограничение снимается.
    """
    bind_host = str(cfg.get("server.host", "127.0.0.1") or "127.0.0.1")
    if not is_loopback(bind_host):
        return True
    name = _host_header_name(host_header).lower()
    if not name:
        return False
    return name in _allowed_hostnames(cfg)


def _origin_allowed(origin: str, cfg: Config) -> bool:
    """Проверяет Origin у изменяющих запросов (когда браузер его присылает)."""
    try:
        parts = urlsplit(origin)
    except ValueError:
        return False
    name = (parts.hostname or "").lower()
    if not name:
        return False
    bind_host = str(cfg.get("server.host", "127.0.0.1") or "127.0.0.1")
    if not is_loopback(bind_host):
        return True
    return name in _allowed_hostnames(cfg)


def _ws_origin_allowed(websocket: WebSocket, cfg: Config) -> bool:
    """Не даём чужим сайтам читать WebSocket-потоки (CSWSH).

    Браузер всегда присылает Origin при открытии сокета. Не-браузерные
    клиенты могут его не присылать — для них остаются Host и токен.
    """
    origin = websocket.headers.get("origin", "")
    return not origin or _origin_allowed(origin, cfg)


#: Заголовки безопасности для всех HTTP-ответов (включая статику и ошибки).
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; "
        "form-action 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; font-src 'self'; connect-src 'self' ws: wss:; "
        "manifest-src 'self'"
    ),
}


class NoCacheStaticFiles(StaticFiles):
    """Статика интерфейса без эвристического кэша.

    Иначе браузер «запоминает» JS/CSS надолго: после обновления MegaTool
    пользователь ещё долго видит старый интерфейс. `no-cache` заставляет
    браузер перепроверять файлы (304 при их неизменности) — быстро и безопасно.
    """

    def file_response(self, *args, **kwargs):  # pragma: no cover - отдаёт браузер
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or Config()
    set_language(cfg.get("ui.language", "ru"))
    runtime = build_runtime(cfg)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime.collector.start()
        runtime.checks.start()
        runtime.hosts.start()
        started = await runtime.balancers.autostart()
        if started:
            runtime.notes.append({"key": "notes.balancers", "params": {"names": ", ".join(started)}})
        try:
            yield
        finally:
            await runtime.balancers.stop_all()
            runtime.hosts.stop()
            runtime.checks.stop()
            runtime.collector.stop()
            runtime.jobs.cancel_all()

    app = FastAPI(
        title=APP_NAME,
        description=APP_DESCRIPTION,
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.rt = runtime

    # ------------------------------------------------------------------ #
    #  Проверка токена (включается в настройках, по умолчанию выключена)
    # ------------------------------------------------------------------ #
    @app.middleware("http")
    async def token_guard(request, call_next):
        path = request.url.path
        if path.startswith("/api") and not path.startswith(PUBLIC_API_PATHS):
            if not runtime.auth.enabled:
                request.state.principal = runtime.access.local_principal()
            else:
                client = request.client.host if request.client else "local"
                if _token_fail_count(client) >= TOKEN_FAIL_LIMIT:
                    return JSONResponse(
                        status_code=429,
                        content={"ok": False, "error": t("server.tooManyAttempts")},
                        headers={"Retry-After": str(int(TOKEN_FAIL_WINDOW))},
                    )
                # В query токен принимаем только для WebSocket: браузер не умеет
                # отправлять заголовки при открытии сокета. Для HTTP — только заголовок,
                # чтобы токен не оседал в истории браузера и журналах прокси.
                provided = (request.headers.get("x-megatool-token")
                            or request.headers.get("x-megapet-token"))  # совместимость со старым именем
                if not provided and path.startswith("/api/ws/"):
                    provided = request.query_params.get("token")
                if runtime.auth.check(provided):
                    principal = runtime.access.master_principal()
                else:
                    principal = runtime.access.authenticate(provided)
                if not principal:
                    _token_note_failure(client)
                    return JSONResponse(status_code=401,
                                        content={"ok": False, "error": t("server.tokenRequired")})
                _token_clear_failures(client)
                if master_only(path) and principal.get("kind") not in ("master", "local"):
                    return JSONResponse(
                        status_code=403,
                        content={"ok": False, "error": t("server.forbidden")},
                    )
                required = required_permission(request.method, path)
                if required and not runtime.access.allowed(principal, *required):
                    return JSONResponse(
                        status_code=403,
                        content={"ok": False, "error": t("server.forbidden")},
                    )
                request.state.principal = principal
        return await call_next(request)

    # ------------------------------------------------------------------ #
    #  Защита от DNS-rebinding: Host и Origin должны указывать на этот же
    #  узел (проверка включается автоматически при прослушивании loopback).
    # ------------------------------------------------------------------ #
    @app.middleware("http")
    async def host_guard(request, call_next):
        path = request.url.path
        if path.startswith("/api"):
            host_header = request.headers.get("host", "")
            if not _host_allowed(host_header, cfg):
                return JSONResponse(status_code=421, content={
                    "ok": False,
                    "error": t("server.badHost", host=_host_header_name(host_header) or "?"),
                })
            origin = request.headers.get("origin", "")
            if origin and request.method not in ("GET", "HEAD", "OPTIONS") and not _origin_allowed(origin, cfg):
                return JSONResponse(status_code=403, content={
                    "ok": False,
                    "error": t("server.badOrigin", origin=origin),
                })
            try:
                length = int(request.headers.get("content-length", "0") or 0)
            except ValueError:
                length = 0
            if length > MAX_BODY_BYTES:
                return JSONResponse(status_code=413, content={
                    "ok": False,
                    "error": t("server.tooLarge", mb=MAX_BODY_BYTES // (1024 * 1024)),
                })
        return await call_next(request)

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        # Swagger UI грузит скрипты и стили с CDN, поэтому для страницы
        # документации политика чуть мягче; на само приложение это не влияет.
        if request.url.path.startswith("/api/docs"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; "
                "form-action 'self'; script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
                "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; img-src 'self' data: "
                "https://fastapi.tiangolo.com; connect-src 'self'; font-src 'self' https://cdn.jsdelivr.net"
            )
        return response

    app.include_router(router)
    app.include_router(access_router)
    app.include_router(agents_router)
    app.include_router(bundles_router)
    app.include_router(servers_router)
    app.include_router(checks_router)
    app.include_router(hosts_router)

    @app.exception_handler(JobLimitError)
    async def _job_limit_handler(_request, exc: JobLimitError):
        return JSONResponse(status_code=429, content={"ok": False, "error": str(exc)})

    # ------------------------------------------------------------------ #
    #  WebSocket: метрики
    # ------------------------------------------------------------------ #
    @app.websocket("/api/ws/metrics")
    async def ws_metrics(websocket: WebSocket) -> None:
        if not _host_allowed(websocket.headers.get("host", ""), cfg) or not _ws_origin_allowed(websocket, cfg):
            await websocket.close(code=4403)
            return
        principal = _ws_principal(runtime, websocket.query_params.get("token"))
        if not principal:
            await websocket.close(code=4401)
            return
        if not runtime.access.allowed(principal, "dashboard", "view"):
            await websocket.close(code=4403)
            return
        await websocket.accept()
        channel: queue.Queue = queue.Queue(maxsize=64)
        runtime.collector.subscribe(channel)
        try:
            await websocket.send_json({
                "type": "hello",
                "version": __version__,
                "interval_ms": runtime.collector.state.get("interval_ms", 2000),
                "state": runtime.collector.public_state(),
                "point": runtime.collector.latest,
                "alerts": runtime.collector.active_alerts(),
            })
            while True:
                payload = await asyncio.get_running_loop().run_in_executor(
                    None, functools.partial(_safe_get, channel, 2.0))
                if payload is None:
                    payload = {"type": "ping", "ts": iso()}
                await websocket.send_json(payload)
        except (WebSocketDisconnect, RuntimeError):
            pass
        except Exception:
            with contextlib.suppress(Exception):
                await websocket.close()
        finally:
            runtime.collector.unsubscribe(channel)

    # ------------------------------------------------------------------ #
    #  WebSocket: живой лог задачи
    # ------------------------------------------------------------------ #
    @app.websocket("/api/ws/jobs/{job_id}")
    async def ws_job(websocket: WebSocket, job_id: str) -> None:
        if not _host_allowed(websocket.headers.get("host", ""), cfg) or not _ws_origin_allowed(websocket, cfg):
            await websocket.close(code=4403)
            return
        principal = _ws_principal(runtime, websocket.query_params.get("token"))
        if not principal:
            await websocket.close(code=4401)
            return
        if not runtime.access.allowed(principal, "jobs", "view"):
            await websocket.close(code=4403)
            return
        job = runtime.jobs.get(job_id)
        await websocket.accept()
        if not job:
            await websocket.send_json({"type": "error", "error": t("server.jobNotFound")})
            await websocket.close()
            return
        channel = job.subscribe()
        try:
            await websocket.send_json({"type": "job", "job": job.to_dict()})
            while True:
                item = await asyncio.get_running_loop().run_in_executor(
                    None, functools.partial(_safe_get, channel, 2.0))
                if item is None:
                    await websocket.send_json({"type": "status", "job": job.to_dict(include_cmd=False)})
                    if job.status != "running":
                        break
                    continue
                await websocket.send_json({"type": "line", **item})
        except (WebSocketDisconnect, RuntimeError):
            pass
        except Exception:
            with contextlib.suppress(Exception):
                await websocket.close()
        finally:
            job.unsubscribe(channel)

    # ------------------------------------------------------------------ #
    #  Интерфейс
    # ------------------------------------------------------------------ #
    if WEB_DIR.is_dir():
        app.mount("/", NoCacheStaticFiles(directory=str(WEB_DIR), html=True), name="web")
    else:  # pragma: no cover
        @app.get("/")
        def missing_web() -> dict[str, str]:
            return {"ok": False, "error": t("server.noWebDir", path=str(WEB_DIR))}

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> JSONResponse:  # noqa: D401 - простой ответ, чтобы не 404
        return JSONResponse(status_code=204, content=None)

    runtime.notes.append({"key": "notes.home", "params": {"path": str(HOME_DIR)}})
    return app


def _safe_get(channel: queue.Queue, timeout: float):
    """Блокирующее чтение из очереди для run_in_executor (None = таймаут)."""
    try:
        return channel.get(timeout=timeout)
    except queue.Empty:
        return None


def _ws_principal(runtime, token: str | None) -> dict | None:
    """Проверяет токен WebSocket: мастер-токен или сессия пользователя."""
    if not runtime.auth.enabled:
        return runtime.access.local_principal()
    if runtime.auth.check(token):
        return runtime.access.master_principal()
    return runtime.access.authenticate(token)


#: Приложение для запуска через `uvicorn megatool.server:app` (в том числе с --reload).
app = create_app()

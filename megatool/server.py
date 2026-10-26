"""FastAPI-приложение: API + статика интерфейса + WebSocket-потоки.

Один процесс, один порт: `/api/*` — данные, всё остальное — интерфейс из `megatool/web/`.
WebSocket-каналы: метрики в реальном времени и живой лог задач.
"""

from __future__ import annotations
from typing import Dict, Optional, Set

import asyncio
import contextlib
import functools
import queue
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import APP_DESCRIPTION, APP_NAME, __version__
from .agents import MAX_AGENTS, normalize_url
from .api import router
from .api_agents import router as agents_router
from .api_auth import router as auth_router
from .api_bundles import router as bundles_router
from .api_checks import router as checks_router
from .api_hosts import router as hosts_router
from .api_servers import router as servers_router
from .auth import COOKIE_NAME, AuthManager, default_auth_path
from .config import Config, HOME_DIR
from .i18n import set_language, t
from .jobs import JobLimitError
from .runtime import build_runtime
from .security import is_loopback
from .util import iso

WEB_DIR = Path(__file__).resolve().parent / "web"

LOOPBACK_HOSTNAMES = {"127.0.0.1", "localhost", "::1", "[::1]"}

#: Максимальный размер тела запроса (файлы конфигов и импорт настроек — небольшие).
MAX_BODY_BYTES = 25 * 1024 * 1024

#: Ручки, доступные без сессии: проверка живости и сам вход.
PUBLIC_API_PATHS = frozenset({
    "/api/health",
    "/api/auth/status",
    "/api/auth/login",
})


def _host_header_name(value: str) -> str:
    """Вытаскивает имя хоста из заголовка Host (без порта, понимает IPv6)."""
    value = (value or "").strip()
    if value.startswith("["):  # [::1]:8799
        return value[1:].split("]", 1)[0]
    if value.count(":") == 1:  # host:port
        return value.rsplit(":", 1)[0]
    return value


def _host_header_port(value: str) -> Optional[int]:
    """Возвращает порт из Host; None — порт не указан, -1 — некорректный."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        return urlsplit(f"//{value}").port
    except ValueError:
        return -1


def _effective_port(scheme: str, port: Optional[int]) -> int:
    if port is not None:
        return port
    return 443 if scheme in ("https", "wss") else 80


def _allowed_hostnames(cfg: Config) -> Set[str]:
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


def _origin_allowed(origin: str, host_header: str) -> bool:
    """Проверяет Origin у изменяющих запросов (когда браузер его присылает).

    Origin должен совпадать с фактическим Host (включая порт), иначе другое
    локальное приложение на ``localhost`` могло бы выполнить CSRF-запрос.
    """
    try:
        parts = urlsplit(origin)
        origin_port = parts.port
    except ValueError:
        return False
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https", "ws", "wss"):
        return False
    name = (parts.hostname or "").lower()
    host_name = _host_header_name(host_header).lower()
    if not name or not host_name or name != host_name:
        return False
    host_port = _host_header_port(host_header)
    if host_port == -1:
        return False
    if _effective_port(scheme, origin_port) != _effective_port(scheme, host_port):
        return False
    return True


def _ws_origin_allowed(websocket: WebSocket) -> bool:
    """Не даём чужим сайтам читать WebSocket-потоки (CSWSH).

    Браузер всегда присылает Origin при открытии сокета. Не-браузерные
    клиенты могут его не присылать — для них остаётся проверка Host.
    """
    origin = websocket.headers.get("origin", "")
    return not origin or _origin_allowed(origin, websocket.headers.get("host", ""))


def _ws_authorized(websocket: WebSocket, auth: AuthManager) -> bool:
    """Проверяет сессию до accept: иначе поток метрик доступен без входа."""
    if not auth.configured:
        return False
    return auth.authenticate(websocket.cookies.get(COOKIE_NAME))


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


def create_app(cfg: Optional[Config] = None, auth: Optional[AuthManager] = None) -> FastAPI:
    cfg = cfg or Config()
    set_language(cfg.get("ui.language", "ru"))
    auth_manager = auth or AuthManager(default_auth_path(cfg.path))
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
    app.state.auth = auth_manager

    def _secure(response, path: str):
        """Добавляет заголовки безопасности (включая ответы-отказы)."""
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if path.startswith("/api"):
            # API может содержать конфиги, ключи и результаты команд:
            # не разрешаем браузеру и прокси кэшировать ответы.
            response.headers.setdefault("Cache-Control", "no-store")
        # Swagger UI грузит скрипты и стили с CDN, поэтому для страницы
        # документации политика чуть мягче; на само приложение это не влияет.
        if path.startswith("/api/docs"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; "
                "form-action 'self'; script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
                "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; img-src 'self' data: "
                "https://fastapi.tiangolo.com; connect-src 'self'; font-src 'self' https://cdn.jsdelivr.net"
            )
        return response

    # ------------------------------------------------------------------ #
    #  Один проход по API-запросам: защита от DNS-rebinding (Host/Origin),
    #  ограничение размера тела и заголовки безопасности для всех ответов.
    # ------------------------------------------------------------------ #
    @app.middleware("http")
    async def security_guard(request, call_next):
        path = request.url.path
        if path.startswith("/api"):
            host_header = request.headers.get("host", "")
            if not _host_allowed(host_header, cfg):
                return _secure(JSONResponse(status_code=421, content={
                    "ok": False,
                    "error": t("server.badHost", host=_host_header_name(host_header) or "?"),
                }), path)
            origin = request.headers.get("origin", "")
            if origin and request.method not in ("GET", "HEAD", "OPTIONS") and not _origin_allowed(origin, host_header):
                return _secure(JSONResponse(status_code=403, content={
                    "ok": False,
                    "error": t("server.badOrigin", origin=origin),
                }), path)
            try:
                length = int(request.headers.get("content-length", "0") or 0)
            except ValueError:
                length = 0
            if length > MAX_BODY_BYTES:
                return _secure(JSONResponse(status_code=413, content={
                    "ok": False,
                    "error": t("server.tooLarge", mb=MAX_BODY_BYTES // (1024 * 1024)),
                }), path)

            normalized = path.rstrip("/") or "/"
            if normalized not in PUBLIC_API_PATHS:
                if not auth_manager.configured:
                    return _secure(JSONResponse(status_code=503, content={
                        "ok": False,
                        "error": t("auth.notConfiguredShort"),
                    }), path)
                if not auth_manager.authenticate(request.cookies.get(COOKIE_NAME)):
                    return _secure(JSONResponse(
                        status_code=401,
                        content={"ok": False, "error": t("auth.required")},
                        headers={"WWW-Authenticate": "Cookie"},
                    ), path)
                request.state.auth_user = auth_manager.username
        return _secure(await call_next(request), path)

    app.include_router(router)
    app.include_router(auth_router)
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
        if not _host_allowed(websocket.headers.get("host", ""), cfg) or not _ws_origin_allowed(websocket):
            await websocket.close(code=4403)
            return
        if not _ws_authorized(websocket, auth_manager):
            await websocket.close(code=4401)
            return
        session_token = websocket.cookies.get(COOKIE_NAME)
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
                if not auth_manager.authenticate(session_token):
                    await websocket.close(code=4401)
                    break
                payload = await asyncio.get_running_loop().run_in_executor(
                    None, functools.partial(_safe_get, channel, 2.0))
                if not auth_manager.authenticate(session_token):
                    await websocket.close(code=4401)
                    break
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
        if not _host_allowed(websocket.headers.get("host", ""), cfg) or not _ws_origin_allowed(websocket):
            await websocket.close(code=4403)
            return
        if not _ws_authorized(websocket, auth_manager):
            await websocket.close(code=4401)
            return
        session_token = websocket.cookies.get(COOKIE_NAME)
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
                if not auth_manager.authenticate(session_token):
                    await websocket.close(code=4401)
                    break
                item = await asyncio.get_running_loop().run_in_executor(
                    None, functools.partial(_safe_get, channel, 2.0))
                if not auth_manager.authenticate(session_token):
                    await websocket.close(code=4401)
                    break
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
        def missing_web() -> Dict[str, str]:
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


#: Приложение для запуска через `uvicorn megatool.server:app` (в том числе с --reload).
app = create_app()

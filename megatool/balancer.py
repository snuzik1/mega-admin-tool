"""Балансировщик нагрузки: встроенный HTTP/TCP-прокси + экспорт конфигов.

Две задачи модуля:

1. **Рабочий балансировщик.** Асинхронный прокси на stdlib (asyncio), который
   распределяет трафик между бэкендами по выбранному алгоритму, сам проверяет
   их здоровье (TCP-коннект или HTTP-запрос) и собирает статистику: RPS,
   активные соединения, задержки (p95), ошибки по каждому бэкенду.
   Он же отдаёт данные для графиков в интерфейсе.

2. **Экспорт конфигурации.** Из той же модели целей генерируются готовые блоки
   для nginx (upstream/server) и haproxy (backend/frontend), которые можно
   проверить (`nginx -t`) и применить на реальном сервере.

Алгоритмы: round_robin (взвешенный «плавный»), weighted, least_conn, ip_hash, random.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import CONFIGS_DIR, Config
from .i18n import t
from .util import ensure_dir, iso, rand_token, safe_id


#: Ключи подписей алгоритмов балансировки для i18n.
_ALGORITHM_KEYS = {
    "round_robin": "balancer.algoRoundRobin",
    "least_conn": "balancer.algoLeastConn",
    "weighted": "balancer.algoWeighted",
    "ip_hash": "balancer.algoIpHash",
    "random": "balancer.algoRandom",
}

#: Алгоритмы в виде обычного словаря (id → подпись); подписи обновляются под язык.
ALGORITHMS = {key: t(translation_key) for key, translation_key in _ALGORITHM_KEYS.items()}


def _refresh_algorithm_labels() -> None:
    """Переводит подписи алгоритмов на текущий язык (словарь отдаётся в API как есть)."""
    for key, translation_key in _ALGORITHM_KEYS.items():
        ALGORITHMS[key] = t(translation_key)


def _algorithm_label(algorithm: str) -> str:
    translation_key = _ALGORITHM_KEYS.get(algorithm)
    return t(translation_key) if translation_key else algorithm


MAX_HEAD = 64 * 1024
MAX_BODY = 32 * 1024 * 1024
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}


# --------------------------------------------------------------------------- #
#  Цель (бэкенд)
# --------------------------------------------------------------------------- #

@dataclass
class Target:
    host: str
    port: int
    weight: int = 1
    note: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    # runtime-состояние (не сохраняется в конфиг)
    healthy: bool | None = None
    checks: int = 0
    ok_checks: int = 0
    fails: int = 0
    latency_ms: float = 0.0
    latency_hist: deque[float] = field(default_factory=lambda: deque(maxlen=60))
    requests: int = 0
    errors: int = 0
    active: int = 0
    bytes_in: int = 0
    bytes_out: int = 0
    last_check: float = 0.0
    last_error: str = ""
    _cw: float = 0.0  # текущий вес для smooth weighted round-robin

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"

    def to_dict(self) -> dict[str, Any]:
        total = self.requests + self.errors
        return {
            "id": self.id,
            "host": self.host,
            "port": self.port,
            "weight": self.weight,
            "note": self.note,
            "address": self.address,
            "healthy": self.healthy,
            "checks": self.checks,
            "ok_checks": self.ok_checks,
            "fails": self.fails,
            "latency_ms": round(self.latency_ms, 1),
            "latency_hist": [round(v, 1) for v in self.latency_hist],
            "requests": self.requests,
            "errors": self.errors,
            "error_rate": round(self.errors / total * 100, 2) if total else 0.0,
            "active": self.active,
            "bytes_in": self.bytes_in,
            "bytes_out": self.bytes_out,
            "last_check": iso(self.last_check) if self.last_check else None,
            "last_error": self.last_error,
        }

    def to_config(self) -> dict[str, Any]:
        return {"id": self.id, "host": self.host, "port": self.port,
                "weight": self.weight, "note": self.note}


def _validate_target(data: dict[str, Any]) -> Target | str:
    host = str(data.get("host", "")).strip()
    if not host or len(host) > 253:
        return t("balancer.invalidHost")
    try:
        port = int(data.get("port", 80))
    except (TypeError, ValueError):
        return t("balancer.invalidPort")
    if not 1 <= port <= 65535:
        return t("balancer.portRange")
    try:
        weight = max(1, min(100, int(data.get("weight", 1))))
    except (TypeError, ValueError):
        weight = 1
    return Target(host=host, port=port, weight=weight, note=str(data.get("note", ""))[:120],
                  id=safe_id(str(data.get("id", "")), "") or uuid.uuid4().hex[:8])


# --------------------------------------------------------------------------- #
#  Балансировщик
# --------------------------------------------------------------------------- #

class Balancer:
    def __init__(self, manager: "BalancerManager", data: dict[str, Any]) -> None:
        self.manager = manager
        self.name = safe_id(str(data.get("name", "")), "") or f"balancer-{uuid.uuid4().hex[:4]}"
        self.listen_host = str(data.get("listen_host", "127.0.0.1") or "127.0.0.1")
        # Порт 0 — осознанный выбор «занять свободный порт автоматически»,
        # поэтому подставляем значение по умолчанию только для пустых данных.
        raw_port = data.get("listen_port", 8080)
        try:
            self.listen_port = 8080 if raw_port in (None, "") else int(raw_port)
        except (TypeError, ValueError):
            self.listen_port = 8080
        if not 0 <= self.listen_port <= 65535:
            self.listen_port = 8080
        self.algorithm = str(data.get("algorithm", "round_robin"))
        if self.algorithm not in ALGORITHMS:
            self.algorithm = "round_robin"
        self.note = str(data.get("note", ""))[:200]
        self.autostart = bool(data.get("autostart", False))
        health = data.get("health", {}) or {}
        self.health = {
            "enabled": bool(health.get("enabled", True)),
            "mode": str(health.get("mode", "tcp")),        # tcp | http
            "path": str(health.get("path", "/") or "/"),
            "interval_s": max(2, int(health.get("interval_s", 10) or 10)),
            "timeout_s": max(1, min(30, int(health.get("timeout_s", 3) or 3))),
            "ok_status_max": int(health.get("ok_status_max", 499) or 499),
        }
        self.targets: list[Target] = []
        for item in data.get("targets", []) or []:
            target = _validate_target(item)
            if isinstance(target, Target):
                self.targets.append(target)

        self.running = False
        self.started_at: float | None = None
        self.error = ""
        self._server: asyncio.AbstractServer | None = None
        self._tasks: list[asyncio.Task] = []
        self._metrics: deque[dict[str, Any]] = deque(maxlen=900)
        self._latencies: deque[float] = deque(maxlen=300)
        self._last_counters: dict[str, int] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------ #
    def to_dict(self, with_stats: bool = True) -> dict[str, Any]:
        data = {
            "name": self.name,
            "listen_host": self.listen_host,
            "listen_port": self.listen_port,
            "listen": f"{self.listen_host}:{self.listen_port}",
            "algorithm": self.algorithm,
            "algorithm_label": _algorithm_label(self.algorithm),
            "note": self.note,
            "autostart": self.autostart,
            "health": self.health,
            "running": self.running,
            "started_at": iso(self.started_at) if self.started_at else None,
            "error": self.error,
            "targets": [t.to_dict() for t in self.targets] if with_stats else [t.to_config() for t in self.targets],
        }
        if with_stats:
            healthy = [t for t in self.targets if t.healthy]
            data["summary"] = {
                "targets": len(self.targets),
                "healthy": len(healthy),
                "unhealthy": len([t for t in self.targets if t.healthy is False]),
                "unknown": len([t for t in self.targets if t.healthy is None]),
                "requests": sum(t.requests for t in self.targets),
                "errors": sum(t.errors for t in self.targets),
                "active": sum(t.active for t in self.targets),
                "rps": self._metrics[-1]["rps"] if self._metrics else 0.0,
                "p95_ms": self._metrics[-1]["p95_ms"] if self._metrics else 0.0,
            }
            data["weight_total"] = sum(t.weight for t in self.targets) or 0
        return data

    def to_config(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "listen_host": self.listen_host,
            "listen_port": self.listen_port,
            "algorithm": self.algorithm,
            "note": self.note,
            "autostart": self.autostart,
            "health": self.health,
            "targets": [t.to_config() for t in self.targets],
        }

    def set_targets(self, targets: list[dict[str, Any]]) -> str | None:
        fresh: list[Target] = []
        for item in targets or []:
            target = _validate_target(item)
            if isinstance(target, str):
                return target
            previous = next((t for t in self.targets if t.id == target.id or t.address == target.address), None)
            if previous:  # сохраняем статистику при редактировании
                target.healthy = previous.healthy
                target.requests = previous.requests
                target.errors = previous.errors
                target.latency_hist = previous.latency_hist
                target.checks = previous.checks
                target.ok_checks = previous.ok_checks
                target.fails = previous.fails
            fresh.append(target)
        self.targets = fresh
        return None

    # ------------------------------------------------------------------ #
    #  Жизненный цикл
    # ------------------------------------------------------------------ #
    async def start(self) -> dict[str, Any]:
        if self.running:
            return {"ok": True, "note": t("balancer.alreadyRunning")}
        if not self.targets:
            return {"ok": False, "error": t("balancer.noTargets")}
        if self.listen_port and self.listen_port == int(self.manager.cfg.get("server.port", 8787)):
            return {"ok": False, "error": t("balancer.portConflictsMegatool")}
        self._loop = asyncio.get_running_loop()
        try:
            self._server = await asyncio.start_server(self.handle, self.listen_host, self.listen_port)
        except OSError as exc:
            self.error = f"{exc}"
            action = t("balancer.freePortHint") if self.listen_port else t("balancer.portBusy")
            return {"ok": False, "error": t(
                "balancer.bindFailed",
                address=f"{self.listen_host}:{self.listen_port}", error=exc, action=action)}
        # Порт 0 означает «любой свободный» — запоминаем фактический, чтобы UI показывал правду.
        if not self.listen_port and self._server.sockets:
            try:
                self.listen_port = int(self._server.sockets[0].getsockname()[1])
            except (OSError, IndexError):
                pass
        self.running = True
        self.started_at = time.time()
        self.error = ""
        self._tasks = []
        if self.health.get("enabled"):
            self._tasks.append(asyncio.create_task(self.health_loop()))
        self._tasks.append(asyncio.create_task(self.metrics_loop()))
        return {"ok": True, "listen": f"{self.listen_host}:{self.listen_port}", "port": self.listen_port}

    async def stop(self) -> dict[str, Any]:
        if not self.running:
            return {"ok": True, "note": t("balancer.notRunning")}
        self.running = False
        for task in self._tasks:
            task.cancel()
        self._tasks = []
        if self._server:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None
        for target in self.targets:
            target.active = 0
        return {"ok": True}

    # ------------------------------------------------------------------ #
    #  Выбор бэкенда
    # ------------------------------------------------------------------ #
    def healthy_targets(self) -> list[Target]:
        candidates = [t for t in self.targets if t.healthy is not False]
        if candidates:
            return candidates
        return []  # все известны как мёртвые — не льём трафик

    def pick(self, client_ip: str = "") -> Target | None:
        candidates = self.healthy_targets()
        if not candidates:
            return None
        if self.algorithm == "least_conn":
            return min(candidates, key=lambda t: (t.active + 1) / max(1, t.weight))
        if self.algorithm == "ip_hash":
            digest = hashlib.sha1(client_ip.encode("utf-8", "ignore")).hexdigest()
            index = int(digest[:8], 16) % len(candidates)
            return candidates[index]
        if self.algorithm == "random":
            weights = [max(1, t.weight) for t in candidates]
            return random.choices(candidates, weights=weights, k=1)[0]
        # smooth weighted round-robin (для round_robin и weighted)
        if self.algorithm == "weighted":
            weights = [max(1, t.weight) for t in candidates]
            return random.choices(candidates, weights=weights, k=1)[0]
        total = 0
        best: Target | None = None
        for target in candidates:
            target._cw += max(1, target.weight)
            total += max(1, target.weight)
            if best is None or target._cw > best._cw:
                best = target
        if best is not None:
            best._cw -= total
        return best

    # ------------------------------------------------------------------ #
    #  Прокси
    # ------------------------------------------------------------------ #
    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername") or ("", 0)
        client_ip = str(peer[0])
        started = time.perf_counter()
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=20)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError, ConnectionError):
            await self._close_writer(writer)
            return
        if len(head) > MAX_HEAD:
            await self._send_simple(writer, 431, "Request Header Fields Too Large")
            return

        head_text = head.decode("latin-1")
        lines = head_text.split("\r\n")
        request_line = lines[0]
        is_http = "HTTP/" in request_line
        if not is_http:
            await self._raw_tunnel(reader, writer, client_ip)
            return

        parts = request_line.split(" ", 2)
        method = parts[0] if parts else "GET"
        path = parts[1] if len(parts) > 1 else "/"
        headers: list[tuple[str, str]] = []
        for line in lines[1:]:
            if not line or ":" not in line:
                continue
            key, _, value = line.partition(":")
            headers.append((key.strip(), value.strip()))
        header_map = {k.lower(): v for k, v in headers}

        try:
            body = await asyncio.wait_for(self._read_request_body(reader, header_map), timeout=60)
        except Exception:
            await self._send_simple(writer, 400, "Bad Request")
            return

        last_error = ""
        for _ in range(3):  # до трёх попыток: упавший бэкенд не должен ломать запрос
            target = self.pick(client_ip)
            if target is None:
                await self._send_simple(writer, 503, "No healthy backends")
                return
            result = await self._forward(target, method, path, headers, header_map, body, writer, client_ip)
            if result is None:
                latency = (time.perf_counter() - started) * 1000
                self._latencies.append(latency)
                # Ответ отправлен целиком с Connection: close — закрываем клиента сами,
                # иначе сокет останется висеть до сборки мусора.
                await self._close_writer(writer)
                return
            last_error = result
        await self._send_simple(writer, 502, "Bad Gateway", detail=last_error)

    async def _read_request_body(self, reader: asyncio.StreamReader, header_map: dict[str, str]) -> bytes:
        if header_map.get("transfer-encoding", "").lower().startswith("chunked"):
            chunks: list[bytes] = []
            total = 0
            while True:
                size_line = await reader.readline()
                if not size_line:
                    break
                try:
                    size = int(size_line.strip().split(b";")[0], 16)
                except ValueError:
                    break
                if size == 0:
                    await reader.readline()
                    break
                chunk = await reader.readexactly(size)
                chunks.append(chunk)
                await reader.readline()
                total += len(chunk)
                if total > MAX_BODY:
                    break
            return b"".join(chunks)
        try:
            length = int(header_map.get("content-length", "0") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return b""
        if length > MAX_BODY:
            return b""
        try:
            return await reader.readexactly(length)
        except asyncio.IncompleteReadError as exc:
            return exc.partial

    async def _forward(self, target: Target, method: str, path: str, headers: list[tuple[str, str]],
                       header_map: dict[str, str], body: bytes,
                       client_writer: asyncio.StreamWriter, client_ip: str) -> str | None:
        timeout = self.health.get("timeout_s", 5) or 5
        start = time.perf_counter()
        target.requests += 1
        target.active += 1
        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(target.host, target.port), timeout=max(3, timeout))
        except Exception as exc:
            target.errors += 1
            target.active = max(0, target.active - 1)
            target.fails += 1
            if target.fails >= 3:
                target.healthy = False
            return str(exc)

        try:
            forward_headers = [
                (key, value) for key, value in headers
                if key.lower() not in HOP_BY_HOP or key.lower() == "transfer-encoding"
            ]
            if "host" not in header_map:
                forward_headers.append(("Host", f"{target.host}:{target.port}"))
            client_port = (client_writer.get_extra_info("peername") or ("", 0))[1]
            prior = header_map.get("x-forwarded-for", "")
            forward_headers.append(("X-Forwarded-For", f"{prior + ', ' if prior else ''}{client_ip}"))
            forward_headers.append(("X-Forwarded-Proto", "http"))
            forward_headers.append(("X-Real-IP", client_ip))
            forward_headers.append(("X-Forwarded-Port", str(client_port)))
            forward_headers.append(("Connection", "close"))
            if body:
                forward_headers = [(k, v) for k, v in forward_headers if k.lower() != "transfer-encoding"]
                forward_headers.append(("Content-Length", str(len(body))))
            else:
                forward_headers = [(k, v) for k, v in forward_headers if k.lower() != "content-length"]

            request = f"{method} {path} HTTP/1.1\r\n".encode("latin-1")
            request += b"".join(f"{k}: {v}\r\n".encode("latin-1") for k, v in forward_headers)
            request += b"\r\n"
            upstream_writer.write(request)
            if body:
                upstream_writer.write(body)
                target.bytes_out += len(body)
            await upstream_writer.drain()

            # Ответ: статус-строка + заголовки, затем тело (потоком до закрытия соединения)
            head = await asyncio.wait_for(upstream_reader.readuntil(b"\r\n\r\n"), timeout=30)
            status_line = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
            code = 502
            parts = status_line.split(" ")
            if len(parts) >= 2 and parts[1].isdigit():
                code = int(parts[1])
            response_headers = []
            for line in head.decode("latin-1").split("\r\n")[1:]:
                if not line or ":" not in line:
                    continue
                key, _, value = line.partition(":")
                if key.strip().lower() in HOP_BY_HOP:
                    continue
                response_headers.append((key.strip(), value.strip()))
            response_headers.append(("Connection", "close"))
            response_headers.append(("X-Balancer", self.name))
            response_headers.append(("X-Backend", target.address))

            out = status_line.encode("latin-1") + b"\r\n"
            out += b"".join(f"{k}: {v}\r\n".encode("latin-1") for k, v in response_headers)
            out += b"\r\n"
            client_writer.write(out)

            sent = len(out)
            while True:
                chunk = await asyncio.wait_for(upstream_reader.read(65536), timeout=120)
                if not chunk:
                    break
                client_writer.write(chunk)
                sent += len(chunk)
                await client_writer.drain()
            target.bytes_in += sent
            await client_writer.drain()

            latency = (time.perf_counter() - start) * 1000
            target.latency_ms = latency
            target.latency_hist.append(latency)
            target.checks += 1
            target.ok_checks += 1
            target.fails = 0
            if code >= 500:
                target.errors += 1
                target.healthy = target.healthy is not False  # 5xx не «убивает» бэкенд сразу
            else:
                target.healthy = True
            return None
        except Exception as exc:
            target.errors += 1
            target.fails += 1
            target.last_error = str(exc)[:200]
            if target.fails >= 3:
                target.healthy = False
            return f"{type(exc).__name__}: {exc}"
        finally:
            target.active = max(0, target.active - 1)
            try:
                upstream_writer.close()
            except Exception:
                pass

    async def _raw_tunnel(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, client_ip: str) -> None:
        """Прозрачный TCP-туннель: для протоколов, которые мы не разбираем (TLS и др.)."""
        target = self.pick(client_ip)
        if target is None:
            await self._send_simple(writer, 503, "No healthy backends")
            return
        target.requests += 1
        target.active += 1
        try:
            up_reader, up_writer = await asyncio.open_connection(target.host, target.port)
        except Exception as exc:
            target.errors += 1
            target.last_error = str(exc)[:200]
            await self._send_simple(writer, 502, "Bad Gateway")
            return

        async def pump(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            try:
                while True:
                    data = await src.read(65536)
                    if not data:
                        break
                    dst.write(data)
                    await dst.drain()
            except Exception:
                pass
            finally:
                try:
                    dst.close()
                except Exception:
                    pass

        try:
            await asyncio.gather(pump(reader, up_writer), pump(up_reader, writer))
        finally:
            target.active = max(0, target.active - 1)

    @staticmethod
    async def _send_simple(writer: asyncio.StreamWriter, code: int, message: str, detail: str = "") -> None:
        body = f"{code} {message}\n" + (f"{detail}\n" if detail else "")
        payload = body.encode("utf-8")
        head = (
            f"HTTP/1.1 {code} {message}\r\n"
            f"Content-Type: text/plain; charset=utf-8\r\n"
            f"Content-Length: {len(payload)}\r\n"
            f"Connection: close\r\n"
            f"X-Balancer: megatool\r\n\r\n"
        ).encode("latin-1")
        try:
            writer.write(head + payload)
            await writer.drain()
        except Exception:
            pass
        await Balancer._close_writer(writer)

    @staticmethod
    async def _close_writer(writer: asyncio.StreamWriter) -> None:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  Здоровье и метрики
    # ------------------------------------------------------------------ #
    async def health_loop(self) -> None:
        interval = self.health.get("interval_s", 10)
        while True:
            try:
                await asyncio.gather(*[self._check_target(t) for t in list(self.targets)])
            except Exception:
                pass
            await asyncio.sleep(interval)

    async def _check_target(self, target: Target) -> None:
        timeout = self.health.get("timeout_s", 3)
        target.checks += 1
        started = time.perf_counter()
        ok = False
        error = ""
        try:
            if self.health.get("mode") == "http":
                ok, error = await self._http_check(target, timeout)
            else:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(target.host, target.port), timeout=timeout)
                ok = True
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
        except Exception as exc:
            ok = False
            error = f"{type(exc).__name__}: {exc}"[:200]
        target.last_check = time.time()
        target.latency_ms = (time.perf_counter() - started) * 1000
        target.latency_hist.append(target.latency_ms)
        if ok:
            target.ok_checks += 1
            target.fails = 0
            target.healthy = True
            target.last_error = ""
        else:
            target.fails += 1
            target.last_error = error or t("balancer.noConnection")
            if target.fails >= 2:
                target.healthy = False

    async def _http_check(self, target: Target, timeout: float) -> tuple[bool, str]:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(target.host, target.port), timeout=timeout)
        try:
            path = self.health.get("path", "/") or "/"
            host_header = f"{target.host}:{target.port}"
            request = (f"GET {path} HTTP/1.1\r\nHost: {host_header}\r\n"
                       f"User-Agent: MegaTool-HealthCheck/1.0\r\nConnection: close\r\n\r\n")
            writer.write(request.encode("latin-1"))
            await writer.drain()
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=timeout)
            first = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
            parts = first.split(" ")
            code = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
            limit = self.health.get("ok_status_max", 499)
            return code <= limit, "" if code <= limit else f"HTTP {code}"
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def metrics_loop(self) -> None:
        previous_requests = 0
        previous_ts = time.time()
        while True:
            await asyncio.sleep(2)
            now = time.time()
            total_requests = sum(t.requests for t in self.targets)
            delta = max(0, total_requests - previous_requests)
            elapsed = max(0.5, now - previous_ts)
            latencies = sorted(self._latencies)
            p95 = latencies[int(len(latencies) * 0.95) - 1] if latencies else 0.0
            self._metrics.append({
                "ts": now,
                "rps": round(delta / elapsed, 2),
                "active": sum(t.active for t in self.targets),
                "healthy": len([t for t in self.targets if t.healthy]),
                "errors": sum(t.errors for t in self.targets),
                "p95_ms": round(p95, 1),
                "requests": total_requests,
            })
            previous_requests = total_requests
            previous_ts = now

    def history(self) -> dict[str, Any]:
        points = list(self._metrics)
        return {
            "ts": [round(p["ts"], 1) for p in points],
            "series": {
                "rps": [p["rps"] for p in points],
                "active": [p["active"] for p in points],
                "healthy": [p["healthy"] for p in points],
                "p95_ms": [p["p95_ms"] for p in points],
            },
            "per_target": {
                t.address: {
                    "requests": t.requests,
                    "errors": t.errors,
                    "latency": [round(v, 1) for v in list(t.latency_hist)[-60:]],
                    "active": t.active,
                    "healthy": t.healthy,
                } for t in self.targets
            },
        }

    # ------------------------------------------------------------------ #
    #  Экспорт конфигурации
    # ------------------------------------------------------------------ #
    def export_nginx(self, server_name: str = "_") -> str:
        upstream_name = f"megatool_{self.name.replace('-', '_')}"
        weights = sorted({t.weight for t in self.targets})
        balance_line = ""
        if self.algorithm == "least_conn":
            balance_line = "    least_conn;\n"
        elif self.algorithm == "ip_hash":
            balance_line = "    ip_hash;\n"
        lines = [
            t("balancer.exportGenerated", date=iso()),
            t("balancer.exportComment", name=self.name, algorithm=_algorithm_label(self.algorithm)),
            f"upstream {upstream_name} {{",
        ]
        if balance_line:
            lines.append(balance_line.rstrip("\n"))
        for target in self.targets:
            weight = f" weight={target.weight}" if len(weights) > 1 or target.weight != 1 else ""
            note = f"  # {target.note}" if target.note else ""
            lines.append(f"    server {target.address}{weight} max_fails=3 fail_timeout=15s;{note}")
        lines.append("    keepalive 32;")
        lines.append("}")
        lines.append("")
        lines.append("server {")
        lines.append(f"    listen {self.listen_port};")
        lines.append(f"    server_name {server_name};")
        if self.health.get("mode") == "http":
            lines.append("    location / {")
            lines.append(f"        proxy_pass http://{upstream_name};")
            lines.append("        proxy_http_version 1.1;")
            lines.append('        proxy_set_header Connection "";')
        else:
            lines.append("    location / {")
            lines.append(f"        proxy_pass http://{upstream_name};")
        lines.append("        proxy_set_header Host $host;")
        lines.append("        proxy_set_header X-Real-IP $remote_addr;")
        lines.append("        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;")
        lines.append("        proxy_set_header X-Forwarded-Proto $scheme;")
        lines.append("        proxy_next_upstream error timeout http_502 http_503 http_504;")
        lines.append("    }")
        lines.append("}")
        return "\n".join(lines) + "\n"

    def export_haproxy(self) -> str:
        mode = "http" if self.health.get("mode") == "http" else "tcp"
        balance = {
            "round_robin": "roundrobin",
            "weighted": "roundrobin",
            "least_conn": "leastconn",
            "ip_hash": "source",
            "random": "random",
        }.get(self.algorithm, "roundrobin")
        lines = [
            t("balancer.exportGenerated", date=iso()),
            "",
            "frontend megatool_in",
            f"    bind *:{self.listen_port}",
            f"    mode {mode}",
            f"    default_backend megatool_{self.name.replace('-', '_')}",
            "",
            f"backend megatool_{self.name.replace('-', '_')}",
            f"    mode {mode}",
            f"    balance {balance}",
        ]
        if mode == "http" and self.health.get("enabled"):
            lines.append(f"    option httpchk GET {self.health.get('path', '/')}")
            lines.append("    http-check expect status 200")
        for target in self.targets:
            lines.append(
                f"    server {target.address.replace(':', '_').replace('.', '_')} {target.address} "
                f"weight {target.weight} check inter {self.health.get('interval_s', 10)}s "
                f"fall 2 rise 2"
            )
        return "\n".join(lines) + "\n"

    def export_json(self) -> str:
        import json

        return json.dumps(self.to_config(), ensure_ascii=False, indent=2)

    def export(self, fmt: str = "nginx") -> str:
        if fmt == "haproxy":
            return self.export_haproxy()
        if fmt == "json":
            return self.export_json()
        return self.export_nginx()


# --------------------------------------------------------------------------- #
#  Менеджер балансировщиков
# --------------------------------------------------------------------------- #

class BalancerManager:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.balancers: dict[str, Balancer] = {}
        self.exports_dir = ensure_dir(CONFIGS_DIR / "balancers")
        self.load()

    # ------------------------------------------------------------------ #
    def load(self) -> None:
        self.balancers = {}
        for item in self.cfg.get("balancers", []) or []:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            balancer = Balancer(self, item)
            self.balancers[balancer.name] = balancer

    def save(self) -> None:
        self.cfg.set("balancers", [b.to_config() for b in self.balancers.values()])
        self.cfg.save()

    # ------------------------------------------------------------------ #
    def list_all(self) -> list[dict[str, Any]]:
        # Подписи алгоритмов отдаются в том же ответе API — обновляем их под язык.
        _refresh_algorithm_labels()
        return [b.to_dict() for b in self.balancers.values()]

    def get(self, name: str) -> Balancer | None:
        return self.balancers.get(safe_id(name, ""))

    def create(self, data: dict[str, Any]) -> dict[str, Any]:
        name = safe_id(str(data.get("name", "")), "")
        if not name:
            return {"ok": False, "error": t("balancer.nameRequired")}
        if name in self.balancers:
            return {"ok": False, "error": t("balancer.alreadyExists", name=name)}
        try:
            port = 8080 if data.get("listen_port") in (None, "") else int(data.get("listen_port"))
        except (TypeError, ValueError):
            return {"ok": False, "error": t("balancer.invalidPort")}
        if not 0 <= port <= 65535:
            return {"ok": False, "error": t("balancer.listenPortRange")}
        payload = dict(data)
        payload["name"] = name
        payload["listen_port"] = port
        balancer = Balancer(self, payload)
        self.balancers[name] = balancer
        self.save()
        return {"ok": True, "balancer": balancer.to_dict()}

    def update(self, name: str, patch: dict[str, Any]) -> dict[str, Any]:
        balancer = self.get(name)
        if not balancer:
            return {"ok": False, "error": t("balancer.notFound")}
        if "targets" in patch:
            error = balancer.set_targets(patch.get("targets") or [])
            if error:
                return {"ok": False, "error": error}
        if "algorithm" in patch and patch["algorithm"] in ALGORITHMS:
            balancer.algorithm = patch["algorithm"]
        if "note" in patch:
            balancer.note = str(patch["note"])[:200]
        if "autostart" in patch:
            balancer.autostart = bool(patch["autostart"])
        if "health" in patch and isinstance(patch["health"], dict):
            health = dict(balancer.health)
            for key in ("enabled", "mode", "path", "interval_s", "timeout_s", "ok_status_max"):
                if key in patch["health"]:
                    health[key] = patch["health"][key]
            balancer.health = {
                "enabled": bool(health.get("enabled", True)),
                "mode": "http" if health.get("mode") == "http" else "tcp",
                "path": str(health.get("path", "/") or "/"),
                "interval_s": max(2, int(health.get("interval_s", 10) or 10)),
                "timeout_s": max(1, min(30, int(health.get("timeout_s", 3) or 3))),
                "ok_status_max": int(health.get("ok_status_max", 499) or 499),
            }
        for key in ("listen_host", "listen_port"):
            if key in patch:
                if key == "listen_port":
                    try:
                        port = int(patch[key])
                    except (TypeError, ValueError):
                        continue
                    if not 0 <= port <= 65535:
                        continue
                    balancer.listen_port = port
                else:
                    balancer.listen_host = str(patch[key] or "127.0.0.1")
        self.save()
        return {"ok": True, "balancer": balancer.to_dict()}

    async def delete(self, name: str) -> dict[str, Any]:
        balancer = self.get(name)
        if not balancer:
            return {"ok": False, "error": t("balancer.notFound")}
        await balancer.stop()
        self.balancers.pop(balancer.name, None)
        self.save()
        return {"ok": True}

    async def start(self, name: str) -> dict[str, Any]:
        balancer = self.get(name)
        if not balancer:
            return {"ok": False, "error": t("balancer.notFound")}
        result = await balancer.start()
        if result.get("ok"):
            self.save()
        return result

    async def stop(self, name: str) -> dict[str, Any]:
        balancer = self.get(name)
        if not balancer:
            return {"ok": False, "error": t("balancer.notFound")}
        return await balancer.stop()

    async def stop_all(self) -> None:
        for balancer in list(self.balancers.values()):
            await balancer.stop()

    async def autostart(self) -> list[str]:
        started = []
        for balancer in self.balancers.values():
            if balancer.autostart and not balancer.running:
                result = await balancer.start()
                if result.get("ok"):
                    started.append(balancer.name)
        return started

    # ------------------------------------------------------------------ #
    def export(self, name: str, fmt: str = "nginx", write_file: bool = False) -> dict[str, Any]:
        balancer = self.get(name)
        if not balancer:
            return {"ok": False, "error": t("balancer.notFound")}
        content = balancer.export(fmt)
        result: dict[str, Any] = {"ok": True, "format": fmt, "content": content}
        if write_file:
            extension = {"nginx": "nginx.conf", "haproxy": "haproxy.cfg", "json": "balancer.json"}.get(fmt, "config.txt")
            path: Path = self.exports_dir / f"{balancer.name}.{extension}"
            path.write_text(content, encoding="utf-8")
            result["path"] = str(path)
        return result

    def alerts(self) -> list[dict[str, Any]]:
        alerts: list[dict[str, Any]] = []
        for balancer in self.balancers.values():
            if not balancer.running:
                continue
            for target in balancer.targets:
                if target.healthy is False:
                    alerts.append({
                        "id": f"balancer-{balancer.name}-{target.id}",
                        "severity": "critical",
                        "title": t("balancer.alertTargetDownTitle", address=target.address),
                        "detail": t("balancer.alertTargetDownDetail", name=balancer.name,
                                    error=target.last_error or t("balancer.noConnection")),
                        "source": t("balancer.alertSource"),
                    })
            healthy = len([t for t in balancer.targets if t.healthy])
            if balancer.targets and healthy == 0:
                alerts.append({
                    "id": f"balancer-{balancer.name}-empty",
                    "severity": "critical",
                    "title": t("balancer.alertNoTargetsTitle", name=balancer.name),
                    "detail": t("balancer.alertNoTargetsDetail",
                                address=f"{balancer.listen_host}:{balancer.listen_port}"),
                    "source": t("balancer.alertSource"),
                })
        return alerts

    def public_state(self) -> dict[str, Any]:
        return {
            "count": len(self.balancers),
            "running": len([b for b in self.balancers.values() if b.running]),
            "targets": sum(len(b.targets) for b in self.balancers.values()),
            "requests": sum(t.requests for b in self.balancers.values() for t in b.targets),
        }

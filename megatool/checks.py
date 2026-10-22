"""Проверки доступности: HTTP(S), TCP-порты и TLS-сертификаты.

Проверки хранятся в `config.json` (секция `checks`), а результаты — в памяти.
Фоновый поток опрашивает включённые проверки по их интервалам, поэтому
состояние обновляется даже когда вкладка «Доступность» закрыта. Наружу
результаты отдаются без служебных ключей и с уже переведённым описанием.
"""

from __future__ import annotations

import re
import socket
import ssl
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import HTTPSHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .i18n import t
from .util import iso, safe_id

MAX_CHECKS = 50
MAX_HISTORY = 40
MAX_BODY_BYTES = 64 * 1024
MIN_INTERVAL = 15
MAX_INTERVAL = 3600
MIN_TIMEOUT = 1.0
MAX_TIMEOUT = 10.0
CHECK_TYPES = ("http", "tcp", "tls")


class _TooManyRedirects(Exception):
    """Больше разрешённого числа перенаправлений."""


class _BadRedirect(Exception):
    """Перенаправление на не-HTTP(S) схему."""


class _RedirectLimit(HTTPRedirectHandler):
    def __init__(self, limit: int = 3) -> None:
        super().__init__()
        self.limit = limit
        self.seen = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.seen += 1
        if self.seen > self.limit:
            raise _TooManyRedirects()
        target = urljoin(req.full_url, newurl)
        if urlsplit(target).scheme not in ("http", "https"):
            raise _BadRedirect()
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# --------------------------------------------------------------------------- #
#  Валидация конфигурации
# --------------------------------------------------------------------------- #

def _int_value(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError, OverflowError):
        number = default
    return min(high, max(low, number))


def _float_value(value: Any, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if number != number or number in (float("inf"), float("-inf")):
        number = default
    return min(high, max(low, number))


def _normalize_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 2048 or any(char.isspace() for char in text):
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    if parts.username or parts.password:
        return ""
    try:
        parts.port  # проверяет корректность порта, даже если он не нужен
    except ValueError:
        return ""
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))


def _normalize_host(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("[") and "]" in text:
        text = text[1:text.index("]")]
    if not text or len(text) > 253:
        return ""
    if any(char in text for char in "/\\@?#") or any(char.isspace() for char in text):
        return ""
    return text


def _target(item: Dict[str, Any]) -> str:
    if item.get("type") == "http":
        return str(item.get("url") or "")
    host = str(item.get("host") or "")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{host}:{item.get('port')}"


def clean_checks(raw: Any) -> Tuple[List[Dict[str, Any]], str]:
    """Проверяет список проверок и возвращает (очищенный список, ошибка)."""
    if not isinstance(raw, list):
        return [], t("checks.badList")
    if len(raw) > MAX_CHECKS:
        return [], t("checks.tooMany", limit=MAX_CHECKS)

    out: List[Dict[str, Any]] = []
    used: Set[str] = set()
    for source in raw:
        if not isinstance(source, dict):
            return [], t("checks.badList")
        kind = str(source.get("type") or "http").strip().lower()
        if kind not in CHECK_TYPES:
            return [], t("checks.badType")

        item: Dict[str, Any] = {
            "type": kind,
            "interval_sec": _int_value(source.get("interval_sec"), 60, MIN_INTERVAL, MAX_INTERVAL),
            "timeout_sec": round(_float_value(source.get("timeout_sec"), 5, MIN_TIMEOUT, MAX_TIMEOUT), 1),
            "enabled": source.get("enabled", True) is not False,
        }

        if kind == "http":
            url = _normalize_url(source.get("url"))
            if not url:
                return [], t("checks.badUrl")
            item.update({
                "url": url,
                "expected_status": _int_value(source.get("expected_status"), 200, 100, 599),
                "keyword": str(source.get("keyword") or "").strip()[:200],
                "verify_tls": source.get("verify_tls", True) is not False,
            })
        else:
            host = _normalize_host(source.get("host"))
            if not host:
                return [], t("checks.badHost")
            port = _int_value(source.get("port"), 443 if kind == "tls" else 80, 1, 65535)
            item.update({
                "host": host,
                "port": port,
                "verify_tls": source.get("verify_tls", True) is not False,
            })
            if kind == "tls":
                item["warn_days"] = _int_value(source.get("warn_days"), 30, 1, 365)

        name = str(source.get("name") or "").strip()[:80] or _target(item)
        item["name"] = name
        check_id = safe_id(str(source.get("id") or ""), "")
        if not check_id or check_id in used:
            check_id = f"check-{uuid.uuid4().hex[:8]}"
        used.add(check_id)
        item["id"] = check_id
        out.append(item)
    return out, ""


# --------------------------------------------------------------------------- #
#  Сетевые проверки
# --------------------------------------------------------------------------- #

def _failure(reason: str, latency_ms: float, **params: Any) -> Dict[str, Any]:
    return {
        "ok": False,
        "warn": False,
        "latency_ms": round(max(0.0, latency_ms), 1),
        "reason": reason,
        "params": params,
    }


def _http_probe(item: Dict[str, Any]) -> Dict[str, Any]:
    started = time.perf_counter()
    verify = bool(item.get("verify_tls", True))
    context = ssl.create_default_context()
    if not verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    redirects = _RedirectLimit(3)
    opener = build_opener(ProxyHandler({}), redirects, HTTPSHandler(context=context))
    request = Request(str(item["url"]), headers={
        "User-Agent": "MegaTool-Check/1.0",
        "Accept": "*/*",
        "Accept-Encoding": "identity",
    })
    timeout = float(item.get("timeout_sec") or 5)
    status = 0
    body = b""
    try:
        with opener.open(request, timeout=timeout) as response:
            status = int(getattr(response, "status", response.getcode()))
            body = response.read(MAX_BODY_BYTES + 1)
    except HTTPError as exc:
        status = int(exc.code)
        try:
            body = exc.read(MAX_BODY_BYTES + 1)
        except Exception:
            body = b""
    except _TooManyRedirects:
        return _failure("checks.detail.redirects", (time.perf_counter() - started) * 1000)
    except _BadRedirect:
        return _failure("checks.detail.redirectScheme", (time.perf_counter() - started) * 1000)
    except (URLError, OSError, ssl.SSLError, ValueError) as exc:
        reason = getattr(exc, "reason", None) or exc
        return _failure("checks.detail.error", (time.perf_counter() - started) * 1000, error=str(reason)[:300])

    latency = (time.perf_counter() - started) * 1000
    expected = int(item.get("expected_status") or 200)
    if status != expected:
        return _failure("checks.detail.httpStatus", latency, status=status, expected=expected)
    keyword = str(item.get("keyword") or "")
    if keyword and keyword not in body.decode("utf-8", errors="replace"):
        return _failure("checks.detail.keyword", latency, keyword=keyword)
    return {
        "ok": True,
        "warn": False,
        "latency_ms": round(latency, 1),
        "reason": "checks.detail.httpOk",
        "params": {"status": status, "ms": round(latency)},
    }


def _tcp_probe(item: Dict[str, Any]) -> Dict[str, Any]:
    started = time.perf_counter()
    host = str(item["host"])
    port = int(item["port"])
    try:
        with socket.create_connection((host, port), timeout=float(item.get("timeout_sec") or 5)):
            pass
    except (OSError, ValueError) as exc:
        return _failure("checks.detail.error", (time.perf_counter() - started) * 1000, error=str(exc)[:300])
    latency = (time.perf_counter() - started) * 1000
    return {
        "ok": True,
        "warn": False,
        "latency_ms": round(latency, 1),
        "reason": "checks.detail.tcpOk",
        "params": {"ms": round(latency)},
    }


def _cert_expiry(cert: Dict[str, Any], der: Optional[bytes]) -> Optional[float]:
    not_after = str(cert.get("notAfter") or "")
    if not_after:
        try:
            return float(ssl.cert_time_to_seconds(not_after))
        except ValueError:
            pass
    # При отключённой проверке цепочки getpeercert() пуст, но битый DER
    # содержит время notAfter. Ищем последнюю метку времени в сертификате.
    for raw in reversed(re.findall(rb"(\d{12}|\d{14})Z", der or b"")):
        try:
            text = raw.decode("ascii")
            fmt = "%y%m%d%H%M%S" if len(text) == 12 else "%Y%m%d%H%M%S"
            parsed = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except ValueError:
            continue
    return None


def _tls_probe(item: Dict[str, Any]) -> Dict[str, Any]:
    started = time.perf_counter()
    verify = bool(item.get("verify_tls", True))
    context = ssl.create_default_context()
    if not verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    host = str(item["host"])
    port = int(item["port"])
    try:
        with socket.create_connection((host, port), timeout=float(item.get("timeout_sec") or 5)) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                cert = tls.getpeercert()
                der = tls.getpeercert(binary_form=True)
    except ssl.SSLCertVerificationError as exc:
        return _failure("checks.detail.tlsVerify", (time.perf_counter() - started) * 1000,
                        error=str(getattr(exc, "verify_message", None) or exc)[:300])
    except (OSError, ValueError, ssl.SSLError) as exc:
        return _failure("checks.detail.error", (time.perf_counter() - started) * 1000, error=str(exc)[:300])

    latency = (time.perf_counter() - started) * 1000
    expiry = _cert_expiry(cert, der)
    if expiry is None:
        return {
            "ok": True,
            "warn": False,
            "latency_ms": round(latency, 1),
            "reason": "checks.detail.tlsOkNoExpiry",
            "params": {"ms": round(latency)},
        }
    days = round((expiry - time.time()) / 86400, 1)
    if days <= 0:
        return _failure("checks.detail.tlsExpired", latency, days=days, expires=iso(expiry))
    warn_days = int(item.get("warn_days") or 30)
    warn = days <= warn_days
    return {
        "ok": True,
        "warn": warn,
        "latency_ms": round(latency, 1),
        "reason": "checks.detail.tlsWarn" if warn else "checks.detail.tlsOk",
        "params": {"days": days, "expires": iso(expiry), "ms": round(latency)},
        "cert_days": days,
    }


def _probe(item: Dict[str, Any]) -> Dict[str, Any]:
    kind = item.get("type")
    if kind == "http":
        return _http_probe(item)
    if kind == "tcp":
        return _tcp_probe(item)
    if kind == "tls":
        return _tls_probe(item)
    return _failure("checks.detail.error", 0.0, error="unknown check type")


# --------------------------------------------------------------------------- #
#  Менеджер фоновых проверок
# --------------------------------------------------------------------------- #

class ChecksManager:
    """Хранит конфигурацию, результаты и историю проверок в памяти."""

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self._lock = threading.RLock()
        self._items: List[Dict[str, Any]] = []
        self._results: Dict[str, Dict[str, Any]] = {}
        self._history: Dict[str, Any] = {}
        self._last_started: Dict[str, float] = {}
        self._running: Set[str] = set()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="megatool-check")
        self.reload()

    # ------------------------------------------------------------------ #
    def reload(self) -> None:
        items, _error = clean_checks(self.cfg.get("checks", []))
        ids = {item["id"] for item in items}
        with self._lock:
            self._items = items
            self._results = {key: value for key, value in self._results.items() if key in ids}
            self._history = {key: value for key, value in self._history.items() if key in ids}
            self._last_started = {key: value for key, value in self._last_started.items() if key in ids}
            self._running &= ids

    def save(self, raw: Any) -> Tuple[List[Dict[str, Any]], str]:
        items, error = clean_checks(raw)
        if error:
            return [], error
        self.cfg.set("checks", items)
        self.cfg.save()
        with self._lock:
            self._last_started = {}
        self.reload()
        return items, ""

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="megatool-checks", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._pool.shutdown(wait=False)

    # ------------------------------------------------------------------ #
    def _loop(self) -> None:
        while not self._stop.is_set():
            now = time.monotonic()
            due: List[Dict[str, Any]] = []
            with self._lock:
                for item in self._items:
                    if not item.get("enabled", True) or item["id"] in self._running:
                        continue
                    last = self._last_started.get(item["id"], 0.0)
                    if now - last < int(item.get("interval_sec") or 60):
                        continue
                    self._last_started[item["id"]] = now
                    self._running.add(item["id"])
                    due.append(dict(item))
            for item in due:
                self._pool.submit(self._run_one, item)
            self._stop.wait(1.0)

    def _run_one(self, item: Dict[str, Any]) -> None:
        result = self._probe_safe(item)
        self._store(item["id"], result)
        with self._lock:
            self._running.discard(item["id"])

    def _probe_safe(self, item: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return _probe(item)
        except Exception as exc:  # pragma: no cover - защита фонового потока
            return _failure("checks.detail.error", 0.0, error=str(exc)[:300])

    def _store(self, check_id: str, result: Dict[str, Any]) -> None:
        now = time.time()
        entry = dict(result)
        entry["checked_ts"] = now
        entry["checked"] = iso(now)
        entry["state"] = "down" if not entry.get("ok") else ("warn" if entry.get("warn") else "up")
        with self._lock:
            history = self._history.get(check_id)
            if history is None:
                from collections import deque
                history = deque(maxlen=MAX_HISTORY)
                self._history[check_id] = history
            history.append({
                "ts": iso(now),
                "ok": bool(entry.get("ok")),
                "warn": bool(entry.get("warn")),
                "latency_ms": entry.get("latency_ms"),
            })
            entry["history"] = list(history)
            self._results[check_id] = entry

    # ------------------------------------------------------------------ #
    def run(self, check_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            item = next((dict(entry) for entry in self._items if entry["id"] == check_id), None)
        if not item:
            return None
        self._store(check_id, self._probe_safe(item))
        return self.snapshot()

    def run_all(self) -> Dict[str, Any]:
        with self._lock:
            items = [dict(entry) for entry in self._items if entry.get("enabled", True)]
        if items:
            with ThreadPoolExecutor(max_workers=min(6, len(items))) as pool:
                results = list(pool.map(self._probe_safe, items))
            for item, result in zip(items, results):
                self._store(item["id"], result)
        return self.snapshot()

    # ------------------------------------------------------------------ #
    def summary(self) -> Dict[str, int]:
        with self._lock:
            items = [dict(entry) for entry in self._items]
            results = {key: dict(value) for key, value in self._results.items()}
        counts = {"total": len(items), "up": 0, "warn": 0, "down": 0, "unknown": 0, "disabled": 0}
        for item in items:
            if not item.get("enabled", True):
                counts["disabled"] += 1
                continue
            result = results.get(item["id"])
            if not result:
                counts["unknown"] += 1
            elif not result.get("ok"):
                counts["down"] += 1
            elif result.get("warn"):
                counts["warn"] += 1
            else:
                counts["up"] += 1
        return counts

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            items = [dict(entry) for entry in self._items]
            results = {key: dict(value) for key, value in self._results.items()}
        output = []
        latest = ""
        for item in items:
            row = dict(item)
            row["target"] = _target(item)
            result = results.get(item["id"])
            if result:
                reason = str(result.pop("reason", "") or "")
                params = result.pop("params", {}) or {}
                row.update({key: value for key, value in result.items() if key != "history"})
                row["detail"] = t(reason, **params) if reason else ""
                row["history"] = result.get("history", [])
                if row.get("checked") and row["checked"] > latest:
                    latest = row["checked"]
            else:
                row.update({"state": "unknown", "ok": None, "detail": "", "history": []})
            if not item.get("enabled", True):
                row["state"] = "disabled"
            output.append(row)
        return {"items": output, "summary": self.summary(), "checked": latest}

    def alerts(self) -> List[Dict[str, Any]]:
        with self._lock:
            items = [dict(entry) for entry in self._items]
            results = {key: dict(value) for key, value in self._results.items()}
        alerts = []
        for item in items:
            if not item.get("enabled", True):
                continue
            result = results.get(item["id"])
            if not result:
                continue
            reason = str(result.get("reason") or "")
            params = result.get("params") or {}
            detail = t(reason, **params) if reason else ""
            if not result.get("ok"):
                alerts.append({
                    "id": f"check:{item['id']}",
                    "severity": "critical",
                    "title": t("checks.alert.down", name=item["name"]),
                    "detail": detail,
                    "source": t("checks.source"),
                    "value": _target(item),
                })
            elif result.get("warn"):
                alerts.append({
                    "id": f"check:{item['id']}",
                    "severity": "warning",
                    "title": t("checks.alert.expiring", name=item["name"]),
                    "detail": detail,
                    "source": t("checks.source"),
                    "value": result.get("cert_days"),
                })
        return alerts

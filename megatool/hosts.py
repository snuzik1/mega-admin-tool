"""Мониторинг серверов: инвентарь, доступность, uptime и время простоя.

Список серверов хранится в секции `hosts` файла config.json, а накопленное
состояние (с какого момента сервер работает/недоступен/выключен и суммарные
интервалы) — в отдельном файле `hosts_state.json`. Так время работы не
сбрасывается при перезапуске MegaTool.

Сервер проверяется по TCP-порту или HTTP(S)-адресу. Фоновый поток опрашивает
включённые записи по их интервалам; выключенные серверы не опрашиваются, но
время, проведённое в выключенном состоянии, учитывается отдельно (disabled).
"""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from .checks import _normalize_host, _normalize_url, _probe
from .config import HOME_DIR
from .i18n import t
from .util import iso, read_json, safe_id, write_json

MAX_HOSTS = 50
MIN_INTERVAL = 15
MAX_INTERVAL = 3600
MIN_TIMEOUT = 1.0
MAX_TIMEOUT = 10.0
PROBE_TYPES = ("tcp", "http")

#: Имя файла с накопленным состоянием. Секретов там нет.
STATE_PATH = HOME_DIR / "hosts_state.json"

_STATES = ("unknown", "up", "down", "disabled")


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


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _note(value: Any, limit: int = 300) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _target(item: Dict[str, Any]) -> str:
    if item.get("probe") == "http":
        return str(item.get("url") or "")
    host = str(item.get("host") or "")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{host}:{item.get('port')}"


def _fingerprint(item: Dict[str, Any]) -> str:
    if item.get("probe") == "http":
        return f"http|{item.get('url')}"
    return f"tcp|{item.get('host')}|{item.get('port')}"


def clean_hosts(raw: Any) -> Tuple[List[Dict[str, Any]], str]:
    """Проверяет список серверов и возвращает (очищенный список, ошибка)."""
    if not isinstance(raw, list):
        return [], t("hosts.badList")
    if len(raw) > MAX_HOSTS:
        return [], t("hosts.tooMany", limit=MAX_HOSTS)

    out: List[Dict[str, Any]] = []
    used: Set[str] = set()
    for source in raw:
        if not isinstance(source, dict):
            return [], t("hosts.badList")
        probe = str(source.get("probe") or "tcp").strip().lower()
        if probe not in PROBE_TYPES:
            return [], t("hosts.badProbe")

        item: Dict[str, Any] = {
            "probe": probe,
            "kind": _text(source.get("kind"), 40),
            "note": _note(source.get("note")),
            "interval_sec": _int_value(source.get("interval_sec"), 60, MIN_INTERVAL, MAX_INTERVAL),
            "timeout_sec": round(_float_value(source.get("timeout_sec"), 5, MIN_TIMEOUT, MAX_TIMEOUT), 1),
            "enabled": source.get("enabled", True) is not False,
        }
        if probe == "http":
            url = _normalize_url(source.get("url"))
            if not url:
                return [], t("hosts.badUrl")
            item.update({
                "url": url,
                "expected_status": _int_value(source.get("expected_status"), 200, 100, 599),
                "keyword": _text(source.get("keyword"), 200),
                "verify_tls": source.get("verify_tls", True) is not False,
            })
        else:
            host = _normalize_host(source.get("host"))
            if not host:
                return [], t("hosts.badHost")
            item.update({
                "host": host,
                "port": _int_value(source.get("port"), 443, 1, 65535),
            })

        name = _text(source.get("name"), 80) or _target(item)
        item["name"] = name
        host_id = safe_id(str(source.get("id") or ""), "")
        if not host_id or host_id in used:
            host_id = f"host-{uuid.uuid4().hex[:8]}"
        used.add(host_id)
        item["id"] = host_id
        out.append(item)
    return out, ""


def _new_state(now: float, item: Dict[str, Any]) -> Dict[str, Any]:
    enabled = item.get("enabled", True)
    return {
        "state": "unknown" if enabled else "disabled",
        "since": now,
        "fingerprint": _fingerprint(item),
        "last_check": 0.0,
        "last_ok": 0.0,
        "last_reason": "",
        "last_params": {},
        "latency_ms": None,
        "ok_count": 0,
        "fail_count": 0,
        "total_up_sec": 0.0,
        "total_down_sec": 0.0,
        "total_disabled_sec": 0.0,
    }


def _close_period(state: Dict[str, Any], now: float) -> None:
    """Добавляет длительность текущего состояния в суммарный счётчик."""
    since = _float_value(state.get("since"), now, 0.0, now)
    elapsed = max(0.0, now - since)
    current = state.get("state")
    if current == "up":
        state["total_up_sec"] = _float_value(state.get("total_up_sec"), 0.0, 0.0, 1e15) + elapsed
    elif current == "down":
        state["total_down_sec"] = _float_value(state.get("total_down_sec"), 0.0, 0.0, 1e15) + elapsed
    elif current == "disabled":
        state["total_disabled_sec"] = _float_value(state.get("total_disabled_sec"), 0.0, 0.0, 1e15) + elapsed


def _set_state(state: Dict[str, Any], new_state: str, now: float) -> bool:
    """Переключает состояние. Возвращает True, если состояние изменилось."""
    if new_state not in _STATES or state.get("state") == new_state:
        return False
    if state.get("state") in ("up", "down", "disabled"):
        _close_period(state, now)
    state["state"] = new_state
    state["since"] = now
    return True


class HostsManager:
    """Хранит инвентарь серверов и историю их доступности."""

    def __init__(self, cfg, state_path: Optional[Union[Path, str]] = None) -> None:
        self.cfg = cfg
        self.state_path = Path(state_path) if state_path else STATE_PATH
        self._lock = threading.RLock()
        self._items: List[Dict[str, Any]] = []
        self._states: Dict[str, Dict[str, Any]] = {}
        self._last_started: Dict[str, float] = {}
        self._running: Set[str] = set()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="megatool-host")
        self._load_state()
        self.reload()

    # ------------------------------------------------------------------ #
    #  Сохранение состояния
    # ------------------------------------------------------------------ #
    def _load_state(self) -> None:
        raw = read_json(self.state_path, {})
        source = raw.get("items") if isinstance(raw, dict) else None
        if not isinstance(source, dict):
            return
        now = time.time()
        for host_id, value in source.items():
            if len(self._states) >= MAX_HOSTS:
                break
            if not isinstance(value, dict) or not safe_id(str(host_id), ""):
                continue
            state = _new_state(now, {"enabled": True})
            current = str(value.get("state") or "unknown")
            state["state"] = current if current in _STATES else "unknown"
            state["since"] = _float_value(value.get("since"), now, 0.0, now)
            state["fingerprint"] = str(value.get("fingerprint") or "")
            state["last_check"] = _float_value(value.get("last_check"), 0.0, 0.0, now)
            state["last_ok"] = _float_value(value.get("last_ok"), 0.0, 0.0, now)
            state["last_reason"] = str(value.get("last_reason") or "")[:200]
            params = value.get("last_params")
            state["last_params"] = params if isinstance(params, dict) else {}
            state["latency_ms"] = value.get("latency_ms")
            state["ok_count"] = _int_value(value.get("ok_count"), 0, 0, 10**9)
            state["fail_count"] = _int_value(value.get("fail_count"), 0, 0, 10**9)
            for key in ("total_up_sec", "total_down_sec", "total_disabled_sec"):
                state[key] = _float_value(value.get(key), 0.0, 0.0, 1e15)
            self._states[str(host_id)] = state

    def _save_state(self) -> None:
        with self._lock:
            payload = {
                "version": 1,
                "updated": iso(),
                "items": {key: dict(value) for key, value in self._states.items()},
            }
        try:
            write_json(self.state_path, payload, mode=0o600)
        except OSError:
            # Состояние — вспомогательные данные; мониторинг не должен падать
            # из-за переполненного или защищённого от записи каталога.
            pass

    # ------------------------------------------------------------------ #
    #  Конфигурация
    # ------------------------------------------------------------------ #
    def reload(self) -> None:
        items, _error = clean_hosts(self.cfg.get("hosts", []))
        now = time.time()
        changed = False
        with self._lock:
            ids = {item["id"] for item in items}
            fresh: Dict[str, Dict[str, Any]] = {}
            for item in items:
                state = self._states.get(item["id"]) or _new_state(now, item)
                fingerprint = _fingerprint(item)
                old_fingerprint = str(state.get("fingerprint") or "")
                if old_fingerprint and old_fingerprint != fingerprint:
                    # Сменилась цель проверки: старый uptime к ней уже не относится,
                    # но суммарные интервалы сохраняем.
                    _close_period(state, now)
                    state.update({
                        "state": "unknown" if item.get("enabled", True) else "disabled",
                        "since": now,
                        "fingerprint": fingerprint,
                        "last_check": 0.0,
                        "last_ok": 0.0,
                        "last_reason": "",
                        "last_params": {},
                        "latency_ms": None,
                    })
                    changed = True
                state["fingerprint"] = fingerprint
                if not item.get("enabled", True):
                    changed = _set_state(state, "disabled", now) or changed
                elif state.get("state") == "disabled":
                    changed = _set_state(state, "unknown", now) or changed
                fresh[item["id"]] = state
            if set(fresh) != set(self._states):
                changed = True
            self._states = fresh
            self._items = items
            self._last_started = {key: value for key, value in self._last_started.items() if key in ids}
            self._running &= ids
        if changed:
            self._save_state()

    def save(self, raw: Any) -> Tuple[List[Dict[str, Any]], str]:
        items, error = clean_hosts(raw)
        if error:
            return [], error
        self.cfg.set("hosts", items)
        self.cfg.save()
        with self._lock:
            self._last_started = {}
        self.reload()
        return items, ""

    # ------------------------------------------------------------------ #
    #  Фоновый мониторинг
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="megatool-hosts", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._pool.shutdown(wait=False)
        self._save_state()

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
        self._record(item, result)
        with self._lock:
            self._running.discard(item["id"])

    def _probe_safe(self, item: Dict[str, Any]) -> Dict[str, Any]:
        try:
            probe_item = dict(item)
            probe_item["type"] = item.get("probe", "tcp")
            return _probe(probe_item)
        except Exception as exc:  # pragma: no cover - защита фонового потока
            return {
                "ok": False,
                "warn": False,
                "latency_ms": 0.0,
                "reason": "checks.detail.error",
                "params": {"error": str(exc)[:300]},
            }

    def _record(self, item: Dict[str, Any], result: Dict[str, Any]) -> None:
        now = time.time()
        changed = False
        with self._lock:
            state = self._states.get(item["id"])
            if state is None:
                state = _new_state(now, item)
                self._states[item["id"]] = state
            if not item.get("enabled", True):
                _set_state(state, "disabled", now)
                return
            if state.get("state") == "disabled":
                _set_state(state, "unknown", now)
            previous = str(state.get("state") or "unknown")
            new_state = "up" if result.get("ok") else "down"
            if previous != new_state:
                _set_state(state, new_state, now)
                changed = True

            state["last_check"] = now
            state["latency_ms"] = result.get("latency_ms")
            state["last_reason"] = str(result.get("reason") or "")[:200]
            params = result.get("params")
            state["last_params"] = params if isinstance(params, dict) else {}
            if result.get("ok"):
                state["last_ok"] = now
                state["ok_count"] = _int_value(state.get("ok_count"), 0, 0, 10**9) + 1
            else:
                state["fail_count"] = _int_value(state.get("fail_count"), 0, 0, 10**9) + 1
        if changed:
            self._save_state()

    # ------------------------------------------------------------------ #
    #  Ручные проверки
    # ------------------------------------------------------------------ #
    def run(self, host_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            item = next((dict(entry) for entry in self._items if entry["id"] == host_id), None)
        if not item:
            return None
        if item.get("enabled", True):
            self._record(item, self._probe_safe(item))
        return self.snapshot()

    def run_all(self) -> Dict[str, Any]:
        with self._lock:
            items = [dict(entry) for entry in self._items if entry.get("enabled", True)]
        if items:
            with ThreadPoolExecutor(max_workers=min(6, len(items))) as pool:
                results = list(pool.map(self._probe_safe, items))
            for item, result in zip(items, results):
                self._record(item, result)
        return self.snapshot()

    # ------------------------------------------------------------------ #
    #  Представление для API и интерфейса
    # ------------------------------------------------------------------ #
    def summary(self) -> Dict[str, int]:
        with self._lock:
            items = [dict(entry) for entry in self._items]
            states = {key: dict(value) for key, value in self._states.items()}
        counts = {"total": len(items), "up": 0, "down": 0, "unknown": 0, "disabled": 0}
        for item in items:
            state = "disabled" if not item.get("enabled", True) else str(
                (states.get(item["id"]) or {}).get("state") or "unknown")
            if state not in counts:
                state = "unknown"
            counts[state] += 1
        return counts

    def public_state(self) -> Dict[str, Any]:
        """Короткая сводка для верхней панели и системных уведомлений."""
        snapshot = self.snapshot()
        return {
            "summary": snapshot["summary"],
            "checked": snapshot["checked"],
            "items": [
                {
                    "id": item["id"], "name": item["name"], "state": item["state"],
                    "enabled": item["enabled"], "target": item["target"],
                }
                for item in snapshot["items"]
            ],
        }

    def snapshot(self) -> Dict[str, Any]:
        now = time.time()
        with self._lock:
            items = [dict(entry) for entry in self._items]
            states = {key: dict(value) for key, value in self._states.items()}
        output: List[Dict[str, Any]] = []
        latest = 0.0
        for item in items:
            row = dict(item)
            row["target"] = _target(item)
            state = states.get(item["id"]) or _new_state(now, item)
            current = str(state.get("state") or "unknown")
            if not item.get("enabled", True):
                current = "disabled"
            since = _float_value(state.get("since"), now, 0.0, now)
            current_duration = max(0.0, now - since) if current in ("up", "down", "disabled") else 0.0
            total_up = _float_value(state.get("total_up_sec"), 0.0, 0.0, 1e15)
            total_down = _float_value(state.get("total_down_sec"), 0.0, 0.0, 1e15)
            total_disabled = _float_value(state.get("total_disabled_sec"), 0.0, 0.0, 1e15)
            if current == "up":
                total_up += current_duration
            elif current == "down":
                total_down += current_duration
            elif current == "disabled":
                total_disabled += current_duration

            reason = str(state.get("last_reason") or "")
            params = state.get("last_params") or {}
            last_check = _float_value(state.get("last_check"), 0.0, 0.0, now)
            last_ok = _float_value(state.get("last_ok"), 0.0, 0.0, now)
            row.update({
                "state": current,
                "since": iso(since),
                "uptime_sec": current_duration if current == "up" else 0.0,
                "downtime_sec": current_duration if current == "down" else 0.0,
                "disabled_sec": current_duration if current == "disabled" else 0.0,
                "total_uptime_sec": round(total_up, 1),
                "total_downtime_sec": round(total_down, 1),
                "total_disabled_sec": round(total_disabled, 1),
                "last_check": iso(last_check) if last_check else "",
                "last_ok": iso(last_ok) if last_ok else "",
                "detail": t(reason, **params) if reason else "",
                "latency_ms": state.get("latency_ms"),
                "ok_count": _int_value(state.get("ok_count"), 0, 0, 10**9),
                "fail_count": _int_value(state.get("fail_count"), 0, 0, 10**9),
            })
            latest = max(latest, last_check)
            output.append(row)
        return {
            "items": output,
            "summary": self.summary(),
            "checked": iso(latest) if latest else "",
        }

    def alerts(self) -> List[Dict[str, Any]]:
        now = time.time()
        with self._lock:
            items = [dict(entry) for entry in self._items]
            states = {key: dict(value) for key, value in self._states.items()}
        alerts: List[Dict[str, Any]] = []
        for item in items:
            if not item.get("enabled", True):
                continue
            state = states.get(item["id"]) or {}
            if state.get("state") != "down":
                continue
            reason = str(state.get("last_reason") or "")
            params = state.get("last_params") or {}
            detail = t(reason, **params) if reason else t("hosts.alert.noData")
            since = _float_value(state.get("since"), now, 0.0, now)
            alerts.append({
                "id": f"host:{item['id']}",
                "severity": "critical",
                "title": t("hosts.alert.down", name=item["name"]),
                "detail": detail,
                "source": t("hosts.source"),
                "value": _target(item),
                "since": iso(since),
            })
        return alerts

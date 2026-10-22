"""Сборщик метрик: сэмплы в фоновом потоке, история для графиков, алерты.

Один экземпляр на процесс приложения. Даёт:
  * snapshot()      — последние значения (для дашборда и WebSocket-пуша);
  * history()       — окно истории по запрошенным сериям;
  * processes()     — список процессов для таблицы;
  * alerts          — активные проблемы (пороги, контейнеры, балансировщик).

Метрики читаются через psutil; если он недоступен, приложение всё равно
работает и отдаёт то, что можно получить стандартной библиотекой.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from .config import Config
from .i18n import t
from .systeminfo import host_memory, static_info
from .util import human_bytes, iso

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover
    psutil = None  # type: ignore

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}

#: Серии, которые складываются в историю (ключ -> человекочитаемое имя в UI).
SERIES_LABELS = {
    "cpu": "CPU, %",
    "mem_pct": "Память, %",
    "swap_pct": "Swap, %",
    "load1": "Load average 1м",
    "net_rx": "Сеть приём, Б/с",
    "net_tx": "Сеть отдача, Б/с",
    "disk_read": "Диск чтение, Б/с",
    "disk_write": "Диск запись, Б/с",
    "disk_pct": "Диск (макс. раздел), %",
    "proc_count": "Процессов",
    "temp_max": "Температура, °C",
}


class Collector:
    """Фоновый сбор метрик + расчёт алертов."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        interval_ms = int(cfg.get("monitor.interval_ms", 2000) or 2000)
        self.interval = max(0.5, interval_ms / 1000.0)
        history = int(cfg.get("monitor.history_points", 1800) or 1800)
        self.points: deque[Dict[str, Any]] = deque(maxlen=history)

        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

        self.latest: Dict[str, Any] = {}
        self.state: Dict[str, Any] = {
            "running": False,
            "started_at": None,
            "samples": 0,
            "interval_ms": interval_ms,
            "history_points": history,
            "psutil": psutil is not None,
            "last_error": "",
            "last_sample_ts": None,
        }
        self._prev: Dict[str, Any] = {}
        self._proc_cache: Dict[int, Any] = {}
        # Тяжёлые обходы процессов кэшируются: число потоков — на 15 с,
        # лёгкий список процессов — на 2 с, полный (для поиска) — тоже на 2 с.
        self._thread_count_cache: Tuple[float, int] = (0.0, 0)
        self._fast_rows_cache: Optional[Tuple[float, List[Dict[str, Any]]]] = None
        self._proc_rows_cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
        self._pending_alerts: Dict[str, float] = {}
        self.alerts: Dict[str, Dict[str, Any]] = {}
        self.alert_providers: Dict[str, Callable[[], List[Dict[str, Any]]]] = {}
        self._provider_ts: Dict[str, float] = {}
        self.subscribers: Set[Any] = set()
        self._last_full: Dict[str, Any] = {}
        self._last_full_ts = 0.0

    # ------------------------------------------------------------------ #
    #  Жизненный цикл
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="megatool-collector", daemon=True)
        self._thread.start()
        self.state["running"] = True
        self.state["started_at"] = iso()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        self.state["running"] = False

    # ------------------------------------------------------------------ #
    #  Подписки для WebSocket
    # ------------------------------------------------------------------ #
    def subscribe(self, queue: Any) -> None:
        with self._lock:
            self.subscribers.add(queue)

    def unsubscribe(self, queue: Any) -> None:
        with self._lock:
            self.subscribers.discard(queue)

    def _publish(self, payload: Dict[str, Any]) -> None:
        with self._lock:
            targets = list(self.subscribers)
        for queue in targets:
            try:
                if queue.qsize() > 8:  # клиент не успевает — не копим мусор
                    continue
                queue.put_nowait(payload)
            except Exception:
                self.subscribers.discard(queue)

    # ------------------------------------------------------------------ #
    #  Основной цикл
    # ------------------------------------------------------------------ #
    def _loop(self) -> None:
        while not self._stop.is_set():
            started = time.perf_counter()
            try:
                point = self._sample()
                with self._lock:
                    self.points.append(point)
                    self.latest = point
                    self.state["samples"] += 1
                    self.state["last_sample_ts"] = point["ts"]
                    self.state["last_error"] = ""
                self._refresh_alerts(point)
                self._publish({"type": "metrics", "point": point,
                               "alerts": self.active_alerts(),
                               "state": self.public_state()})
            except Exception as exc:  # сбор метрик не должен ломать приложение
                with self._lock:
                    self.state["last_error"] = f"{type(exc).__name__}: {exc}"
            elapsed = time.perf_counter() - started
            self._stop.wait(max(0.2, self.interval - elapsed))

    # ------------------------------------------------------------------ #
    #  Сэмпл
    # ------------------------------------------------------------------ #
    def _sample(self) -> Dict[str, Any]:
        ts = time.time()
        point: Dict[str, Any] = {"ts": ts, "iso": iso(ts)}

        if psutil is not None:
            point["cpu"] = round(psutil.cpu_percent(interval=None), 1)
            point["cpu_per_core"] = [round(v, 1) for v in psutil.cpu_percent(percpu=True, interval=None)]
            try:
                times = psutil.cpu_times_percent(interval=None)
                point["cpu_user"] = round(getattr(times, "user", 0.0), 1)
                point["cpu_sys"] = round(getattr(times, "system", 0.0), 1)
                point["cpu_iowait"] = round(getattr(times, "iowait", 0.0), 1)
                point["cpu_idle"] = round(getattr(times, "idle", 0.0), 1)
            except Exception:
                pass
            try:
                load = psutil.getloadavg()
                point["load1"], point["load5"], point["load15"] = (round(v, 2) for v in load)
            except Exception:
                point["load1"] = point["load5"] = point["load15"] = None
        else:
            point.update({"cpu": None, "cpu_per_core": [], "load1": None, "load5": None, "load15": None})

        mem = host_memory()
        point["mem_total"] = mem.get("total", 0)
        point["mem_used"] = mem.get("used", 0)
        point["mem_available"] = mem.get("available", 0)
        point["mem_pct"] = mem.get("percent", 0.0)
        point["swap_total"] = mem.get("swap_total", 0)
        point["swap_used"] = mem.get("swap_used", 0)
        point["swap_pct"] = mem.get("swap_percent", 0.0)

        point["proc_count"] = 0
        point["thread_count"] = 0
        if psutil is not None:
            try:
                point["proc_count"] = len(psutil.pids())
            except Exception:
                pass
            # Обход всех процессов ради числа потоков — самая дорогая часть
            # сэмпла (на загруженных машинах занимает секунды), поэтому
            # обновляем его не чаще раза в 15 секунд.
            now_mono = time.monotonic()
            if now_mono - self._thread_count_cache[0] > 15.0:
                try:
                    total = sum(p.info["num_threads"] or 0 for p in self._iter_procs(["num_threads"]))
                    self._thread_count_cache = (now_mono, total)
                except Exception:
                    self._thread_count_cache = (now_mono, self._thread_count_cache[1])
            point["thread_count"] = self._thread_count_cache[1]

        # сеть и диск — считаем скорости по дельте счётчиков
        net = self._net_counters()
        diskio = self._disk_counters()
        prev = self._prev
        dt = max(0.001, ts - prev.get("ts", ts - self.interval))
        point["net_rx"] = max(0.0, (net["rx"] - prev.get("net_rx_total", net["rx"])) / dt) if prev else 0.0
        point["net_tx"] = max(0.0, (net["tx"] - prev.get("net_tx_total", net["tx"])) / dt) if prev else 0.0
        point["disk_read"] = max(0.0, (diskio["read"] - prev.get("disk_read_total", diskio["read"])) / dt) if prev else 0.0
        point["disk_write"] = max(0.0, (diskio["write"] - prev.get("disk_write_total", diskio["write"])) / dt) if prev else 0.0
        point["net"] = net
        point["diskio"] = diskio

        disks = self._disk_usage()
        point["disks"] = disks
        point["disk_pct"] = max([d["percent"] for d in disks], default=0.0)
        point["disk_free_min"] = min([d["free"] for d in disks], default=0)

        point["temps"] = self._temps()
        point["temp_max"] = max([t["current"] for t in point["temps"] if t.get("current") is not None], default=None)

        self._prev = {
            "ts": ts,
            "net_rx_total": net["rx"],
            "net_tx_total": net["tx"],
            "disk_read_total": diskio["read"],
            "disk_write_total": diskio["write"],
        }
        for key in ("cpu", "mem_pct"):
            if point.get(key) is not None:
                self._prev[key] = point[key]
        return point

    def _iter_procs(self, attrs: Iterable[str]):
        if psutil is None:
            return []
        try:
            return psutil.process_iter(attrs=list(attrs), ad_value=None)
        except Exception:
            return []

    def _net_counters(self) -> Dict[str, Any]:
        if psutil is None:
            return {"rx": 0, "tx": 0, "packets_rx": 0, "packets_tx": 0, "errors": 0, "drops": 0, "per_nic": []}
        try:
            total = psutil.net_io_counters()
            per_nic = []
            for name, counters in (psutil.net_io_counters(pernic=True) or {}).items():
                if name.lower().startswith(("lo", "loopback")):
                    continue
                per_nic.append({
                    "name": name,
                    "rx": counters.bytes_recv,
                    "tx": counters.bytes_sent,
                    "errors": counters.errin + counters.errout,
                    "drops": counters.dropin + counters.dropout,
                })
            return {
                "rx": total.bytes_recv, "tx": total.bytes_sent,
                "packets_rx": total.packets_recv, "packets_tx": total.packets_sent,
                "errors": total.errin + total.errout, "drops": total.dropin + total.dropout,
                "per_nic": per_nic,
            }
        except Exception:
            return {"rx": 0, "tx": 0, "packets_rx": 0, "packets_tx": 0, "errors": 0, "drops": 0, "per_nic": []}

    def _disk_counters(self) -> Dict[str, Any]:
        if psutil is None or not hasattr(psutil, "disk_io_counters"):
            return {"read": 0, "write": 0, "read_iops": 0, "write_iops": 0, "per_disk": []}
        try:
            total = psutil.disk_io_counters()
            if total is None:
                return {"read": 0, "write": 0, "read_iops": 0, "write_iops": 0, "per_disk": []}
            per_disk = []
            for name, counters in (psutil.disk_io_counters(perdisk=True) or {}).items():
                per_disk.append({
                    "name": name,
                    "read": counters.read_bytes, "write": counters.write_bytes,
                    "read_iops": counters.read_count, "write_iops": counters.write_count,
                    "busy_ms": getattr(counters, "busy_time", 0),
                })
            return {
                "read": total.read_bytes, "write": total.write_bytes,
                "read_iops": total.read_count, "write_iops": total.write_count,
                "per_disk": sorted(per_disk, key=lambda d: -(d["read"] + d["write"]))[:8],
            }
        except Exception:
            return {"read": 0, "write": 0, "read_iops": 0, "write_iops": 0, "per_disk": []}

    def _disk_usage(self) -> List[Dict[str, Any]]:
        return static_info().get("disks", [])

    def _temps(self) -> List[Dict[str, Any]]:
        return static_info().get("temperatures", [])

    # ------------------------------------------------------------------ #
    #  Алерты
    # ------------------------------------------------------------------ #
    def _refresh_alerts(self, point: Dict[str, Any]) -> None:
        thresholds = self.cfg.get("monitor.thresholds", {}) or {}
        sustain = float(thresholds.get("sustain_sec", 20) or 20)
        cores = (static_info().get("cpu", {}).get("cores_logical") or os.cpu_count() or 1)
        now = point["ts"]

        checks: List[Tuple[str, bool, str, str, Any, str]] = [
            ("cpu", (point.get("cpu") or 0) >= float(thresholds.get("cpu_pct", 90)),
             "critical", t("collector.cpuHigh"),
             t("collector.cpuHighDetail", value=point.get("cpu"), limit=thresholds.get("cpu_pct")),
             point.get("cpu")),
            ("mem", (point.get("mem_pct") or 0) >= float(thresholds.get("mem_pct", 90)),
             "critical", t("collector.memLow"),
             t("collector.memLowDetail", value=point.get("mem_pct"), limit=thresholds.get("mem_pct")),
             point.get("mem_pct")),
            ("swap", (point.get("swap_pct") or 0) >= float(thresholds.get("swap_pct", 60)),
             "warning", t("collector.swapHigh"),
             t("collector.swapHighDetail", value=point.get("swap_pct"), limit=thresholds.get("swap_pct")),
             point.get("swap_pct")),
            ("disk", (point.get("disk_pct") or 0) >= float(thresholds.get("disk_pct", 90)),
             "critical", t("collector.diskLow"),
             t("collector.diskLowDetail", value=point.get("disk_pct")), point.get("disk_pct")),
            ("load", (point.get("load1") or 0) >= float(thresholds.get("load_per_core", 2)) * cores,
             "warning", t("collector.loadHigh"),
             t("collector.loadHighDetail", value=point.get("load1"), cores=cores), point.get("load1")),
            ("temp", point.get("temp_max") is not None and point["temp_max"] >= float(thresholds.get("temp_c", 85)),
             "warning", t("collector.tempHigh"),
             t("collector.tempHighDetail", value=point.get("temp_max")), point.get("temp_max")),
        ]

        with self._lock:
            for key, active, severity, title, detail, value in checks:
                if active:
                    first = self._pending_alerts.setdefault(key, now)
                    if now - first >= sustain:
                        self.alerts[key] = {
                            "id": key, "severity": severity, "title": title, "detail": detail,
                            "value": value, "since": iso(first), "since_ts": first,
                            "source": t("collector.sourceSystem"),
                            "active": True,
                        }
                else:
                    self._pending_alerts.pop(key, None)
                    self.alerts.pop(key, None)

        # внешние провайдеры (контейнеры, балансировщик) опрашиваем реже
        poll = float(self.cfg.get("monitor.container_poll_sec", 30) or 30)
        for name, provider in list(self.alert_providers.items()):
            if now - self._provider_ts.get(name, 0) < poll:
                continue
            self._provider_ts[name] = now
            try:
                fresh = provider() or []
            except Exception as exc:
                fresh = [{"id": f"{name}-error", "severity": "info", "source": name,
                          "title": t("collector.providerFailed", name=name), "detail": str(exc)}]
            with self._lock:
                for key in [k for k, a in self.alerts.items() if a.get("source_key") == name]:
                    self.alerts.pop(key, None)
                for item in fresh:
                    if not item.get("id"):
                        continue
                    item.setdefault("severity", "warning")
                    item.setdefault("source", name)
                    item["source_key"] = name
                    item["active"] = True
                    self.alerts[item["id"]] = item

    def active_alerts(self) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self.alerts.values())
        items.sort(key=lambda a: (SEVERITY_ORDER.get(a.get("severity", "info"), 3), a.get("title", "")))
        return items

    def clear_alert(self, alert_id: str) -> bool:
        with self._lock:
            return self.alerts.pop(alert_id, None) is not None

    # ------------------------------------------------------------------ #
    #  Публичные представления
    # ------------------------------------------------------------------ #
    def public_state(self) -> Dict[str, Any]:
        with self._lock:
            state = dict(self.state)
            state["buffered_points"] = len(self.points)
            state["subscribers"] = len(self.subscribers)
            state["alerts"] = len(self.alerts)
        state["last_point_iso"] = iso(state.get("last_sample_ts")) if state.get("last_sample_ts") else None
        return state

    def snapshot(self, with_details: bool = True) -> Dict[str, Any]:
        """Последние метрики. with_details добавляет диски/сеть/температуры/топ процессов."""
        now = time.time()
        if with_details and self._last_full and now - self._last_full_ts < 1.0:
            return self._last_full
        with self._lock:
            point = dict(self.latest) if self.latest else {}
        if not point:
            point = self._sample()
        data = {
            "point": point,
            "alerts": self.active_alerts(),
            "state": self.public_state(),
            "info": static_info(),
        }
        if with_details:
            data["processes"] = self.processes(limit=12)
            data["connections"] = self.connection_summary()
        self._last_full = data
        self._last_full_ts = now
        return data

    def history(self, minutes: int = 30, series: Optional[List[str]] = None) -> Dict[str, Any]:
        """Окно истории. series=None — все известные серии."""
        series = list(series or SERIES_LABELS.keys())
        cutoff = time.time() - max(1, minutes) * 60
        with self._lock:
            points = [p for p in self.points if p["ts"] >= cutoff]
        out = {"ts": [round(p["ts"], 1) for p in points], "series": {}, "labels": {}, "points": len(points)}
        for key in series:
            values = []
            for p in points:
                value = p.get(key)
                values.append(round(value, 3) if isinstance(value, (int, float)) else None)
            out["series"][key] = values
            # SERIES_LABELS хранит русский текст; для UI отдаём перевод по ключу.
            out["labels"][key] = t(f"collector.series.{key}") if key in SERIES_LABELS else key
        return out

    def summarize(self) -> Dict[str, Any]:
        """Короткая сводка для верхней панели/карточек."""
        point = self.latest or {}
        return {
            "cpu": point.get("cpu"),
            "mem_pct": point.get("mem_pct"),
            "mem_used_human": human_bytes(point.get("mem_used")),
            "mem_total_human": human_bytes(point.get("mem_total")),
            "swap_pct": point.get("swap_pct"),
            "disk_pct": point.get("disk_pct"),
            "load1": point.get("load1"),
            "net_rx_human": human_bytes(point.get("net_rx")) + "/с" if point.get("net_rx") is not None else "—",
            "net_tx_human": human_bytes(point.get("net_tx")) + "/с" if point.get("net_tx") is not None else "—",
            "proc_count": point.get("proc_count"),
            "temp_max": point.get("temp_max"),
        }

    # ------------------------------------------------------------------ #
    #  Процессы
    # ------------------------------------------------------------------ #
    def processes(self, sort: str = "cpu", limit: int = 50, query: str = "") -> List[Dict[str, Any]]:
        if psutil is None:
            return []
        query = (query or "").lower().strip()
        limit = max(1, limit)
        if query:
            # Поиск требует точного совпадения по имени/командной строке —
            # идём полным обходом (он кэшируется по строке запроса).
            rows = [dict(row) for row in self._scan_processes(query)]
            rows.sort(key=self._process_sort_key(sort), reverse=sort != "name")
            return rows[:limit]
        return self._processes_fast(sort, limit)

    @staticmethod
    def _process_sort_key(sort: str):
        key_map = {
            "cpu": lambda r: r["cpu"],
            "mem": lambda r: r["rss"],
            "pid": lambda r: r["pid"],
            "name": lambda r: (r["name"] or "").lower(),
            "cpu_time": lambda r: r["cpu"],
        }
        return key_map.get(sort, key_map["cpu"])

    def _processes_fast(self, sort: str, limit: int) -> List[Dict[str, Any]]:
        """Быстрый путь для таблицы процессов: лёгкий обход всех процессов,
        затем подробности только для тех, кто попал в топ.

        Полный обход с cmdline/username/memory_info дорог; здесь эти поля
        читаются лишь для `limit` процессов (обычно 12–50).
        """
        cached = self._fast_rows_cache
        if cached is not None and time.monotonic() - cached[0] < 2.0:
            rows = cached[1]
        else:
            rows = []
            for proc in self._iter_procs(["pid", "name", "cpu_percent", "memory_percent"]):
                try:
                    rows.append({
                        "pid": proc.info["pid"],
                        "name": proc.info.get("name") or "?",
                        "cpu": round(float(proc.info.get("cpu_percent") or 0.0), 1),
                        "mem_pct": round(float(proc.info.get("memory_percent") or 0.0), 1),
                    })
                except Exception:
                    continue
            self._proc_cache = {row["pid"]: None for row in rows}
            self._fast_rows_cache = (time.monotonic(), rows)
        if sort == "mem":
            ordered = sorted(rows, key=lambda r: r["mem_pct"], reverse=True)
        elif sort == "name":
            ordered = sorted(rows, key=lambda r: (r["name"] or "").lower())
        elif sort == "pid":
            ordered = sorted(rows, key=lambda r: r["pid"], reverse=True)
        else:  # cpu / cpu_time / прочее
            ordered = sorted(rows, key=lambda r: r["cpu"], reverse=True)
        out: List[Dict[str, Any]] = []
        for row in ordered[:limit]:
            detail = self._process_details(row["pid"])
            if detail is None:
                continue
            detail["cpu"] = row["cpu"]
            detail["mem_pct"] = row["mem_pct"]
            out.append(detail)
        return out

    @staticmethod
    def _process_details(pid: int) -> Optional[Dict[str, Any]]:
        """Подробности одного процесса: недоступные поля подменяются заглушками."""
        try:
            proc = psutil.Process(pid)
        except Exception:
            return None

        def safe(getter, default=None):
            try:
                return getter()
            except Exception:
                return default

        name = safe(proc.name) or "?"
        cmdline = safe(lambda: proc.cmdline() or []) or []
        rss = safe(lambda: proc.memory_info().rss, 0) or 0
        return {
            "pid": pid,
            "ppid": safe(proc.ppid),
            "name": name,
            "user": safe(proc.username) or "—",
            "status": safe(proc.status),
            "rss": rss,
            "rss_human": human_bytes(rss),
            "threads": safe(proc.num_threads),
            "created": safe(proc.create_time),
            "cmdline": " ".join(str(c) for c in cmdline)[:300] or name,
        }

    def _scan_processes(self, query: str = "") -> List[Dict[str, Any]]:
        """Полный обход процессов (для поиска) с коротким кэшем на 2 секунды."""
        cached = self._proc_rows_cache.get(query)
        if cached is not None and time.monotonic() - cached[0] < 2.0:
            return cached[1]
        attrs = ["pid", "ppid", "name", "username", "status", "memory_info",
                 "num_threads", "create_time", "cmdline", "cpu_percent", "memory_percent"]
        rows: List[Dict[str, Any]] = []
        alive: Set[int] = set()
        for proc in self._iter_procs(attrs):
            try:
                pid = proc.info["pid"]
                alive.add(pid)
                name = proc.info.get("name") or "?"
                cmdline = proc.info.get("cmdline") or []
                if query and query not in name.lower() and not any(query in str(c) for c in cmdline):
                    continue
                rss = proc.info.get("memory_info").rss if proc.info.get("memory_info") else 0
                rows.append({
                    "pid": pid,
                    "ppid": proc.info.get("ppid"),
                    "name": name,
                    "user": proc.info.get("username") or "—",
                    "status": proc.info.get("status"),
                    "cpu": round(proc.info.get("cpu_percent") or 0.0, 1),
                    "mem_pct": round(proc.info.get("memory_percent") or 0.0, 1),
                    "rss": rss,
                    "rss_human": human_bytes(rss),
                    "threads": proc.info.get("num_threads"),
                    "created": proc.info.get("create_time"),
                    "cmdline": " ".join(str(c) for c in cmdline)[:300] or name,
                })
            except Exception:
                continue
        self._proc_cache = {pid: None for pid in alive}
        self._proc_rows_cache = {query: (time.monotonic(), rows)}
        return rows

    def process_detail(self, pid: int) -> Optional[Dict[str, Any]]:
        if psutil is None:
            return None
        try:
            proc = psutil.Process(pid)
            with proc.oneshot():
                info = {
                    "pid": pid,
                    "name": proc.name(),
                    "exe": proc.exe() if proc.exe() else "",
                    "cwd": proc.cwd(),
                    "cmdline": proc.cmdline(),
                    "status": proc.status(),
                    "user": proc.username(),
                    "created": proc.create_time(),
                    "created_iso": iso(proc.create_time()),
                    "parent": proc.ppid(),
                    "threads": proc.num_threads(),
                    "memory": proc.memory_info()._asdict(),
                    "cpu_percent": proc.cpu_percent(interval=None),
                    "cpu_times": proc.cpu_times()._asdict(),
                    "open_files": len(proc.open_files() or []),
                    "connections": len(proc.net_connections(kind="inet") or []),
                    "nice": proc.nice(),
                }
            return info
        except Exception as exc:
            return {"pid": pid, "error": str(exc)}

    def kill_process(self, pid: int, force: bool = False) -> Dict[str, Any]:
        if psutil is None:
            return {"ok": False, "error": t("collector.psutilMissing")}
        if not self.cfg.get("safety.allow_process_kill", True):
            return {"ok": False, "error": t("collector.killDisabled")}
        if pid <= 4:
            return {"ok": False, "error": t("collector.systemProcess")}
        try:
            proc = psutil.Process(pid)
            name = proc.name()
            if force:
                proc.kill()
            else:
                proc.terminate()
            return {"ok": True, "pid": pid, "name": name, "signal": "kill" if force else "terminate"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ------------------------------------------------------------------ #
    #  Сеть
    # ------------------------------------------------------------------ #
    def connection_summary(self) -> Dict[str, Any]:
        if psutil is None:
            return {"available": False, "total": 0, "by_status": {}, "listening": 0}
        try:
            conns = psutil.net_connections(kind="inet")
        except Exception:
            return {"available": False, "total": 0, "by_status": {}, "listening": 0,
                    "error": t("collector.adminRequired")}
        by_status: Dict[str, int] = {}
        listening = 0
        for conn in conns:
            status = conn.status or "NONE"
            by_status[status] = by_status.get(status, 0) + 1
            if status == "LISTEN":
                listening += 1
        return {"available": True, "total": len(conns), "by_status": by_status, "listening": listening}

    def connections(self, limit: int = 100) -> List[Dict[str, Any]]:
        if psutil is None:
            return []
        out = []
        try:
            for conn in psutil.net_connections(kind="inet")[:limit]:
                name = ""
                if conn.pid:
                    try:
                        name = psutil.Process(conn.pid).name()
                    except Exception:
                        name = ""
                local = f"{conn.laddr.ip}:{conn.laddr.port}" if conn.laddr else ""
                remote = f"{conn.raddr.ip}:{conn.raddr.port}" if conn.raddr else ""
                out.append({
                    "fd": conn.fd, "type": "TCP" if conn.type == 1 else "UDP",
                    "local": local, "remote": remote, "status": conn.status,
                    "pid": conn.pid, "process": name,
                })
        except Exception:
            return []
        return out

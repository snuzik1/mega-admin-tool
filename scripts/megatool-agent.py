#!/usr/bin/env python3
"""MegaTool Agent — лёгкий агент мониторинга для удалённых машин.

Один файл, только стандартная библиотека: копируется на сервер и запускается
командой `python3 megatool-agent.py --host 0.0.0.0 --token СЕКРЕТ`.
Отдаёт JSON с CPU/RAM/swap/диском/сетью/uptime по адресу /metrics.

Безопасность: по умолчанию слушает только 127.0.0.1. Если открываете агент
в сеть, обязательно задайте токен (--token или MEGATOOL_AGENT_TOKEN):
запросы без токена будут отклоняться.

Поддерживаются Linux (через /proc) и Windows (через ctypes); при наличии
psutil используются его данные для температуры и сетевых счётчиков.
"""

from __future__ import annotations
from typing import Dict, List, Optional, Tuple

import argparse
import ctypes
import hmac
import json
import os
import platform
import shutil
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

try:  # необязательное улучшение: температура, сеть, macOS
    import psutil  # type: ignore
except Exception:  # pragma: no cover - агент должен работать и без psutil
    psutil = None  # type: ignore

AGENT_VERSION = "1.0"
CACHE_TTL = 1.0
DISK_PATH = "/" if os.name != "nt" else "C:\\"
TOKEN = ""

_LOCK = threading.Lock()
_CACHE: Dict[str, object] = {"ts": 0.0, "data": None}

IS_WINDOWS = os.name == "nt"
IS_LINUX = sys.platform.startswith("linux")


# --------------------------------------------------------------------------- #
#  CPU: два замера подряд, разница отдаётся в процентах
# --------------------------------------------------------------------------- #

class _CpuSampler:
    def __init__(self) -> None:
        self._prev: Optional[Tuple[int, int]] = None
        self._init = threading.Lock()
        self._prime()

    def _read(self) -> Optional[Tuple[int, int]]:
        """Возвращает (всего тиков, простой) или None, если ОС не поддерживается."""
        if IS_LINUX:
            try:
                with open("/proc/stat", "r", encoding="ascii") as fh:
                    parts = fh.readline().split()
                values = [int(item) for item in parts[1:]]
                idle = values[3] + (values[4] if len(values) > 4 else 0)
                return sum(values), idle
            except (OSError, ValueError, IndexError):
                return None
        if IS_WINDOWS:
            try:
                class FILETIME(ctypes.Structure):
                    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

                def to_int(ft: "FILETIME") -> int:
                    return (ft.high << 32) | ft.low

                idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
                kernel32 = ctypes.windll.kernel32
                if not kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
                    return None
                total = to_int(kernel) + to_int(user)
                return total, to_int(idle)
            except Exception:  # pragma: no cover - старые/экзотические Windows
                return None
        return None

    def _prime(self) -> None:
        with self._init:
            self._prev = self._read()

    def percent(self) -> float:
        current = self._read()
        if current is None:
            if psutil is not None:
                try:
                    return float(psutil.cpu_percent(interval=None))
                except Exception:
                    pass
            return 0.0
        prev = self._prev
        self._prev = current
        if prev is None:
            return 0.0
        total = current[0] - prev[0]
        idle = current[1] - prev[1]
        if total <= 0:
            return 0.0
        return round(max(0.0, min(100.0, (1.0 - idle / total) * 100.0)), 1)


CPU = _CpuSampler()


# --------------------------------------------------------------------------- #
#  Память, диск, uptime, сеть
# --------------------------------------------------------------------------- #

def _linux_meminfo() -> Dict[str, int]:
    values: Dict[str, int] = {}
    try:
        with open("/proc/meminfo", "r", encoding="ascii") as fh:
            for line in fh:
                if ":" not in line:
                    continue
                key, _, rest = line.partition(":")
                number = rest.strip().split(" ")[0]
                try:
                    values[key] = int(number) * 1024
                except ValueError:
                    continue
    except OSError:
        pass
    return values


def _windows_memory() -> Tuple[int, int, int, int]:
    """total, available, swap_total, swap_available (в байтах)."""
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    try:
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return 0, 0, 0, 0
    except Exception:  # pragma: no cover
        return 0, 0, 0, 0
    swap_total = max(0, int(status.ullTotalPageFile) - int(status.ullTotalPhys))
    swap_avail = max(0, int(status.ullAvailPageFile) - int(status.ullAvailPhys))
    return int(status.ullTotalPhys), int(status.ullAvailPhys), swap_total, swap_avail


def _uptime() -> float:
    if IS_LINUX:
        try:
            with open("/proc/uptime", "r", encoding="ascii") as fh:
                return float(fh.read().split()[0])
        except (OSError, ValueError, IndexError):
            pass
    if IS_WINDOWS:
        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.GetTickCount64.restype = ctypes.c_ulonglong
            return float(kernel32.GetTickCount64()) / 1000.0
        except Exception:  # pragma: no cover
            pass
    if psutil is not None:
        try:
            return max(0.0, time.time() - psutil.boot_time())
        except Exception:
            pass
    return 0.0


def _net_totals() -> Tuple[Optional[int], Optional[int]]:
    if IS_LINUX:
        rx = tx = 0
        try:
            with open("/proc/net/dev", "r", encoding="ascii") as fh:
                for line in fh.readlines()[2:]:
                    name, _, rest = line.partition(":")
                    if name.strip() == "lo":
                        continue
                    fields = rest.split()
                    if len(fields) >= 9:
                        rx += int(fields[0])
                        tx += int(fields[8])
            return rx, tx
        except (OSError, ValueError):
            pass
    if psutil is not None:
        try:
            counters = psutil.net_io_counters()
            return int(counters.bytes_recv), int(counters.bytes_sent)
        except Exception:
            pass
    return None, None


def _temperature() -> Optional[float]:
    if psutil is None:
        return None
    try:
        sensors = psutil.sensors_temperatures()  # type: ignore[attr-defined]
    except Exception:
        return None
    values = [entry.current for group in (sensors or {}).values() for entry in group if entry.current is not None]
    return round(max(values), 1) if values else None


def collect_metrics() -> Dict[str, object]:
    """Собирает показатели машины. Результат кэшируется на секунду."""
    now = time.time()
    with _LOCK:
        if _CACHE["data"] is not None and now - float(_CACHE["ts"]) < CACHE_TTL:  # type: ignore[arg-type]
            return dict(_CACHE["data"])  # type: ignore[arg-type]

    mem_total = mem_available = 0
    swap_total = swap_available = 0
    if IS_LINUX:
        info = _linux_meminfo()
        mem_total = info.get("MemTotal", 0)
        mem_available = info.get("MemAvailable", info.get("MemFree", 0))
        swap_total = info.get("SwapTotal", 0)
        swap_available = info.get("SwapFree", 0)
    elif IS_WINDOWS:
        mem_total, mem_available, _, _ = _windows_memory()
        if psutil is not None:
            # Файл подкачки Windows не равен swap: psutil даёт честные цифры.
            try:
                sm = psutil.swap_memory()
                swap_total, swap_available = int(sm.total), int(sm.free)
            except Exception:
                pass
    elif psutil is not None:
        try:
            vm = psutil.virtual_memory()
            sm = psutil.swap_memory()
            mem_total, mem_available = int(vm.total), int(vm.available)
            swap_total, swap_available = int(sm.total), int(sm.free)
        except Exception:
            pass

    mem_used = max(0, mem_total - mem_available)
    swap_used = max(0, swap_total - swap_available)

    try:
        disk = shutil.disk_usage(DISK_PATH)
        disk_total, disk_used = int(disk.total), int(disk.used)
    except OSError:
        disk_total = disk_used = 0

    load1 = load5 = load15 = None
    try:
        load1, load5, load15 = os.getloadavg()
    except (AttributeError, OSError):
        pass

    net_rx, net_tx = _net_totals()

    data: Dict[str, object] = {
        "ok": True,
        "service": "megatool-agent",
        "agent_version": AGENT_VERSION,
        "ts": round(now, 3),
        "host": socket.gethostname(),
        "os": platform.system(),
        "os_pretty": platform.platform(),
        "uptime": round(_uptime(), 1),
        "cores": os.cpu_count() or 1,
        "cpu_pct": CPU.percent(),
        "load1": round(load1, 2) if load1 is not None else None,
        "load5": round(load5, 2) if load5 is not None else None,
        "load15": round(load15, 2) if load15 is not None else None,
        "mem_total": mem_total,
        "mem_used": mem_used,
        "mem_pct": round(mem_used / mem_total * 100.0, 1) if mem_total else 0.0,
        "swap_total": swap_total,
        "swap_used": swap_used,
        "swap_pct": round(swap_used / swap_total * 100.0, 1) if swap_total else 0.0,
        "disk_path": DISK_PATH,
        "disk_total": disk_total,
        "disk_used": disk_used,
        "disk_pct": round(disk_used / disk_total * 100.0, 1) if disk_total else 0.0,
        "net_rx": net_rx,
        "net_tx": net_tx,
        "temp_c": _temperature(),
    }
    with _LOCK:
        _CACHE.update({"ts": now, "data": data})
    return dict(data)


# --------------------------------------------------------------------------- #
#  HTTP-сервер
# --------------------------------------------------------------------------- #

class AgentHandler(BaseHTTPRequestHandler):
    server_version = f"MegaToolAgent/{AGENT_VERSION}"
    sys_version = ""

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: A003 - API stdlib
        if args and str(args[0]).startswith(("GET", "HEAD")):
            sys.stderr.write("[agent] %s %s\n" % (self.address_string(), fmt % args))

    def _send(self, status: int, payload: Dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        if not TOKEN:
            return True
        header = self.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            if hmac.compare_digest(header[7:].strip(), TOKEN):
                return True
        if hmac.compare_digest(self.headers.get("X-MegaTool-Agent-Token", ""), TOKEN):
            return True
        query = parse_qs(urlsplit(self.path).query)
        provided = (query.get("token") or [""])[0]
        return bool(provided) and hmac.compare_digest(provided, TOKEN)

    def do_GET(self) -> None:  # noqa: N802 - API stdlib
        path = urlsplit(self.path).path.rstrip("/") or "/"
        if path in ("/health", "/healthz"):
            self._send(200, {"ok": True, "service": "megatool-agent", "version": AGENT_VERSION})
            return
        if path not in ("/metrics", "/"):
            self._send(404, {"ok": False, "error": "not found"})
            return
        if not self._authorized():
            self._send(401, {"ok": False, "error": "token required"})
            return
        try:
            self._send(200, collect_metrics())
        except Exception as exc:  # pragma: no cover - защита от неожиданностей
            self._send(500, {"ok": False, "error": str(exc)[:200]})

    def do_HEAD(self) -> None:  # noqa: N802 - API stdlib
        if not self._authorized():
            self.send_response(401)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()


def main(argv: Optional[List[str]] = None) -> int:
    global TOKEN, DISK_PATH
    parser = argparse.ArgumentParser(description="MegaTool Agent: лёгкий экспортёр метрик")
    parser.add_argument("--host", default="127.0.0.1", help="адрес прослушивания (по умолчанию 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8791, help="порт (по умолчанию 8791)")
    parser.add_argument("--token", default=os.environ.get("MEGATOOL_AGENT_TOKEN", ""),
                        help="токен доступа (или переменная MEGATOOL_AGENT_TOKEN)")
    parser.add_argument("--disk", default=DISK_PATH, help="точка монтирования для расчёта диска")
    args = parser.parse_args(argv)

    TOKEN = str(args.token or "")
    DISK_PATH = str(args.disk or DISK_PATH)

    server = ThreadingHTTPServer((args.host, args.port), AgentHandler)
    server.daemon_threads = True
    exposed = args.host not in ("127.0.0.1", "localhost", "::1")
    print(f"MegaTool Agent {AGENT_VERSION} слушает http://{args.host}:{args.port}/metrics")
    print(f"Машина: {socket.gethostname()} • {platform.platform()}")
    if exposed and not TOKEN:
        print("!! ВНИМАНИЕ: агент открыт в сеть без токена. Задайте --token или MEGATOOL_AGENT_TOKEN.")
    if not TOKEN:
        print("   Токен не задан: доступ разрешён только тем, кто может подключиться к порту.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановка агента.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

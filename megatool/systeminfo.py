"""Базовая информация о сервере/рабочей станции (меняется редко, кэшируется).

Используется psutil, если он установлен; там где это возможно есть запасные
варианты через stdlib (winreg, /proc, /etc/os-release).
"""

from __future__ import annotations

import os
import platform
import socket
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__
from .i18n import t
from .util import IS_LINUX, IS_WINDOWS, human_bytes, human_duration, iso, run

try:  # psutil — основная зависимость, но приложение должно жить и без него
    import psutil  # type: ignore
except Exception:  # pragma: no cover - редкий случай
    psutil = None  # type: ignore

_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}
_CACHE_TTL = 30.0


def reset_cache() -> None:
    """Сбрасывает кэш паспорта машины (например, при смене языка)."""
    _CACHE.update({"ts": 0.0, "data": None})


def _read_os_release() -> dict[str, str]:
    if not IS_LINUX:
        return {}
    out: dict[str, str] = {}
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                key, _, value = line.partition("=")
                out[key.strip()] = value.strip().strip('"')
    except OSError:
        pass
    return out


def cpu_model() -> str:
    """Название процессора: реестр Windows, /proc/cpuinfo Linux, затем platform."""
    if IS_WINDOWS:
        try:
            import winreg  # type: ignore

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as key:
                return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except Exception:
            pass
    if IS_LINUX:
        try:
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            pass
    text = platform.processor() or platform.machine()
    return text.strip() or t("systeminfo.unknown")


def virtualization() -> str:
    """Тип виртуализации: kvm, vmware, wsl, физическая машина и т.п."""
    if IS_LINUX:
        res = run(["systemd-detect-virt"], timeout=5)
        if res.ok and res.out.strip():
            return res.out.strip()
        if Path("/proc/1/cgroup").is_file() and "docker" in _safe_text("/proc/1/cgroup"):
            return "docker"
    if IS_WINDOWS:
        # Основная проверка для Windows — модель компьютера в Win32_ComputerSystem,
        # но ради скорости ограничиваемся свободным ответом без внешних вызовов.
        return "host"
    return t("systeminfo.noData")


def _safe_text(path: str, limit: int = 4096) -> str:
    try:
        data = Path(path).read_text(encoding="utf-8", errors="replace")
        return data[:limit] if limit else data
    except OSError:
        return ""


def is_admin() -> bool:
    if IS_WINDOWS:
        try:
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        except Exception:
            return False
    try:
        return os.geteuid() == 0  # type: ignore[attr-defined]
    except AttributeError:
        return False


def _disks() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if psutil is not None:
        for part in psutil.disk_partitions(all=False):
            if "cdrom" in (part.opts or "") or part.fstype == "":
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except PermissionError:
                continue
            items.append({
                "device": part.device,
                "mount": part.mountpoint,
                "fstype": part.fstype,
                "total": usage.total,
                "used": usage.used,
                "free": usage.free,
                "percent": round(usage.percent, 1),
            })
        return items
    # запасной вариант без psutil
    mounts = ["/"] if not IS_WINDOWS else [os.environ.get("SystemDrive", "C:") + "\\"]
    for mount in mounts:
        try:
            import shutil as _shutil

            usage = _shutil.disk_usage(mount)
            items.append({
                "device": mount, "mount": mount, "fstype": "—",
                "total": usage.total, "used": usage.used, "free": usage.free,
                "percent": round(usage.used / usage.total * 100, 1) if usage.total else 0.0,
            })
        except OSError:
            continue
    return items


def _nics() -> list[dict[str, Any]]:
    if psutil is None:
        return []
    stats = {}
    try:
        stats = psutil.net_if_stats()
    except Exception:
        stats = {}
    out: list[dict[str, Any]] = []
    for name, addrs in psutil.net_if_addrs().items():
        if name.lower().startswith(("lo", "loopback")):
            continue
        info = {
            "name": name,
            "up": bool(getattr(stats.get(name), "isup", False)),
            "speed_mbps": getattr(stats.get(name), "speed", 0) or 0,
            "mtu": getattr(stats.get(name), "mtu", 0) or 0,
            "ipv4": [],
            "ipv6": [],
            "mac": "",
        }
        for addr in addrs:
            family = getattr(addr, "family", None)
            fam_name = getattr(family, "name", str(family))
            if fam_name == "AF_INET":
                info["ipv4"].append(addr.address)
            elif fam_name == "AF_INET6":
                info["ipv6"].append(addr.address.split("%")[0])
            elif fam_name in ("AF_LINK", "AF_PACKET") or "MAC" in str(family):
                info["mac"] = addr.address
        out.append(info)
    return out


def _users() -> list[dict[str, Any]]:
    if psutil is None:
        return []
    out = []
    try:
        for user in psutil.users():
            out.append({
                "user": user.name,
                "terminal": user.terminal or "—",
                "host": user.host or t("systeminfo.local"),
                "since": user.started,
                "since_iso": iso(user.started),
            })
    except Exception:
        pass
    return out


def _temp_sensors() -> list[dict[str, Any]]:
    if psutil is None or not hasattr(psutil, "sensors_temperatures"):
        return []
    out = []
    try:
        for chip, entries in (psutil.sensors_temperatures() or {}).items():
            for entry in entries:
                out.append({
                    "chip": chip,
                    "label": entry.label or chip,
                    "current": round(entry.current, 1) if entry.current is not None else None,
                    "high": entry.high,
                    "critical": entry.critical,
                })
    except Exception:
        pass
    return out[:12]


def static_info(refresh: bool = False) -> dict[str, Any]:
    """Собирает паспорт машины. Кэш 30 секунд — данные меняются редко."""
    now = time.time()
    if not refresh and _CACHE["data"] and now - _CACHE["ts"] < _CACHE_TTL:
        return _CACHE["data"]

    os_release = _read_os_release()
    boot_time = psutil.boot_time() if psutil is not None else None
    mem_total = host_memory().get("total")

    data: dict[str, Any] = {
        "app": {
            "name": "MegaTool",
            "version": __version__,
            "python": platform.python_version(),
            "python_path": sys.executable,
        },
        "host": {
            "hostname": socket.gethostname(),
            "fqdn": socket.getfqdn(),
            "os": platform.system(),
            "os_release": platform.release(),
            "os_version": platform.version(),
            "os_pretty": os_release.get("PRETTY_NAME") or f"{platform.system()} {platform.release()}",
            "distro_id": os_release.get("ID", ""),
            "arch": platform.machine(),
            "kernel": platform.uname().version,
            "timezone": time.tzname[0] if time.tzname else "",
            "locale": os.environ.get("LANG", ""),
            "virtualization": virtualization(),
            "is_admin": is_admin(),
            "boot_time": boot_time,
            "boot_iso": iso(boot_time) if boot_time else None,
            "uptime": (now - boot_time) if boot_time else None,
            "uptime_human": human_duration(now - boot_time) if boot_time else "—",
        },
        "cpu": {
            "model": cpu_model(),
            "cores_logical": psutil.cpu_count(logical=True) if psutil else os.cpu_count(),
            "cores_physical": psutil.cpu_count(logical=False) if psutil else None,
            "freq_mhz": None,
            "load": None,
        },
        "memory": {
            "total": mem_total,
            "total_human": human_bytes(mem_total),
        },
        "disks": _disks(),
        "nics": _nics(),
        "users": _users(),
        "temperatures": _temp_sensors(),
        "capabilities": {
            "psutil": psutil is not None,
            "systemd": Path("/run/systemd/system").is_dir() if IS_LINUX else False,
            "windows": IS_WINDOWS,
            "linux": IS_LINUX,
        },
    }

    if psutil is not None:
        try:
            freq = psutil.cpu_freq()
            data["cpu"]["freq_mhz"] = round(freq.current, 0) if freq else None
            data["cpu"]["freq_max_mhz"] = round(freq.max, 0) if freq else None
        except Exception:
            pass
        try:
            if hasattr(psutil, "getloadavg"):
                data["cpu"]["load"] = [round(v, 2) for v in psutil.getloadavg()]
        except Exception:
            pass

    _CACHE.update({"ts": now, "data": data})
    return data


def host_memory() -> dict[str, Any]:
    """Объём памяти без psutil: ctypes на Windows, /proc/meminfo на Linux."""
    if psutil is not None:
        try:
            vm = psutil.virtual_memory()
            sm = psutil.swap_memory()
            return {
                "total": vm.total, "used": vm.used, "available": vm.available,
                "percent": round(vm.percent, 1),
                "swap_total": sm.total, "swap_used": sm.used,
                "swap_percent": round(sm.percent, 1),
            }
        except Exception:
            pass
    if IS_WINDOWS:
        try:
            import ctypes

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

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))  # type: ignore[attr-defined]
            total = int(stat.ullTotalPhys)
            avail = int(stat.ullAvailPhys)
            return {
                "total": total, "used": total - avail, "available": avail,
                "percent": round((total - avail) / total * 100, 1) if total else 0.0,
                "swap_total": int(stat.ullTotalPageFile),
                "swap_used": int(stat.ullTotalPageFile - stat.ullAvailPageFile),
                "swap_percent": 0.0,
            }
        except Exception:
            pass
    if IS_LINUX:
        info: dict[str, int] = {}
        try:
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                key, _, rest = line.partition(":")
                info[key.strip()] = int(rest.strip().split()[0]) * 1024
            total = info.get("MemTotal", 0)
            avail = info.get("MemAvailable", 0)
            return {
                "total": total, "used": total - avail, "available": avail,
                "percent": round((total - avail) / total * 100, 1) if total else 0.0,
                "swap_total": info.get("SwapTotal", 0),
                "swap_used": info.get("SwapTotal", 0) - info.get("SwapFree", 0),
                "swap_percent": 0.0,
            }
        except OSError:
            pass
    return {"total": 0, "used": 0, "available": 0, "percent": 0.0,
            "swap_total": 0, "swap_used": 0, "swap_percent": 0.0}

"""Сетевые помощники: loopback, локальные адреса и имена машины.

MegaTool по умолчанию слушает только 127.0.0.1. Для удалённого доступа
используются встроенный TLS и проверка Host/Origin (см. server.py), а не
только сетевой интерфейс.
"""

from __future__ import annotations

import functools
import ipaddress
import socket
from typing import FrozenSet

try:  # psutil уже является обязательной зависимостью проекта
    import psutil
except ImportError:  # pragma: no cover - аварийный режим без psutil
    psutil = None  # type: ignore[assignment]


def is_loopback(host: str) -> bool:
    text = str(host or "").strip()
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return text.lower() in ("localhost", "")


def is_wildcard(host: str) -> bool:
    return host in ("0.0.0.0", "::", "")


@functools.lru_cache(maxsize=1)
def local_addresses() -> FrozenSet[str]:
    """IP-адреса локальных интерфейсов, включая loopback.

    Нужны для автоматического allowlist Host и SAN сертификата: админ не
    должен вручную вписывать IP машины, чтобы открыть её по сети. Служебные
    адреса (unspecified, multicast, link-local) отбрасываются.
    """
    result = set()
    if psutil is not None:
        try:
            for entries in psutil.net_if_addrs().values():
                for entry in entries:
                    if entry.family not in (socket.AF_INET, socket.AF_INET6):
                        continue
                    raw = str(entry.address or "").split("%", 1)[0]
                    try:
                        address = ipaddress.ip_address(raw)
                    except ValueError:
                        continue
                    if address.is_unspecified or address.is_multicast:
                        continue
                    if address.is_link_local:
                        continue
                    result.add(str(address))
        except (OSError, RuntimeError):
            pass
    # На случай, если psutil не установлен или не отдал интерфейсы.
    for name in {socket.gethostname(), socket.getfqdn()}:
        if not name:
            continue
        try:
            for item in socket.getaddrinfo(name, None):
                raw = str(item[4][0]).split("%", 1)[0]
                try:
                    address = ipaddress.ip_address(raw)
                except ValueError:
                    continue
                if (not address.is_unspecified and not address.is_multicast
                        and not address.is_link_local):
                    result.add(str(address))
        except OSError:
            continue
    result.update({"127.0.0.1", "::1"})
    return frozenset(result)


@functools.lru_cache(maxsize=1)
def local_hostnames() -> FrozenSet[str]:
    """Имена и IP, по которым эта машина может быть адресована."""
    names = {"localhost", "127.0.0.1", "::1"}
    for name in (socket.gethostname(), socket.getfqdn()):
        text = str(name or "").strip().lower()
        if text:
            names.add(text)
            names.add(text.split(".", 1)[0])
    names.update(local_addresses())
    return frozenset(names)

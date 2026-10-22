"""Безопасность: определение loopback-адресов.

MegaTool по умолчанию слушает только 127.0.0.1. Отдельной авторизации в
приложении нет: доступ ограничивается сетевым интерфейсом, проверкой Host и
Origin (см. server.py) и правами операционной системы.
"""

from __future__ import annotations

import ipaddress


def is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in ("localhost", "")

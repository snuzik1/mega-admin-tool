"""Доступ и безопасность: токен на API, журнал действий, ограничения.

MegaTool по умолчанию слушает только 127.0.0.1 и не требует пароля. Если нужно
открыть его из локальной сети, включается токен: запросы без заголовка
`X-MegaTool-Token` (или `?token=`) отклоняются.
"""

from __future__ import annotations

import hmac
import ipaddress
from typing import Any

from .config import Config
from .i18n import t
from .util import iso, rand_token


class AuthManager:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    # ------------------------------------------------------------------ #
    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("auth.enabled", False))

    @property
    def token(self) -> str:
        return str(self.cfg.get("auth.token", "") or "")

    def rotate(self) -> str:
        token = rand_token(24)
        self.cfg.set("auth.token", token)
        self.cfg.set("auth.enabled", True)
        self.cfg.save()
        return token

    def disable(self) -> None:
        self.cfg.set("auth.enabled", False)
        self.cfg.save()

    def check(self, provided: str | None) -> bool:
        if not self.enabled:
            return True
        expected = self.token
        if not expected:
            return False
        return bool(provided) and hmac.compare_digest(str(provided), expected)

    # ------------------------------------------------------------------ #
    def public_state(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "token_set": bool(self.token),
            "token_preview": (self.token[:4] + "…") if self.token else "",
            "note": t("security.tokenNote"),
        }


def is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in ("localhost", "")

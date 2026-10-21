"""Опрос удалённых агентов мониторинга.

Агент — отдельный скрипт `scripts/megatool-agent.py`, которому не нужны
зависимости MegaTool: его можно скопировать на сервер и запустить обычным
Python. Здесь мы раз в несколько секунд забираем его JSON и складываем
результат в кэш, чтобы дашборд не дёргал чужие машины на каждый запрос.

Токены агентов наружу (в API и интерфейс) не отдаются: только признак
`token_set` и первые символы для узнаваемости.
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .i18n import get_language, t
from .util import iso

#: Сколько секунд считаем свежими результаты опроса.
CACHE_TTL = 5.0
#: Таймаут одного агента: недоступная машина не должна тормозить остальные.
TIMEOUT = 3.0
#: Разумный предел числа агентов в настройках.
MAX_AGENTS = 20

_NUMERIC_FIELDS = (
    "uptime", "cores", "cpu_pct", "load1", "load5", "load15",
    "mem_total", "mem_used", "mem_pct", "swap_total", "swap_used", "swap_pct",
    "disk_total", "disk_used", "disk_pct", "net_rx", "net_tx", "temp_c",
)
_TEXT_FIELDS = ("host", "os", "os_pretty", "agent_version")

_CACHE: dict[str, Any] = {"ts": 0.0, "key": "", "data": None}
_LOCK = threading.Lock()


def normalize_url(value: Any) -> str:
    """Приводит адрес агента к виду http(s)://host:port/metrics."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "http://" + raw
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    if parts.username or parts.password:
        # Учётные данные в адресе попали бы в API и журналы открытым текстом.
        return ""
    path = parts.path.rstrip("/")
    if not path:
        path = "/metrics"
    elif not path.endswith("/metrics"):
        path += "/metrics"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def pick_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    """Оставляет только известные поля, приводя числа к нормальному виду."""
    metrics: dict[str, Any] = {}
    for key in _TEXT_FIELDS:
        if key in payload and isinstance(payload[key], str):
            metrics[key] = payload[key][:200]
    for key in _NUMERIC_FIELDS:
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if number != number or number in (float("inf"), float("-inf")):  # NaN/∞
            continue
        metrics[key] = round(number, 2) if key in ("load1", "load5", "load15") else int(number) if key in (
            "cores", "mem_total", "mem_used", "swap_total", "swap_used", "disk_total", "disk_used",
            "net_rx", "net_tx",
        ) else number
    metrics["ts"] = payload.get("ts") or time.time()
    return metrics


def fetch(url: str, token: str = "", timeout: float = TIMEOUT) -> dict[str, Any]:
    """Забирает /metrics у одного агента. Бросает исключение при ошибке."""
    target = normalize_url(url)
    if not target:
        raise ValueError("invalid agent url")
    request = Request(target, headers={
        "Accept": "application/json",
        "User-Agent": "MegaTool-AgentClient/1.0",
        **_auth_headers(token),
    })
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - адрес задаёт пользователь
        raw = response.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("response too large")
    data = json.loads(raw.decode("utf-8", errors="replace"))
    if not isinstance(data, dict):
        raise ValueError("invalid json")
    if data.get("ok") is False:
        raise ValueError(str(data.get("error") or "agent error")[:200])
    return data


def _auth_headers(token: str) -> dict[str, str]:
    if not token:
        return {}
    return {
        "Authorization": f"Bearer {token}",
        "X-MegaTool-Agent-Token": token,
    }


def _probe(agent: dict[str, Any]) -> dict[str, Any]:
    entry = {
        "id": str(agent.get("id") or ""),
        "name": str(agent.get("name") or agent.get("id") or "agent"),
        "url": normalize_url(agent.get("url")),
        "online": False,
        "error": "",
        "metrics": {},
        "checked_at": iso(),
    }
    try:
        payload = fetch(entry["url"], str(agent.get("token") or ""))
        entry["online"] = True
        entry["metrics"] = pick_metrics(payload)
        entry["host"] = entry["metrics"].get("host") or ""
    except Exception as exc:  # сеть, таймаут, неверный токен — показываем причину
        entry["error"] = t("agents.unreachable", error=str(exc)[:180])
    return entry


def collect(cfg, force: bool = False) -> dict[str, Any]:
    """Опрашивает всех настроенных агентов параллельно (с кэшем на CACHE_TTL)."""
    agents = [item for item in (cfg.get("agents", []) or []) if isinstance(item, dict)]
    agents = agents[:MAX_AGENTS]
    key = json.dumps(
        {"agents": [{k: a.get(k) for k in ("id", "url", "token")} for a in agents], "lang": get_language()},
        sort_keys=True,
        default=str,
    )
    now = time.time()
    if not force:
        with _LOCK:
            if _CACHE["data"] is not None and _CACHE["key"] == key and now - float(_CACHE["ts"]) < CACHE_TTL:
                return _CACHE["data"]

    if agents:
        workers = max(1, min(8, len(agents)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="mega-agent") as pool:
            results = list(pool.map(_probe, agents))
    else:
        results = []

    online = sum(1 for item in results if item["online"])
    data = {
        "agents": results,
        "summary": {"total": len(results), "online": online, "offline": len(results) - online},
        "checked_at": iso(),
    }
    with _LOCK:
        _CACHE.update({"ts": now, "key": key, "data": data})
    return data


def reset_cache() -> None:
    """Сбрасывает кэш опроса (например, при смене языка или настроек)."""
    with _LOCK:
        _CACHE.update({"ts": 0.0, "key": "", "data": None})

"""Настройки и домашний каталог MegaTool.

Всё состояние приложения живёт в MEGATOOL_HOME (по умолчанию ~/.megatool):

    ~/.megatool/
        config.json      — настройки
        apps/            — развёрнутые приложения (compose-проекты, манифесты)
        configs/         — библиотека конфигов (nginx, ansible, k8s, prometheus...)
        runs/            — журналы запусков задач (Ansible и др.)
        logs/audit.jsonl — журнал действий пользователя
"""

from __future__ import annotations

import copy
import functools
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Tuple, Union

from .util import ensure_dir, read_json, write_json

ENV_HOME = "MEGATOOL_HOME"
LEGACY_ENV_HOME = "MEGAPET_HOME"


def _resolve_home() -> Path:
    """Определяет рабочий каталог с мягкой миграцией со старого имени.

    Приоритет: MEGATOOL_HOME → MEGAPET_HOME (для совместимости) → ~/.megatool.
    Если каталога ~/.megatool ещё нет, а ~/.megapet остался от прежних версий —
    переносим его (данные пользователя не теряются).
    """
    for env_name in (ENV_HOME, LEGACY_ENV_HOME):
        value = os.environ.get(env_name)
        if value:
            return Path(value).expanduser()
    new_home = Path.home() / ".megatool"
    old_home = Path.home() / ".megapet"
    if not new_home.exists() and old_home.is_dir():
        try:
            os.rename(old_home, new_home)
        except OSError:
            return old_home
    return new_home


HOME_DIR = _resolve_home()
CONFIG_PATH = HOME_DIR / "config.json"
APPS_DIR = HOME_DIR / "apps"
CONFIGS_DIR = HOME_DIR / "configs"
RUNS_DIR = HOME_DIR / "runs"
LOGS_DIR = HOME_DIR / "logs"
AUDIT_LOG = LOGS_DIR / "audit.jsonl"
BUNDLES_DIR = HOME_DIR / "bundles"

DEFAULT_THRESHOLDS = {
    "cpu_pct": 90.0,
    "mem_pct": 90.0,
    "swap_pct": 60.0,
    "disk_pct": 90.0,
    "load_per_core": 2.0,
    "sustain_sec": 20,
    "temp_c": 85.0,
}

UI_THEMES = ("light", "sepia", "slate", "dark", "contrast", "auto")
UI_DENSITIES = ("normal", "compact")
UI_LANGUAGES = ("ru", "en")

DEFAULT_CONFIG: Dict[str, Any] = {
    "version": 1,
    "server": {
        "host": "127.0.0.1",
        "port": 8787,
        "open_browser": True,
        "allowed_hosts": [],     # доп. имена Host; локальные имена и IP добавляются сами
        "allow_insecure_remote": False,  # только явное решение администратора
        "tls": {
            "enabled": False,
            "certfile": "",      # пусто = MEGATOOL_HOME/tls/server.crt
            "keyfile": "",       # пусто = MEGATOOL_HOME/tls/server.key
        },
    },
    "audit": {
        "max_mb": 5,             # порог ротации журнала действий
        "keep": 3,               # сколько архивов хранить
    },
    "monitor": {
        "interval_ms": 2000,
        "history_points": 1800,
        "thresholds": dict(DEFAULT_THRESHOLDS),
        "watch_paths": [],       # пусто = значения по умолчанию для ОС
        "extra_roots": [],       # дополнительные корни для редактора конфигов
        "container_poll_sec": 30,
    },
    "engines": {
        "docker": "",
        "podman": "",
        "kubectl": "",
        "ansible": "",
        "ansible_playbook": "",
        "ansible_inventory": "",
        "nginx": "",
        "ssh": "",               # путь к клиенту ssh (пусто = из PATH)
        "scp": "",               # путь к клиенту scp (пусто = из PATH)
        "preferred_container_engine": "auto",  # auto | docker | podman
    },
    "safety": {
        "allow_process_kill": True,
        "allow_container_actions": True,
        "allow_k8s_delete": True,
        "allow_file_delete": True,
        "allow_remote_exec": True,   # операции на удалённых серверах по SSH
    },
    "jobs": {
        "max_active": 8,             # одновременных долгих задач (1–64)
        "keep": 60,                  # записей задач в памяти/на диске
    },
    "agents": [],                    # удалённые агенты мониторинга (см. scripts/megatool-agent.py)
    "checks": [],                    # проверки доступности: HTTP, TCP и TLS
    "hosts": [],                     # инвентарь серверов: TCP/HTTP, uptime и простой
    "ui": {
        "theme": "light",
        "density": "normal",
        "language": "ru",
        "notifications": False,      # системные уведомления браузера о critical-алертах и падении серверов
    },
    "balancers": [],
    "catalog": {
        "deployed": {},
    },
    "servers": [],               # SSH-серверы для автоматической настройки
}


def default_watch_paths() -> List[str]:
    """Каталоги, которые имеет смысл отслеживать на изменения (по умолчанию)."""
    candidates = ["/etc", "/etc/nginx", "/etc/systemd/system", "/opt", str(HOME_DIR)]
    return [p for p in candidates if Path(p).is_dir()]


class Config:
    """Потокобезопасная обёртка над config.json с поддержкой точечных путей.

    cfg.get("monitor.thresholds.cpu_pct") -> 90.0
    cfg.set("server.port", 9000)
    """

    def __init__(self, path: Union[Path, str] = CONFIG_PATH) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = copy.deepcopy(DEFAULT_CONFIG)
        self.load()

    # ------------------------------------------------------------------ #
    def load(self) -> Dict[str, Any]:
        with self._lock:
            stored = read_json(self.path, default=None)
            data = copy.deepcopy(DEFAULT_CONFIG)
            if isinstance(stored, dict):
                data = _deep_merge(data, stored)
            # Старые версии хранили настройки встроенной авторизации: удаляем
            # секцию при загрузке, чтобы она не возвращалась в файл и в API.
            data.pop("auth", None)
            self._data = data
            self._ensure_dirs()
            if not self.path.is_file():
                self.save()
            return self._data

    def save(self) -> None:
        with self._lock:
            # В конфиге могут лежать токены агентов: закрываем файл от чтения
            # другими пользователями.
            write_json(self.path, self._data, mode=0o600)

    @property
    def data(self) -> Dict[str, Any]:
        with self._lock:
            return self._data

    def _ensure_dirs(self) -> None:
        for d in (HOME_DIR, APPS_DIR, CONFIGS_DIR, RUNS_DIR, LOGS_DIR, BUNDLES_DIR):
            ensure_dir(d)
        for d in (HOME_DIR, LOGS_DIR):
            try:
                os.chmod(d, 0o700)
            except OSError:
                pass

    # ------------------------------------------------------------------ #
    def get(self, dotted: str, default: Any = None) -> Any:
        with self._lock:
            node: Any = self._data
            for part in _path_parts(dotted):
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
            return node

    def set(self, dotted: str, value: Any) -> None:
        with self._lock:
            parts = _path_parts(dotted)
            node = self._data
            for part in parts[:-1]:
                nxt = node.get(part)
                if not isinstance(nxt, dict):
                    nxt = {}
                    node[part] = nxt
                node = nxt
            node[parts[-1]] = value

    def update(self, patch: Dict[str, Any], persist: bool = True) -> Dict[str, Any]:
        """Глубоко сливает патч с настройками (используется API PATCH /settings)."""
        with self._lock:
            clean = dict(patch or {})
            clean.pop("auth", None)
            self._data = _deep_merge(self._data, clean)
            if persist:
                self.save()
            return self._data

    def export(self, redact_secrets: bool = True) -> Dict[str, Any]:
        with self._lock:
            data = copy.deepcopy(self._data)
        data.pop("auth", None)
        if redact_secrets:
            for agent in data.get("agents", []) or []:
                if not isinstance(agent, dict):
                    continue
                token = str(agent.get("token") or "")
                agent["token_set"] = bool(token)
                agent["token_preview"] = f"{token[:4]}…" if token else ""
                agent["token"] = ""
        return data


@functools.lru_cache(maxsize=256)
def _path_parts(dotted: str) -> Tuple[str, ...]:
    """Кэширует разбор точечных путей: горячий путь чтения настроек."""
    return tuple(str(dotted).split("."))


def _deep_merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out

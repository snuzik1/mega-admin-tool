"""Локализация серверных сообщений: ошибки, предупреждения, тексты задач.

Язык хранится в настройках (ui.language) и переключается через set_language().
Словари лежат в пакете megatool.lang: модули вида ru_<модуль>.py / en_<модуль>.py
с переменной DATA (вложенные словари). Ключи точечные: "containers.notFound".

Резервный язык — русский: если перевода нет, показывается русский текст,
а если нет и его — возвращается сам ключ (так недостачу сразу видно).
"""

from __future__ import annotations

import functools
import importlib
import pkgutil
import sys
from typing import Any, Dict, List, Optional

_LANG = "ru"
_DICTS: Dict[str, Dict[str, Any]] = {"ru": {}, "en": {}}
_LOADED = False


def _deep_merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _load_dicts() -> None:
    """Собирает словари из всех модулей megatool.lang (ru_* и en_*)."""
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    try:
        from . import lang as lang_pkg
    except ImportError:  # pragma: no cover - пакет словарей всегда есть
        return
    for info in pkgutil.iter_modules(lang_pkg.__path__):
        name = info.name
        if name.startswith("ru_"):
            target = "ru"
        elif name.startswith("en_"):
            target = "en"
        else:
            continue
        try:
            module = importlib.import_module(f"{lang_pkg.__name__}.{name}")
        except ImportError:  # pragma: no cover
            continue
        data = getattr(module, "DATA", None)
        if isinstance(data, dict):
            _DICTS[target] = _deep_merge(_DICTS[target], data)


def languages() -> List[str]:
    return ["ru", "en"]


def get_language() -> str:
    return _LANG


def set_language(lang: Optional[str]) -> str:
    """Устанавливает язык сообщений; неизвестный код превращается в «ru»."""
    global _LANG
    _LANG = lang if lang in _DICTS else "ru"
    # Кэши отчётов хранят уже переведённые подписи (30–45 с). Сбрасываем их,
    # чтобы после переключения языка не смешивались два языка.
    for name in ("changes", "systeminfo", "agents"):
        module = sys.modules.get(f"{__package__}.{name}")
        reset = getattr(module, "reset_cache", None)
        if callable(reset):
            try:
                reset()
            except Exception:  # pragma: no cover - кэш не критичен
                pass
    return _LANG


def _lookup(dictionary: Dict[str, Any], path: str) -> Any:
    node: Any = dictionary
    for part in str(path).split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


@functools.lru_cache(maxsize=4096)
def _template(lang: str, key: str) -> Optional[str]:
    """Готовый шаблон перевода (кэш: t() вызывается сотни раз в секунду)."""
    value = _lookup(_DICTS.get(lang, {}), key)
    if value is None:
        value = _lookup(_DICTS["ru"], key)
    return None if value is None else str(value)


def t(key: str, **params: Any) -> str:
    """Перевод по ключу с подстановкой {параметров}."""
    _load_dicts()
    text = _template(_LANG, key)
    if text is None:
        return key
    for name, item in params.items():
        text = text.replace("{" + name + "}", str(item))
    return text


#: Короткий алиас для строковых литералов в модулях.
L = t


def audit() -> Dict[str, List[str]]:
    """Ключи, которых не хватает хотя бы в одном языке (проверка полноты)."""
    _load_dicts()
    missing: Dict[str, List[str]] = {"ru": [], "en": []}

    def walk(node: Dict[str, Any], prefix: str) -> None:
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                walk(value, path)
            else:
                for lang in ("ru", "en"):
                    if _lookup(_DICTS[lang], path) is None:
                        missing[lang].append(path)

    walk(_DICTS["ru"], "")
    walk(_DICTS["en"], "")
    return missing

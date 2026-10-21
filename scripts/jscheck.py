"""Проверка синтаксиса JS-файлов через esprima (по возможности) — для CI и локально."""
from __future__ import annotations

import sys
from pathlib import Path

try:
    import esprima
except ImportError:  # pragma: no cover
    print("esprima не установлен: pip install esprima")
    sys.exit(2)

root = Path("web/js")
bad = 0


def sanitize_for_esprima(text: str) -> str:
    """esprima 4 не знает ES2020 (?. и ??). Для проверки синтаксиса подменяем их
    на эквиваленты по структуре: настоящая проверка — запуск в браузере."""
    return text.replace("??", "||").replace("?.", ".")


for path in sorted(root.rglob("*.js")):
    text = path.read_text(encoding="utf-8")
    try:
        esprima.parseModule(sanitize_for_esprima(text))
        print(f"ok   {path}")
    except Exception as exc:  # noqa: BLE001
        bad += 1
        print(f"FAIL {path}: {exc}")
print("---")
print(f"files: {len(list(root.rglob('*.js')))}, failed: {bad}")
sys.exit(1 if bad else 0)

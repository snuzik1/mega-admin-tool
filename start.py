#!/usr/bin/env python3
"""MegaTool — запуск одной командой на Windows и Linux.

    python start.py [параметры megatool]

При первом запуске создаёт .venv и ставит пять runtime-зависимостей,
при следующих — сразу запускает приложение (pip не вызывается).
Каталог окружения можно переопределить переменной MEGATOOL_VENV.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = Path(os.environ.get("MEGATOOL_VENV") or (ROOT / ".venv")).expanduser()
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
CHECK = "import fastapi, uvicorn, websockets, psutil, yaml"


def run(cmd: list[str]) -> int:
    """Запускает команду из корня проекта и возвращает код выхода."""
    return subprocess.call(cmd, cwd=str(ROOT))


def deps_ready() -> bool:
    """Быстрая проверка: окружение существует и все зависимости импортируются."""
    if not PY.is_file():
        return False
    return run([str(PY), "-c", CHECK]) == 0


def main() -> int:
    if sys.version_info < (3, 10):
        print("MegaTool требует Python 3.10 или новее.")
        return 1
    if not deps_ready():
        if not PY.is_file():
            print(f"[setup] создаю виртуальное окружение: {VENV}")
            code = run([sys.executable, "-m", "venv", str(VENV)])
            if code != 0:
                print("[setup] не удалось создать venv. Проверьте установку Python 3.10+.")
                return code
        print("[setup] ставлю 5 runtime-зависимостей (обычно ~15 секунд)...")
        code = run([
            str(PY), "-m", "pip", "install", "--disable-pip-version-check",
            "--no-input", "--quiet", "-r", str(ROOT / "requirements.txt"),
        ])
        if code != 0:
            print("[setup] не удалось установить зависимости (проверьте сеть).")
            return code
    return run([str(PY), "-m", "megatool", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())

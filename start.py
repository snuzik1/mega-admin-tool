#!/usr/bin/env python3
"""MegaTool — запуск одной командой на Windows, Linux и macOS.

    python start.py [параметры megatool]

При первом запуске создаёт .venv и ставит пять runtime-зависимостей,
при следующих — сразу запускает приложение (pip не вызывается).
Каталог окружения можно переопределить переменной MEGATOOL_VENV.

Команды --help и --version не требуют окружения и зависимостей.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence

ROOT = Path(__file__).resolve().parent


def _resolve_venv(value: str) -> Path:
    """Раскрывает ~ и переменные окружения; относительный путь — от проекта."""
    path = Path(os.path.expandvars(value)).expanduser()
    return path if path.is_absolute() else ROOT / path


VENV = _resolve_venv(os.environ.get("MEGATOOL_VENV") or ".venv")
MIN_VERSION = (3, 8)
CHECK = "import fastapi, uvicorn, websockets, psutil, yaml"
FAST_ARGS = ("-h", "--help", "--version")


def venv_python(venv: Path, windows: Optional[bool] = None) -> Path:
    """Путь к Python внутри venv: Scripts/python.exe или bin/python."""
    if windows is None:
        windows = os.name == "nt"
    if windows:
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def run(cmd: Sequence[str], cwd: Optional[Path] = None, quiet: bool = False) -> int:
    """Запускает команду без shell и возвращает код выхода."""
    output = subprocess.DEVNULL if quiet else None
    return subprocess.call(
        [str(item) for item in cmd],
        cwd=str(cwd or ROOT),
        stdout=output,
        stderr=output,
    )


def deps_ready(py: Path, cwd: Path = ROOT) -> bool:
    """Быстрая проверка: все runtime-зависимости импортируются."""
    return py.is_file() and run([str(py), "-c", CHECK], cwd, quiet=True) == 0


def venv_version_ok(py: Path, cwd: Path = ROOT) -> bool:
    """Проверяет, что Python внутри venv не старее минимальной версии."""
    check = "import sys; raise SystemExit(0 if sys.version_info >= %r else 1)" % (MIN_VERSION,)
    return py.is_file() and run([str(py), "-c", check], cwd, quiet=True) == 0


def _setup_hint(windows: bool) -> str:
    if windows:
        return "Установите Python 3.8+ с python.org и включите Add python.exe to PATH."
    if sys.platform == "darwin":
        return "Установите Python 3.8+ (например, brew install python) и повторите запуск."
    return "Linux: установите модуль venv (Debian/Ubuntu: sudo apt install python3-venv)."


def main(
    argv: Optional[List[str]] = None,
    *,
    root: Path = ROOT,
    venv: Path = VENV,
    windows: Optional[bool] = None,
) -> int:
    """Готовит окружение при необходимости и запускает `python -m megatool`."""
    args = list(sys.argv[1:] if argv is None else argv)
    win = os.name == "nt" if windows is None else windows
    py = venv_python(venv, win)

    if sys.version_info < MIN_VERSION:
        print("MegaTool требует Python 3.8 или новее.")
        return 1

    # Справка и версия не должны ждать создания venv и установки зависимостей.
    if any(arg in FAST_ARGS for arg in args):
        return run([sys.executable, "-m", "megatool", *args], cwd=root)

    if not py.is_file():
        print(f"[setup] создаю виртуальное окружение: {venv}")
        code = run([sys.executable, "-m", "venv", str(venv)], cwd=root)
        if code != 0:
            print("[setup] не удалось создать venv. Проверьте Python 3.8+ и модуль venv.")
            print("[setup] " + _setup_hint(win))
            return code
    elif not venv_version_ok(py, root):
        print(f"[setup] окружение {venv} создано старой версией Python.")
        print("[setup] удалите этот каталог и запустите start.py снова.")
        return 1

    if not deps_ready(py, root):
        print("[setup] ставлю 5 runtime-зависимостей (обычно ~15 секунд)...")
        code = run([
            str(py), "-m", "pip", "install", "--disable-pip-version-check",
            "--no-input", "--quiet", "-r", str(root / "requirements.txt"),
        ], cwd=root)
        if code != 0:
            print("[setup] не удалось установить зависимости (проверьте сеть и pip).")
            return code
    return run([str(py), "-m", "megatool", *args], cwd=root)


if __name__ == "__main__":
    raise SystemExit(main())

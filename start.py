#!/usr/bin/env python3
"""MegaTool — запуск одной командой на Linux.

    python3 start.py [--install-only] [параметры megatool]

При первом запуске создаёт .venv и ставит пять runtime-зависимостей,
при следующих — сразу запускает приложение (pip не вызывается).
Флаг --install-only готовит окружение и выходит, не запуская сервер.
Каталог окружения можно переопределить переменной MEGATOOL_VENV.

Команды --help и --version не требуют окружения и зависимостей.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent


def _resolve_venv(value: str) -> Path:
    """Раскрывает ~ и переменные окружения; относительный путь — от проекта."""
    path = Path(os.path.expandvars(value)).expanduser()
    return path if path.is_absolute() else ROOT / path


VENV = _resolve_venv(os.environ.get("MEGATOOL_VENV") or ".venv")
MIN_VERSION = (3, 8)
CHECK = "import fastapi, uvicorn, websockets, psutil, yaml"
FAST_ARGS = ("-h", "--help", "--version")
INSTALL_ONLY = "--install-only"


def venv_python(venv: Path) -> Path:
    """Путь к Python внутри виртуального окружения Linux."""
    return venv / "bin" / "python"


def is_windows_venv(venv: Path) -> bool:
    """Похоже ли окружение на venv, созданный в Windows."""
    return (venv / "Scripts" / "python.exe").is_file()


def venv_has_files(venv: Path) -> bool:
    """Есть ли в каталоге окружения хотя бы один элемент."""
    try:
        return any(venv.iterdir())
    except OSError:
        return False


def run(
    cmd: Sequence[str],
    cwd: Optional[Path] = None,
    quiet: bool = False,
    env: Optional[Dict[str, str]] = None,
) -> int:
    """Запускает команду без shell и возвращает код выхода."""
    output = subprocess.DEVNULL if quiet else None
    return subprocess.call(
        [str(item) for item in cmd],
        cwd=str(cwd or ROOT),
        stdout=output,
        stderr=output,
        env=env,
    )


def deps_ready(py: Path, cwd: Path = ROOT) -> bool:
    """Быстрая проверка: все runtime-зависимости импортируются."""
    return py.is_file() and run([str(py), "-c", CHECK], cwd, quiet=True) == 0


def venv_version_ok(py: Path, cwd: Path = ROOT) -> bool:
    """Проверяет, что Python внутри venv не старее минимальной версии."""
    check = "import sys; raise SystemExit(0 if sys.version_info >= %r else 1)" % (MIN_VERSION,)
    return py.is_file() and run([str(py), "-c", check], cwd, quiet=True) == 0


def _setup_hint() -> str:
    return (
        "Linux: установите Python 3.8+ и модуль venv "
        "(Debian/Ubuntu: sudo apt install python3 python3-venv; "
        "Fedora: sudo dnf install python3; Arch: sudo pacman -S python; "
        "Alpine: sudo apk add python3 py3-pip bash). "
        "Или запустите автоматический установщик: sh install.sh."
    )


def _auto_install_system(root: Path) -> bool:
    """Пробует доставить системные пакеты через install.sh."""
    if os.environ.get("MEGATOOL_AUTO_INSTALL", "1") == "0":
        return False
    installer = root / "install.sh"
    if not installer.is_file():
        return False
    print("[setup] пробую доставить системные пакеты автоматически...")
    print("[setup] может потребоваться sudo; отключить: MEGATOOL_AUTO_INSTALL=0")
    return run(["sh", str(installer), "--system-only"], cwd=root) == 0


def main(
    argv: Optional[List[str]] = None,
    *,
    root: Path = ROOT,
    venv: Path = VENV,
) -> int:
    """Готовит окружение при необходимости и запускает `python -m megatool`."""
    args = list(sys.argv[1:] if argv is None else argv)
    install_only = INSTALL_ONLY in args
    if install_only:
        args = [arg for arg in args if arg != INSTALL_ONLY]

    if sys.version_info < MIN_VERSION:
        print("MegaTool требует Python 3.8 или новее.")
        return 1

    # Справка и версия не должны ждать создания venv, установки зависимостей
    # и доступны на любой ОС: именно эти команды помогают понять, что запущено.
    if any(arg in FAST_ARGS for arg in args):
        return run([sys.executable, "-m", "megatool", *args], cwd=root)

    if sys.platform != "linux":
        print("MegaTool поддерживает только Linux.")
        return 1

    py = venv_python(venv)
    if not py.is_file():
        if is_windows_venv(venv):
            print(f"[setup] окружение {venv} создано в Windows.")
            print("[setup] оно несовместимо с Linux и не будет использоваться.")
            print(f"[setup] удалите каталог и запустите снова: rm -rf \"{venv}\"")
            return 1
        if venv_has_files(venv):
            print(f"[setup] каталог {venv} уже существует, но не содержит bin/python.")
            print("[setup] это не Linux-venv или он повреждён.")
            print(f"[setup] удалите каталог и запустите снова: rm -rf \"{venv}\"")
            return 1
        print(f"[setup] создаю виртуальное окружение: {venv}")
        code = run([sys.executable, "-m", "venv", str(venv)], cwd=root)
        if code != 0 and _auto_install_system(root):
            print("[setup] повторяю создание venv после установки системных пакетов...")
            code = run([sys.executable, "-m", "venv", str(venv)], cwd=root)
        if code != 0:
            print("[setup] не удалось создать venv. Проверьте Python 3.8+ и модуль venv.")
            print("[setup] " + _setup_hint())
            return code
    elif not venv_version_ok(py, root):
        print(f"[setup] окружение {venv} создано старой версией Python.")
        print("[setup] удалите этот каталог и запустите start.py снова.")
        return 1

    if not deps_ready(py, root):
        print("[setup] ставлю 5 runtime-зависимостей (обычно ~15 секунд)...")
        pip_cmd = [
            str(py), "-m", "pip", "install", "--disable-pip-version-check",
            "--no-input", "--quiet", "-r", str(root / "requirements.txt"),
        ]
        code = run(pip_cmd, cwd=root)
        if code != 0 and _auto_install_system(root):
            print("[setup] повторяю установку зависимостей...")
            code = run(pip_cmd, cwd=root)
        if code != 0:
            print("[setup] не удалось установить зависимости (проверьте сеть и pip).")
            print("[setup] системные пакеты можно доставить: sh install.sh --system-only")
            return code
    if install_only:
        print(f"[setup] зависимости готовы: {venv}")
        return 0
    # Пароль нужен дочернему процессу для первого создания auth.json, но не
    # должен оставаться в окружении родителя, пока работает сервер.
    launch_env = os.environ.copy()
    for name in ("MEGATOOL_ADMIN_PASSWORD", "MEGATOOL_NEW_PASSWORD"):
        os.environ.pop(name, None)
    return run([str(py), "-m", "megatool", *args], cwd=root, env=launch_env)


if __name__ == "__main__":
    raise SystemExit(main())

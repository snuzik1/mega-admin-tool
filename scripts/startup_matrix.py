#!/usr/bin/env python3
"""Симуляция запуска MegaTool в 10 профилях Windows, Linux и macOS.

Скрипт не запускает приложение и не ставит зависимости: он подменяет
subprocess-вызовы и проверяет, что `start.py` выбирает правильный Python
внутри venv, создаёт окружение только при необходимости и передаёт
аргументы в `python -m megatool`.

Запуск: python scripts/startup_matrix.py
Тесты: pytest tests/test_startup_matrix.py
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import start  # noqa: E402  (путь к проекту добавляется выше)


PROFILES: List[Dict[str, object]] = [
    {
        "name": "Windows 10 / Python 3.8",
        "platform": "Windows 10",
        "windows": True,
        "python": (3, 8, 10),
        "venv": "ready",
        "deps": True,
        "expect": "launch",
    },
    {
        "name": "Windows 11 / Python 3.12",
        "platform": "Windows 11",
        "windows": True,
        "python": (3, 12, 8),
        "venv": "new",
        "deps": False,
        "expect": "launch",
    },
    {
        "name": "Windows Server / Python 3.14",
        "platform": "Windows Server 2022",
        "windows": True,
        "python": (3, 14, 0),
        "venv": "stale",
        "deps": True,
        "expect": "stale",
    },
    {
        "name": "Windows, путь с пробелом / 3.10",
        "platform": "Windows 10 (OneDrive)",
        "windows": True,
        "python": (3, 10, 14),
        "venv": "new",
        "deps": False,
        "expect": "launch",
        "spaces": True,
    },
    {
        "name": "Debian 12 / Python 3.11",
        "platform": "Debian 12",
        "windows": False,
        "python": (3, 11, 9),
        "venv": "ready",
        "deps": True,
        "expect": "launch",
    },
    {
        "name": "Ubuntu 24.04 / Python 3.12",
        "platform": "Ubuntu 24.04",
        "windows": False,
        "python": (3, 12, 8),
        "venv": "new",
        "deps": False,
        "expect": "launch",
    },
    {
        "name": "Alpine 3.20 / Python 3.12",
        "platform": "Alpine 3.20",
        "windows": False,
        "python": (3, 12, 8),
        "venv": "ready",
        "deps": False,
        "expect": "launch",
    },
    {
        "name": "macOS 14 Intel / Python 3.10",
        "platform": "macOS 14 (Intel)",
        "windows": False,
        "python": (3, 10, 14),
        "venv": "ready",
        "deps": True,
        "expect": "launch",
    },
    {
        "name": "macOS 15 Apple Silicon / 3.12",
        "platform": "macOS 15 (arm64)",
        "windows": False,
        "python": (3, 12, 8),
        "venv": "new",
        "deps": False,
        "expect": "launch",
    },
    {
        "name": "macOS 15 Apple Silicon / 3.13",
        "platform": "macOS 15 (arm64)",
        "windows": False,
        "python": (3, 13, 1),
        "venv": "ready",
        "deps": False,
        "expect": "launch",
    },
]


def _fake_run(calls: List[List[str]], py: Path, profile: Dict[str, object]):
    """Эмулирует venv/pip/python и записывает фактические команды."""

    def runner(cmd, cwd=None, quiet=False):  # type: ignore[no-untyped-def]
        argv = [str(item) for item in cmd]
        calls.append(argv)
        if len(argv) >= 3 and argv[1:3] == ["-m", "venv"]:
            py.parent.mkdir(parents=True, exist_ok=True)
            py.write_text("", encoding="utf-8")
            return 0
        if len(argv) >= 3 and argv[1] == "-c":
            if "version_info" in argv[2]:
                return 0 if profile["venv"] != "stale" else 1
            if "import fastapi" in argv[2]:
                return 0 if profile["deps"] else 1
        if any(part == "pip" for part in argv):
            return 0
        if len(argv) >= 3 and argv[1:3] == ["-m", "megatool"]:
            return 0
        return 0

    return runner


def _expected_python_parts(windows: bool) -> Tuple[str, str]:
    if windows:
        return ("Scripts", "python.exe")
    return ("bin", "python")


def simulate(profile: Dict[str, object]) -> Dict[str, object]:
    """Прогоняет один профиль через настоящий start.main() с mock subprocess."""
    with tempfile.TemporaryDirectory(prefix="megatool-startup-") as tmp:
        folder = "Mega Tool" if profile.get("spaces") else "MegaTool"
        root = Path(tmp) / folder
        root.mkdir()
        (root / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
        venv = root / ".venv"
        windows = bool(profile["windows"])
        py = start.venv_python(venv, windows)

        if profile["venv"] != "new":
            py.parent.mkdir(parents=True)
            py.write_text("", encoding="utf-8")

        calls: List[List[str]] = []
        runner = _fake_run(calls, py, profile)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            with mock.patch.object(start, "run", side_effect=runner):
                with mock.patch.object(start.sys, "version_info", profile["python"]):
                    code = start.main(["--no-browser"], root=root, venv=venv, windows=windows)

        path_ok = py.parts[-2:] == _expected_python_parts(windows)
        launched = bool(calls) and calls[-1][1:3] == ["-m", "megatool"]
        created = any(cmd[1:3] == ["-m", "venv"] for cmd in calls)
        installed = any("pip" in cmd for cmd in calls)
        if profile["expect"] == "stale":
            ok = code == 1 and path_ok and not launched
            summary = "OK: старое venv отклонено"
        else:
            ok = code == 0 and path_ok and launched
            setup = "venv создан" if created else "venv готов"
            pip_state = "pip вызван" if installed else "pip пропущен"
            summary = f"OK: {setup}, {pip_state}"

        return {
            "name": profile["name"],
            "platform": profile["platform"],
            "ok": ok,
            "code": code,
            "path": "/".join(py.parts[-2:]),
            "launched": launched,
            "created": created,
            "installed": installed,
            "summary": summary,
            "output": output.getvalue().strip(),
        }


def main() -> int:
    print("Симуляция запуска MegaTool: 10 профилей Windows / Linux / macOS")
    print("-" * 82)
    failed = 0
    for number, profile in enumerate(PROFILES, 1):
        result = simulate(profile)
        status = "OK  " if result["ok"] else "FAIL"
        if not result["ok"]:
            failed += 1
        print(
            f"[{number:02d}/10] {status} {result['name']:<36} "
            f"{result['path']:<18} {result['summary']}"
        )
        if not result["ok"] and result["output"]:
            for line in str(result["output"]).splitlines():
                print(f"          {line}")
    print("-" * 82)
    if failed:
        print(f"Провалено профилей: {failed}")
        return 1
    print("Все 10 профилей прошли.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

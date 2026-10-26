#!/usr/bin/env python3
"""Симуляция запуска MegaTool в 10 профилях Linux.

Скрипт не запускает приложение и не ставит зависимости: он подменяет
subprocess-вызовы и проверяет, что `start.py` выбирает правильный Python
внутри venv, создаёт окружение только при необходимости и передаёт
аргументы в `python -m megatool`.

Запуск: python3 scripts/startup_matrix.py
Тесты: pytest tests/test_startup_matrix.py
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
from pathlib import Path
from typing import Dict, List
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import start  # noqa: E402  (путь к проекту добавляется выше)


PROFILES: List[Dict[str, object]] = [
    {
        "name": "Debian 12 / Python 3.8",
        "platform": "Debian 12",
        "python": (3, 8, 10),
        "venv": "ready",
        "deps": True,
        "expect": "launch",
    },
    {
        "name": "Ubuntu 22.04 / Python 3.10",
        "platform": "Ubuntu 22.04",
        "python": (3, 10, 14),
        "venv": "ready",
        "deps": False,
        "expect": "launch",
    },
    {
        "name": "Ubuntu 24.04 / Python 3.12",
        "platform": "Ubuntu 24.04",
        "python": (3, 12, 8),
        "venv": "new",
        "deps": False,
        "expect": "launch",
    },
    {
        "name": "Ubuntu 24.04 / Python 3.14",
        "platform": "Ubuntu 24.04",
        "python": (3, 14, 0),
        "venv": "new",
        "deps": False,
        "expect": "launch",
    },
    {
        "name": "Fedora 40 / Python 3.12",
        "platform": "Fedora 40",
        "python": (3, 12, 8),
        "venv": "ready",
        "deps": False,
        "expect": "launch",
    },
    {
        "name": "Rocky Linux 9 / Python 3.9",
        "platform": "Rocky Linux 9",
        "python": (3, 9, 18),
        "venv": "ready",
        "deps": True,
        "expect": "launch",
    },
    {
        "name": "Arch Linux / Python 3.13",
        "platform": "Arch Linux",
        "python": (3, 13, 1),
        "venv": "new",
        "deps": False,
        "expect": "launch",
    },
    {
        "name": "Alpine 3.20 / Python 3.12",
        "platform": "Alpine 3.20",
        "python": (3, 12, 8),
        "venv": "ready",
        "deps": False,
        "expect": "launch",
    },
    {
        "name": "Debian, путь с пробелом / 3.11",
        "platform": "Debian 12 (путь с пробелом)",
        "python": (3, 11, 9),
        "venv": "new",
        "deps": False,
        "expect": "launch",
        "spaces": True,
    },
    {
        "name": "Debian 12 / старое venv / 3.12",
        "platform": "Debian 12",
        "python": (3, 12, 8),
        "venv": "stale",
        "deps": True,
        "expect": "stale",
    },
]


def _fake_run(calls: List[List[str]], py: Path, profile: Dict[str, object]):
    """Эмулирует venv/pip/python и записывает фактические команды."""

    def runner(cmd, cwd=None, quiet=False, env=None):  # type: ignore[no-untyped-def]
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


def simulate(profile: Dict[str, object]) -> Dict[str, object]:
    """Прогоняет один профиль через настоящий start.main() с mock subprocess."""
    with tempfile.TemporaryDirectory(prefix="megatool-startup-") as tmp:
        folder = "Mega Tool" if profile.get("spaces") else "MegaTool"
        root = Path(tmp) / folder
        root.mkdir()
        (root / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
        venv = root / ".venv"
        py = start.venv_python(venv)

        if profile["venv"] != "new":
            py.parent.mkdir(parents=True)
            py.write_text("", encoding="utf-8")

        calls: List[List[str]] = []
        runner = _fake_run(calls, py, profile)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            with mock.patch.object(start, "run", side_effect=runner):
                with mock.patch.object(start.sys, "version_info", profile["python"]):
                    code = start.main(["--no-browser"], root=root, venv=venv)

        path_ok = py.parts[-2:] == ("bin", "python")
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
    total = len(PROFILES)
    print(f"Симуляция запуска MegaTool: {total} профилей Linux")
    print("-" * 82)
    failed = 0
    for number, profile in enumerate(PROFILES, 1):
        result = simulate(profile)
        status = "OK  " if result["ok"] else "FAIL"
        if not result["ok"]:
            failed += 1
        print(
            f"[{number:02d}/{total:02d}] {status} {result['name']:<36} "
            f"{result['path']:<18} {result['summary']}"
        )
        if not result["ok"] and result["output"]:
            for line in str(result["output"]).splitlines():
                print(f"          {line}")
    print("-" * 82)
    if failed:
        print(f"Провалено профилей: {failed}")
        return 1
    print(f"Все {total} профилей прошли.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

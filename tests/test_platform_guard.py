"""Проверки Linux-only защиты и миграции старого venv."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import List

import pytest

import start
from megatool import __main__ as cli

ROOT = Path(__file__).resolve().parents[1]


def _no_run(calls: List[List[str]]):
    def fake_run(cmd, cwd=None, quiet=False):  # type: ignore[no-untyped-def]
        calls.append([str(item) for item in cmd])
        return 0

    return fake_run


def test_cli_rejects_non_linux(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli.sys, "platform", "win32")

    code = cli.main([])

    assert code == 1
    assert "Linux" in capsys.readouterr().out


def test_cli_version_works_on_non_linux(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli.sys, "platform", "darwin")

    code = cli.main(["--version"])

    assert code == 0
    assert "MegaTool" in capsys.readouterr().out


def test_start_rejects_non_linux(monkeypatch, tmp_path: Path, capsys) -> None:
    calls: List[List[str]] = []
    monkeypatch.setattr(start, "run", _no_run(calls))
    monkeypatch.setattr(start.sys, "platform", "win32")

    code = start.main([], root=tmp_path, venv=tmp_path / ".venv")

    assert code == 1
    assert calls == []
    assert "Linux" in capsys.readouterr().out


def test_start_rejects_windows_venv(monkeypatch, tmp_path: Path, capsys) -> None:
    calls: List[List[str]] = []
    monkeypatch.setattr(start, "run", _no_run(calls))
    monkeypatch.setattr(start.sys, "platform", "linux")
    monkeypatch.setattr(start.sys, "version_info", (3, 12, 0))

    venv = tmp_path / ".venv"
    (venv / "Scripts").mkdir(parents=True)
    (venv / "Scripts" / "python.exe").write_text("", encoding="utf-8")

    code = start.main([], root=tmp_path, venv=venv)

    assert code == 1
    assert calls == []
    output = capsys.readouterr().out
    assert "Windows" in output
    assert str(venv) in output


def test_start_rejects_foreign_venv_directory(monkeypatch, tmp_path: Path, capsys) -> None:
    calls: List[List[str]] = []
    monkeypatch.setattr(start, "run", _no_run(calls))
    monkeypatch.setattr(start.sys, "platform", "linux")
    monkeypatch.setattr(start.sys, "version_info", (3, 12, 0))

    venv = tmp_path / ".venv"
    (venv / "junk").mkdir(parents=True)

    code = start.main([], root=tmp_path, venv=venv)

    assert code == 1
    assert calls == []
    output = capsys.readouterr().out
    assert "bin/python" in output
    assert str(venv) in output


@pytest.mark.skipif(sys.platform != "linux" or shutil.which("bash") is None,
                    reason="нужен Linux и bash")
def test_bootstrap_rejects_windows_venv(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "bootstrap.sh", project / "scripts" / "bootstrap.sh")
    (project / "requirements.txt").write_text("", encoding="utf-8")

    venv = project / ".venv" / "Scripts"
    venv.mkdir(parents=True)
    (venv / "python.exe").write_text("", encoding="utf-8")

    result = subprocess.run(
        ["bash", str(project / "scripts" / "bootstrap.sh")],
        cwd=str(project),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "Windows" in result.stderr
    assert str(project / ".venv") in result.stderr

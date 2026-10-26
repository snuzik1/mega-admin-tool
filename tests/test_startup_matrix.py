"""Проверки симуляции запуска в Linux-профилях."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List

import pytest

import start
from scripts.startup_matrix import PROFILES, simulate

pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="MegaTool поддерживает только Linux",
)


@pytest.mark.parametrize("profile", PROFILES, ids=[p["name"] for p in PROFILES])
def test_startup_profile(profile):
    result = simulate(profile)

    assert result["ok"], f"{profile['name']}: {result}"
    assert result["code"] in (0, 1)
    assert result["path"] == "bin/python"
    if profile["expect"] == "stale":
        assert not result["launched"]
        assert not result["created"]
        assert not result["installed"]
    else:
        assert result["launched"]


def test_launch_env_removes_passwords(tmp_path, monkeypatch):
    root = tmp_path / "MegaTool"
    root.mkdir()
    (root / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    venv = root / ".venv"
    py = start.venv_python(venv)
    py.parent.mkdir(parents=True)
    py.write_text("", encoding="utf-8")

    seen = {}

    def fake_run(cmd, cwd=None, quiet=False, env=None):
        if env is not None:
            seen["env"] = env
        return 0

    monkeypatch.setattr(start, "run", fake_run)
    monkeypatch.setenv("MEGATOOL_ADMIN_PASSWORD", "admin-secret")
    monkeypatch.setenv("MEGATOOL_NEW_PASSWORD", "new-secret")

    assert start.main(["--no-browser"], root=root, venv=venv) == 0
    assert "MEGATOOL_ADMIN_PASSWORD" not in os.environ
    assert "MEGATOOL_NEW_PASSWORD" not in os.environ
    assert seen["env"]["MEGATOOL_ADMIN_PASSWORD"] == "admin-secret"
    assert seen["env"]["MEGATOOL_NEW_PASSWORD"] == "new-secret"


def test_venv_python_layout() -> None:
    assert start.venv_python(Path("venv")).parts[-2:] == ("bin", "python")


def test_relative_venv_is_relative_to_project() -> None:
    assert start._resolve_venv("custom-venv") == start.ROOT / "custom-venv"


@pytest.mark.parametrize("flag", ["--version", "--help", "-h"])
def test_help_and_version_skip_setup(monkeypatch, tmp_path: Path, flag: str) -> None:
    calls: List[List[str]] = []

    def fake_run(cmd, cwd=None, quiet=False, env=None):  # type: ignore[no-untyped-def]
        calls.append([str(item) for item in cmd])
        return 0

    monkeypatch.setattr(start, "run", fake_run)
    monkeypatch.setattr(start.sys, "version_info", (3, 12, 0))

    code = start.main([flag], root=tmp_path, venv=tmp_path / ".venv")

    assert code == 0
    assert calls == [[start.sys.executable, "-m", "megatool", flag]]
    assert not (tmp_path / ".venv").exists()


def test_old_python_is_rejected(monkeypatch, tmp_path: Path, capsys) -> None:
    calls: List[List[str]] = []

    def fake_run(cmd, cwd=None, quiet=False, env=None):  # type: ignore[no-untyped-def]
        calls.append([str(item) for item in cmd])
        return 0

    monkeypatch.setattr(start, "run", fake_run)
    monkeypatch.setattr(start.sys, "version_info", (3, 7, 17))

    code = start.main([], root=tmp_path, venv=tmp_path / ".venv")

    assert code == 1
    assert calls == []
    assert "Python 3.8" in capsys.readouterr().out


def test_venv_failure_has_linux_hint(monkeypatch, tmp_path: Path, capsys) -> None:
    def fake_run(cmd, cwd=None, quiet=False, env=None):  # type: ignore[no-untyped-def]
        return 1

    monkeypatch.setattr(start, "run", fake_run)
    monkeypatch.setattr(start.sys, "version_info", (3, 12, 0))

    code = start.main([], root=tmp_path, venv=tmp_path / ".venv")

    assert code == 1
    assert "python3-venv" in capsys.readouterr().out

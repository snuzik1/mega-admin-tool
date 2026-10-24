"""Проверки Linux-only защиты и миграции старого venv."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List

import pytest

import start
from megatool import __main__ as cli
from scripts.venv_path import resolve_venv

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


def test_start_version_works_on_non_linux(monkeypatch, tmp_path: Path) -> None:
    calls: List[List[str]] = []
    monkeypatch.setattr(start, "run", _no_run(calls))
    monkeypatch.setattr(start.sys, "platform", "win32")
    monkeypatch.setattr(start.sys, "version_info", (3, 12, 0))

    code = start.main(["--version"], root=tmp_path, venv=tmp_path / ".venv")

    assert code == 0
    assert calls == [[start.sys.executable, "-m", "megatool", "--version"]]
    assert not (tmp_path / ".venv").exists()


def test_venv_path_relative_to_project(tmp_path: Path) -> None:
    assert resolve_venv("custom-venv", tmp_path) == tmp_path / "custom-venv"


def test_venv_path_absolute_is_kept(tmp_path: Path) -> None:
    target = tmp_path / "absolute-venv"
    assert resolve_venv(str(target), tmp_path) == target


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
    shutil.copy(ROOT / "scripts" / "venv_path.py", project / "scripts" / "venv_path.py")
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


@pytest.mark.skipif(sys.platform != "linux" or shutil.which("bash") is None,
                    reason="нужен Linux и bash")
def test_bootstrap_respects_megatool_venv(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "bootstrap.sh", project / "scripts" / "bootstrap.sh")
    shutil.copy(ROOT / "scripts" / "venv_path.py", project / "scripts" / "venv_path.py")
    (project / "requirements.txt").write_text("", encoding="utf-8")

    venv = project / "custom-venv" / "Scripts"
    venv.mkdir(parents=True)
    (venv / "python.exe").write_text("", encoding="utf-8")

    env = os.environ.copy()
    env["MEGATOOL_VENV"] = "custom-venv"
    result = subprocess.run(
        ["bash", str(project / "scripts" / "bootstrap.sh")],
        cwd=str(project),
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 1
    assert "Windows" in result.stderr
    assert str(project / "custom-venv") in result.stderr


@pytest.mark.skipif(sys.platform != "linux" or shutil.which("bash") is None,
                    reason="нужен Linux и bash")
def test_install_autostart_respects_megatool_venv(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "install-autostart.sh",
                project / "scripts" / "install-autostart.sh")
    shutil.copy(ROOT / "scripts" / "venv_path.py", project / "scripts" / "venv_path.py")

    custom = project / "custom-venv"
    python = custom / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    python.chmod(0o755)

    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    systemctl = fakebin / "systemctl"
    systemctl.write_text(
        '#!/bin/sh\necho "$@" >> "$SYSTEMCTL_LOG"\n',
        encoding="utf-8",
    )
    systemctl.chmod(0o755)
    log = tmp_path / "systemctl.log"
    config_home = tmp_path / "config"
    home = tmp_path / "home"

    env = os.environ.copy()
    env["MEGATOOL_VENV"] = "custom-venv"
    env["XDG_CONFIG_HOME"] = str(config_home)
    env["HOME"] = str(home)
    env["SYSTEMCTL_LOG"] = str(log)
    env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")

    result = subprocess.run(
        ["bash", str(project / "scripts" / "install-autostart.sh")],
        cwd=str(project),
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    unit = config_home / "systemd" / "user" / "megatool.service"
    text = unit.read_text(encoding="utf-8")
    assert f'ExecStart="{python}" -m megatool --no-browser' in text
    assert "daemon-reload" in log.read_text(encoding="utf-8")
    assert "enable --now megatool.service" in log.read_text(encoding="utf-8")


def test_start_install_only_prepares_deps_without_launch(monkeypatch, tmp_path: Path, capsys) -> None:
    calls: List[List[str]] = []
    monkeypatch.setattr(start, "run", _no_run(calls))
    monkeypatch.setattr(start.sys, "platform", "linux")
    monkeypatch.setattr(start.sys, "version_info", (3, 12, 0))

    venv = tmp_path / ".venv"
    py = start.venv_python(venv)
    py.parent.mkdir(parents=True)
    py.write_text("", encoding="utf-8")

    code = start.main(["--install-only"], root=tmp_path, venv=venv)

    assert code == 0
    assert calls
    assert not any(cmd[1:3] == ["-m", "megatool"] for cmd in calls)
    assert "зависимости готовы" in capsys.readouterr().out


def test_start_auto_installs_system_deps_on_venv_failure(monkeypatch, tmp_path: Path) -> None:
    calls: List[List[str]] = []
    attempts = 0
    installer = tmp_path / "install.sh"
    installer.write_text("#!/bin/sh\n", encoding="utf-8")
    venv = tmp_path / ".venv"
    py = start.venv_python(venv)

    def fake_run(cmd, cwd=None, quiet=False):  # type: ignore[no-untyped-def]
        nonlocal attempts
        argv = [str(item) for item in cmd]
        calls.append(argv)
        if len(argv) >= 3 and argv[1:3] == ["-m", "venv"]:
            attempts += 1
            if attempts == 1:
                return 1
            py.parent.mkdir(parents=True)
            py.write_text("", encoding="utf-8")
        return 0

    monkeypatch.setattr(start, "run", fake_run)
    monkeypatch.setattr(start.sys, "platform", "linux")
    monkeypatch.setattr(start.sys, "version_info", (3, 12, 0))
    monkeypatch.delenv("MEGATOOL_AUTO_INSTALL", raising=False)

    code = start.main([], root=tmp_path, venv=venv)

    assert code == 0
    assert attempts == 2
    assert ["sh", str(installer), "--system-only"] in calls
    assert calls[-1][1:3] == ["-m", "megatool"]


def test_start_retries_pip_after_system_install(monkeypatch, tmp_path: Path) -> None:
    calls: List[List[str]] = []
    pip_calls = 0
    installer = tmp_path / "install.sh"
    installer.write_text("#!/bin/sh\n", encoding="utf-8")
    venv = tmp_path / ".venv"
    py = start.venv_python(venv)
    py.parent.mkdir(parents=True)
    py.write_text("", encoding="utf-8")

    def fake_run(cmd, cwd=None, quiet=False):  # type: ignore[no-untyped-def]
        nonlocal pip_calls
        argv = [str(item) for item in cmd]
        calls.append(argv)
        if len(argv) >= 3 and argv[1] == "-c" and "import fastapi" in argv[2]:
            return 1
        if any(part == "pip" for part in argv):
            pip_calls += 1
            return 1 if pip_calls == 1 else 0
        return 0

    monkeypatch.setattr(start, "run", fake_run)
    monkeypatch.setattr(start.sys, "platform", "linux")
    monkeypatch.setattr(start.sys, "version_info", (3, 12, 0))
    monkeypatch.delenv("MEGATOOL_AUTO_INSTALL", raising=False)

    code = start.main([], root=tmp_path, venv=venv)

    assert code == 0
    assert pip_calls == 2
    assert ["sh", str(installer), "--system-only"] in calls
    assert calls[-1][1:3] == ["-m", "megatool"]


def test_start_auto_install_can_be_disabled(monkeypatch, tmp_path: Path) -> None:
    calls: List[List[str]] = []
    installer = tmp_path / "install.sh"
    installer.write_text("#!/bin/sh\n", encoding="utf-8")

    def fake_run(cmd, cwd=None, quiet=False):  # type: ignore[no-untyped-def]
        calls.append([str(item) for item in cmd])
        return 1

    monkeypatch.setattr(start, "run", fake_run)
    monkeypatch.setattr(start.sys, "platform", "linux")
    monkeypatch.setattr(start.sys, "version_info", (3, 12, 0))
    monkeypatch.setenv("MEGATOOL_AUTO_INSTALL", "0")

    code = start.main([], root=tmp_path, venv=tmp_path / ".venv")

    assert code == 1
    assert not any(cmd[:2] == ["sh", str(installer)] for cmd in calls)


@pytest.mark.skipif(sys.platform != "linux" or shutil.which("sh") is None,
                    reason="нужен Linux и sh")
def test_install_script_help_and_dry_run() -> None:
    script = ROOT / "install.sh"

    help_result = subprocess.run(
        ["sh", str(script), "--help"],
        capture_output=True,
        text=True,
    )
    assert help_result.returncode == 0
    assert "--system-only" in help_result.stdout

    dry_run = subprocess.run(
        ["sh", str(script), "--dry-run", "--system-only"],
        capture_output=True,
        text=True,
    )
    assert dry_run.returncode == 0, dry_run.stderr
    assert "пакетный менеджер" in dry_run.stdout
    assert "системные пакеты" in dry_run.stdout

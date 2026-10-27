"""Резервное копирование и восстановление данных MegaTool."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKUP = ROOT / "scripts" / "backup.py"
RESTORE = ROOT / "scripts" / "restore.py"


def _run(script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args],
        text=True,
        capture_output=True,
        check=False,
    )


def test_backup_restore_roundtrip(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "apps").mkdir(parents=True)
    (home / "configs").mkdir()
    (home / "config.json").write_text('{"ui": {"language": "ru"}}\n', encoding="utf-8")
    (home / "auth.json").write_text('{"version": 1}\n', encoding="utf-8")
    (home / "hosts_state.json").write_text("{}\n", encoding="utf-8")
    (home / "apps" / "demo.txt").write_text("demo\n", encoding="utf-8")
    (home / "configs" / "nginx.conf").write_text("server {}\n", encoding="utf-8")
    (home / "tls" / "keys").mkdir(parents=True)
    (home / "tls" / "server.crt").write_text("dummy cert\n", encoding="utf-8")
    (home / "tls" / "server.key").write_text("dummy key\n", encoding="utf-8")
    (home / "tls" / "keys" / "custom.key").write_text("dummy nested key\n", encoding="utf-8")
    os.chmod(home / "tls" / "server.key", 0o600)
    os.chmod(home / "tls" / "keys" / "custom.key", 0o600)
    (home / "logs").mkdir()
    (home / "logs" / "audit.jsonl").write_text('{"event": "x"}\n', encoding="utf-8")
    os.chmod(home / "auth.json", 0o600)
    os.chmod(home / "config.json", 0o600)
    os.chmod(home, 0o700)

    archive = tmp_path / "backup.tgz"
    result = _run(BACKUP, "--home", str(home), "--output", str(archive))
    assert result.returncode == 0, result.stderr
    assert archive.is_file()
    if os.name == "posix":
        assert archive.stat().st_mode & 0o777 == 0o600

    # Существующий архив не перезаписывается (в том числе через гонку).
    before = archive.read_bytes()
    result = _run(BACKUP, "--home", str(home), "--output", str(archive))
    assert result.returncode == 1
    assert archive.read_bytes() == before

    restored = tmp_path / "restored"
    result = _run(RESTORE, str(archive), "--home", str(restored))
    assert result.returncode == 0, result.stderr
    assert (restored / "config.json").read_text(encoding="utf-8") == '{"ui": {"language": "ru"}}\n'
    assert (restored / "apps" / "demo.txt").read_text(encoding="utf-8") == "demo\n"
    assert (restored / "tls" / "server.key").read_text(encoding="utf-8") == "dummy key\n"
    assert (restored / "tls" / "keys" / "custom.key").read_text(encoding="utf-8") == "dummy nested key\n"
    assert not (restored / "logs").exists()
    if os.name == "posix":
        assert (restored / "auth.json").stat().st_mode & 0o777 == 0o600
        assert (restored / "config.json").stat().st_mode & 0o777 == 0o600
        assert (restored / "tls" / "server.key").stat().st_mode & 0o777 == 0o600
        assert (restored / "tls" / "server.crt").stat().st_mode & 0o777 == 0o600
        assert (restored / "tls" / "keys" / "custom.key").stat().st_mode & 0o777 == 0o600
        assert (restored / "tls" / "keys").stat().st_mode & 0o777 == 0o700
        assert (restored / "tls").stat().st_mode & 0o777 == 0o700
        assert restored.stat().st_mode & 0o700 == 0o700

    # Без --force второй раз не перезаписываем.
    result = _run(RESTORE, str(archive), "--home", str(restored))
    assert result.returncode == 1
    assert "--force" in result.stderr


def _archive_with_member(
    path: Path,
    name: str,
    member_type: bytes = tarfile.REGTYPE,
    data: bytes = b"owned",
    linkname: str = "",
) -> None:
    with tarfile.open(str(path), "w:gz") as handle:
        info = tarfile.TarInfo(name)
        info.type = member_type
        info.linkname = linkname
        if member_type == tarfile.REGTYPE:
            info.size = len(data)
            handle.addfile(info, io.BytesIO(data))
        else:
            handle.addfile(info)


def test_backup_refuses_output_inside_home_without_creating_parent(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    if os.name == "posix":
        os.chmod(home, 0o700)
    output = home / "backups" / "backup.tgz"
    result = _run(BACKUP, "--home", str(home), "--output", str(output))
    assert result.returncode == 1
    assert "внутри MEGATOOL_HOME" in result.stderr
    assert not (home / "backups").exists()


def test_restore_rejects_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "evil.tgz"
    _archive_with_member(archive, "../evil.txt")
    target = tmp_path / "home"
    result = _run(RESTORE, str(archive), "--home", str(target), "--force")
    assert result.returncode == 1
    assert "недопустимый путь" in result.stderr
    assert not (tmp_path / "evil.txt").exists()


def test_restore_rejects_absolute_paths_and_special_files(tmp_path: Path) -> None:
    target = tmp_path / "home"
    cases = [
        ("absolute", "/etc/evil", tarfile.REGTYPE, "недопустимый путь"),
        ("symlink", "apps/link", tarfile.SYMTYPE, "недопустимый тип"),
        ("device", "apps/device", tarfile.CHRTYPE, "недопустимый тип"),
    ]
    for label, name, member_type, message in cases:
        archive = tmp_path / f"{label}.tgz"
        _archive_with_member(archive, name, member_type, linkname="/etc/passwd")
        result = _run(RESTORE, str(archive), "--home", str(target), "--force")
        assert result.returncode == 1, label
        assert message in result.stderr, label


def test_restore_rejects_open_home_permissions(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("POSIX permissions only")
    home = tmp_path / "home"
    home.mkdir()
    os.chmod(home, 0o755)
    archive = tmp_path / "backup.tgz"
    _archive_with_member(archive, "config.json", data=b"{}\n")
    result = _run(RESTORE, str(archive), "--home", str(home), "--force")
    assert result.returncode == 1
    assert "chmod 700" in result.stderr


def test_restore_rejects_existing_symlink_escape(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("symlinks are POSIX-only in this test")
    home = tmp_path / "home"
    (home / "apps").mkdir(parents=True)
    os.chmod(home, 0o700)
    outside = tmp_path / "outside.txt"
    outside.write_text("do not touch\n", encoding="utf-8")
    link = home / "apps" / "demo.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are not available")
    archive = tmp_path / "backup.tgz"
    _archive_with_member(archive, "apps/demo.txt", data=b"replacement\n")

    result = _run(RESTORE, str(archive), "--home", str(home), "--force")
    assert result.returncode == 1
    assert "символической ссылкой" in result.stderr
    assert outside.read_text(encoding="utf-8") == "do not touch\n"

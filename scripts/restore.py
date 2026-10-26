#!/usr/bin/env python3
"""Восстановление данных MegaTool из архива backup.py.

Скрипт проверяет имена и типы файлов, не извлекает симлинки и устройства,
не принимает абсолютные пути и ``..``. Перед восстановлением остановите
MegaTool. Существующие данные перезаписываются только с ``--force``.

Примеры:
    python3 scripts/restore.py megatool-backup-20261025-120000.tar.gz --force
    python3 scripts/restore.py backup.tgz --home /srv/megatool --force
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import List, Optional, Set

ALLOWED_TOP = {
    "config.json",
    "hosts_state.json",
    "auth.json",
    "apps",
    "bundles",
    "configs",
    "logs",
    "runs",
}
PRIVATE_FILES = {"auth.json", "config.json"}
PRIVATE_DIRS = {"logs"}


def default_home() -> Path:
    value = os.environ.get("MEGATOOL_HOME", "").strip()
    if value:
        return Path(value).expanduser()
    return Path.home() / ".megatool"


def _safe_members(archive: tarfile.TarFile) -> List[tarfile.TarInfo]:
    members = archive.getmembers()
    for member in members:
        name = member.name.replace("\\", "/")
        path = PurePosixPath(name)
        if not name or path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
            raise ValueError(f"недопустимый путь в архиве: {member.name!r}")
        if path.parts[0] not in ALLOWED_TOP:
            raise ValueError(f"недопустимый объект в архиве: {member.name!r}")
        if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
            raise ValueError(f"недопустимый тип объекта: {member.name!r}")
    return members


def _targets(members: List[tarfile.TarInfo], home: Path) -> Set[Path]:
    return {home / PurePosixPath(member.name) for member in members}


def _skip_member(name: str, include_runs: bool) -> bool:
    normalized = name.replace("\\", "/")
    return not include_runs and (normalized == "runs" or normalized.startswith("runs/"))


def _check_target(target: Path, home: Path, home_resolved: Path) -> None:
    """Не позволяем существующим симлинкам увести запись за пределы home."""
    if target.is_symlink():
        raise ValueError(f"цель является символической ссылкой: {target}")
    expected = home_resolved / target.relative_to(home)
    if target.resolve() != expected:
        raise ValueError(f"путь выходит за пределы MEGATOOL_HOME: {target}")


def restore(archive_path: Path, home: Path, force: bool, include_runs: bool) -> int:
    archive_path = archive_path.expanduser()
    if not archive_path.is_file():
        print(f"Архив не найден: {archive_path}", file=sys.stderr)
        return 1
    if home.resolve() == Path(home.anchor).resolve():
        print("Нельзя восстанавливать в корень файловой системы.", file=sys.stderr)
        return 1
    if home.exists() and not home.is_dir():
        print(f"MEGATOOL_HOME не каталог: {home}", file=sys.stderr)
        return 1
    home_existed = home.exists()
    if home_existed and os.name == "posix":
        mode = stat.S_IMODE(home.stat().st_mode)
        if mode & 0o077:
            print(f"Каталог {home} доступен другим пользователям (права {oct(mode)}).", file=sys.stderr)
            print(f"Выполните: chmod 700 {home}", file=sys.stderr)
            return 1

    try:
        with tarfile.open(str(archive_path), "r:*") as archive:
            members = _safe_members(archive)
            targets = _targets(members, home)
            if not force:
                existing = sorted(str(path) for path in targets if path.exists())
                if existing:
                    print("Данные уже существуют; используйте --force для перезаписи:", file=sys.stderr)
                    for item in existing[:10]:
                        print(f"  {item}", file=sys.stderr)
                    return 1
            home.mkdir(parents=True, exist_ok=True)
            if not home_existed and os.name == "posix":
                os.chmod(str(home), 0o700)
            home_resolved = home.resolve()
            temp_root = Path(tempfile.mkdtemp(prefix=".megatool-restore-", dir=str(home.parent)))
            try:
                for member in members:
                    if _skip_member(member.name, include_runs):
                        continue
                    target = temp_root / PurePosixPath(member.name.replace("\\", "/"))
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        source = archive.extractfile(member)
                        if source is None:
                            continue
                        with source, open(str(target), "wb") as handle:
                            shutil.copyfileobj(source, handle, length=1024 * 1024)
                # Копируем только разрешённые объекты, сохраняя относительные пути.
                for member in members:
                    name = member.name.replace("\\", "/")
                    if _skip_member(name, include_runs):
                        continue
                    source = temp_root / PurePosixPath(name)
                    target = home / PurePosixPath(name)
                    _check_target(target, home, home_resolved)
                    if source.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        if os.name == "posix":
                            os.chmod(str(target), 0o700 if target.name in PRIVATE_DIRS or target == home else 0o755)
                    elif source.is_file():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        if target.exists() and not force:
                            raise FileExistsError(str(target))
                        shutil.copy2(str(source), str(target))
                        if os.name == "posix":
                            mode = 0o600 if target.name in PRIVATE_FILES else None
                            if target.name == "auth.json":
                                mode = 0o600
                            if mode is not None:
                                os.chmod(str(target), mode)
            finally:
                shutil.rmtree(str(temp_root), ignore_errors=True)
    except (OSError, tarfile.TarError, ValueError) as exc:
        print(f"Восстановление не выполнено: {exc}", file=sys.stderr)
        return 1

    print(f"Восстановлено в {home}")
    print("Если восстанавливался auth.json, прежние веб-сессии недействительны.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Восстановление данных MegaTool")
    parser.add_argument("archive", help="архив, созданный scripts/backup.py")
    parser.add_argument("--home", default="", help="каталог данных (MEGATOOL_HOME)")
    parser.add_argument("--force", action="store_true", help="перезаписать существующие данные")
    parser.add_argument("--include-runs", action="store_true", help="восстанавливать runs/")
    args = parser.parse_args(argv)
    home = Path(args.home).expanduser() if args.home else default_home()
    return restore(Path(args.archive), home, args.force, args.include_runs)


if __name__ == "__main__":
    raise SystemExit(main())

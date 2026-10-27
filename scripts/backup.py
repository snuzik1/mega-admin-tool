#!/usr/bin/env python3
"""Резервная копия данных MegaTool.

Архив содержит настройки, состояние серверов, приложения, наборы конфигов и
(по умолчанию) файл аутентификации. Пароль в auth.json не хранится в открытом
виде, но хеш чувствителен: архив создаётся с правами 0600.

Примеры:
    python3 scripts/backup.py
    python3 scripts/backup.py --output /var/backups/megatool.tgz
    MEGATOOL_HOME=/srv/megatool python3 scripts/backup.py --include-logs
"""

from __future__ import annotations

import argparse
import os
import stat
import sys
import tarfile
import time
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

TOP_LEVEL = (
    "config.json",
    "hosts_state.json",
    "auth.json",
    "apps",
    "bundles",
    "configs",
    "tls",
    "logs",
    "runs",
)


def default_home() -> Path:
    value = os.environ.get("MEGATOOL_HOME", "").strip()
    if value:
        return Path(value).expanduser()
    return Path.home() / ".megatool"


def _iter_files(root: Path, base: Path) -> Iterable[Tuple[Path, str]]:
    """Обходит каталог, не следуя по симлинкам и не включая спецфайлы."""
    for current, dirs, files in os.walk(str(root), followlinks=False):
        current_path = Path(current)
        dirs[:] = sorted(name for name in dirs if not (current_path / name).is_symlink())
        yield current_path, current_path.relative_to(base).as_posix()
        for name in sorted(files):
            path = current_path / name
            if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
                continue
            yield path, path.relative_to(base).as_posix()


def build_archive(home: Path, output: Path, include_logs: bool, include_runs: bool) -> int:
    if not home.is_dir():
        print(f"Каталог MegaTool не найден: {home}", file=sys.stderr)
        return 1
    output = output.expanduser()
    if output.exists():
        print(f"Файл уже существует: {output}", file=sys.stderr)
        return 1
    try:
        output.resolve().relative_to(home.resolve())
    except ValueError:
        pass
    else:
        print("Архив нельзя создавать внутри MEGATOOL_HOME: он попадёт сам в себя.", file=sys.stderr)
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)

    selected: List[Path] = []
    for name in TOP_LEVEL:
        if name == "logs" and not include_logs:
            continue
        if name == "runs" and not include_runs:
            continue
        path = home / name
        if path.exists() and not path.is_symlink():
            selected.append(path)

    count = 0
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(str(output), flags, 0o600)
    except FileExistsError:
        print(f"Файл уже существует: {output}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Не удалось создать архив: {exc}", file=sys.stderr)
        return 1

    try:
        with os.fdopen(fd, "wb") as handle:
            with tarfile.open(fileobj=handle, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
                for path in selected:
                    if path.is_dir():
                        for item, arcname in _iter_files(path, home):
                            archive.add(str(item), arcname=arcname, recursive=False)
                            count += 1
                    elif stat.S_ISREG(path.stat().st_mode):
                        archive.add(str(path), arcname=path.name, recursive=False)
                        count += 1
    except (OSError, tarfile.TarError) as exc:
        try:
            output.unlink()
        except OSError:
            pass
        print(f"Не удалось создать архив: {exc}", file=sys.stderr)
        return 1

    if os.name == "posix":
        os.chmod(str(output), 0o600)
    print(f"Готово: {output} ({count} объектов, {output.stat().st_size} байт)")
    print("В архиве могут быть auth.json и приватный ключ TLS — храните архив как секрет (0600).")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Резервная копия данных MegaTool")
    parser.add_argument("--home", default="", help="каталог данных (MEGATOOL_HOME)")
    parser.add_argument("--output", default="", help="путь к tar.gz")
    parser.add_argument("--include-logs", action="store_true", help="включить logs/ (аудит)")
    parser.add_argument("--include-runs", action="store_true", help="включить runs/ (логи задач)")
    args = parser.parse_args(argv)
    home = Path(args.home).expanduser() if args.home else default_home()
    output = Path(args.output).expanduser() if args.output else Path.cwd() / (
        "megatool-backup-" + time.strftime("%Y%m%d-%H%M%S") + ".tar.gz"
    )
    return build_archive(home, output, args.include_logs, args.include_runs)


if __name__ == "__main__":
    raise SystemExit(main())

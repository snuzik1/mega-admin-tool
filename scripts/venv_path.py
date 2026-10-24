#!/usr/bin/env python3
"""Единое разрешение пути MEGATOOL_VENV для shell-скриптов.

Правила совпадают с start.py: ~ и переменные окружения раскрываются,
относительный путь считается от каталога проекта.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Optional


def resolve_venv(value: str, root: Path) -> Path:
    path = Path(os.path.expandvars(value)).expanduser()
    return path if path.is_absolute() else root / path


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("Использование: venv_path.py КАТАЛОГ_ПРОЕКТА MEGATOOL_VENV", file=sys.stderr)
        return 2
    print(resolve_venv(args[1], Path(args[0])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

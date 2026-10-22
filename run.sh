#!/usr/bin/env bash
# Запуск MegaTool (Linux/macOS). Вся подготовка окружения живёт в start.py:
# эта обёртка только находит Python и передаёт ему аргументы.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON=python
else
  echo "Python 3.8+ не найден. Установите python3 и запустите снова." >&2
  exit 1
fi

if [ -n "${MEGATOOL_HOST:-}" ]; then
  set -- --host "$MEGATOOL_HOST" "$@"
fi
if [ -n "${MEGATOOL_PORT:-}" ]; then
  set -- --port "$MEGATOOL_PORT" "$@"
fi
exec "$PYTHON" "$ROOT/start.py" "$@"

#!/usr/bin/env bash
# Запуск MegaTool (Linux/macOS). Первый запуск сам создаст окружение и поставит
# только runtime-зависимости; при следующих запусках pip не вызывается.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$ROOT/.venv/bin/python"

if [ ! -x "$PY" ] || ! "$PY" -c "import fastapi, uvicorn, websockets, psutil, yaml" >/dev/null 2>&1; then
  bash "$ROOT/scripts/bootstrap.sh"
fi

cd "$ROOT"
exec "$PY" -m megatool --host "${MEGATOOL_HOST:-127.0.0.1}" --port "${MEGATOOL_PORT:-8787}" "$@"

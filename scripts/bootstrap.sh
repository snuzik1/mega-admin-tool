#!/usr/bin/env bash
# Создаёт .venv и ставит runtime-зависимости (быстро).
# Для разработки: bash scripts/bootstrap.sh --dev
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/.venv"
REQ="requirements.txt"
if [ "${1:-}" = "--dev" ]; then
  REQ="requirements-dev.txt"
fi

if [ ! -x "$VENV/bin/python" ]; then
  if [ -f "$VENV/Scripts/python.exe" ]; then
    echo "[bootstrap] окружение $VENV создано в Windows." >&2
    echo "[bootstrap] оно несовместимо с Linux. Удалите каталог и запустите снова:" >&2
    echo "            rm -rf \"$VENV\"" >&2
    exit 1
  fi
  if [ -d "$VENV" ] && [ -n "$(ls -A "$VENV" 2>/dev/null || true)" ]; then
    echo "[bootstrap] каталог $VENV уже существует, но не содержит bin/python." >&2
    echo "[bootstrap] это не Linux-venv или он повреждён. Удалите каталог:" >&2
    echo "            rm -rf \"$VENV\"" >&2
    exit 1
  fi
  echo "[bootstrap] создаю виртуальное окружение"
  python3 -m venv "$VENV"
fi

PY="$VENV/bin/python"
echo "[bootstrap] ставлю зависимости ($REQ)..."
"$PY" -m pip install --disable-pip-version-check --no-input --quiet -r "$ROOT/$REQ"
echo "[bootstrap] готово."
echo "Запуск:        bash run.sh"
echo "Для разработки: $VENV/bin/python -m pytest -q"

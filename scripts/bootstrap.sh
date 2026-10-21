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

if [ ! -d "$VENV" ]; then
  echo "[bootstrap] создаю виртуальное окружение"
  python3 -m venv "$VENV"
fi

PY="$VENV/bin/python"
echo "[bootstrap] ставлю зависимости ($REQ)..."
"$PY" -m pip install --disable-pip-version-check --no-input --quiet -r "$ROOT/$REQ"
echo "[bootstrap] готово."
echo "Запуск:        bash run.sh"
echo "Для разработки: $VENV/bin/python -m pytest -q"

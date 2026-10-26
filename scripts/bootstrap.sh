#!/usr/bin/env bash
# Создаёт окружение (по умолчанию .venv) и ставит runtime-зависимости.
# Для разработки: bash scripts/bootstrap.sh --dev
# Каталог можно переопределить через MEGATOOL_VENV (как в start.py).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[bootstrap] Python 3.8+ не найден. Установите python3 и запустите снова." >&2
  exit 1
fi
if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; then
  echo "[bootstrap] нужен Python 3.8 или новее." >&2
  exit 1
fi

VENV="$(python3 "$ROOT/scripts/venv_path.py" "$ROOT" "${MEGATOOL_VENV:-.venv}")"
REQ="requirements.txt"
SECURITY_REQ=""
if [ "${1:-}" = "--dev" ]; then
  REQ="requirements-dev.txt"
  SECURITY_REQ="requirements-security.txt"
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
if [ -n "$SECURITY_REQ" ]; then
  echo "[bootstrap] ставлю сканеры безопасности ($SECURITY_REQ)..."
  "$PY" -m pip install --disable-pip-version-check --no-input --quiet -r "$ROOT/$SECURITY_REQ"
fi
echo "[bootstrap] готово."
echo "Запуск:        bash run.sh"
echo "Для разработки: $VENV/bin/python -m pytest -q"

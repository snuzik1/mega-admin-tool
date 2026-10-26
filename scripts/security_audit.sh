#!/usr/bin/env bash
# Dependency audit с задокументированными исключениями Starlette.
# Новые advisories валят проверку; см. SECURITY.md.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON:-$ROOT/.venv/bin/python}"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="python3"
fi
if ! "$PYTHON_BIN" -c "import pip_audit" >/dev/null 2>&1; then
  echo "pip-audit не установлен: $PYTHON_BIN -m pip install -r requirements-security.txt" >&2
  exit 2
fi

exec "$PYTHON_BIN" -m pip_audit -r requirements.txt \
  --ignore-vuln PYSEC-2026-161 \
  --ignore-vuln PYSEC-2026-248 \
  --ignore-vuln PYSEC-2026-249 \
  --ignore-vuln PYSEC-2026-1942 \
  --ignore-vuln PYSEC-2026-2280 \
  --ignore-vuln PYSEC-2026-2281

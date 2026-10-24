#!/bin/sh
# Запуск MegaTool (Linux). Обёртка на POSIX sh, поэтому работает и на
# минимальном Alpine без bash. Если нет Python 3.8+/venv, она вызывает
# install.sh --system-only; Python-окружение по-прежнему готовит start.py.
set -eu
ROOT="$(cd "$(dirname "$0")" && pwd)"

python_version_ok() {
  candidate="$1"
  command -v "$candidate" >/dev/null 2>&1 || return 1
  "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1
}

has_compatible_version() {
  for candidate in python3 python; do
    if python_version_ok "$candidate"; then
      return 0
    fi
  done
  return 1
}

has_python3() {
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info[0] >= 3 else 1)' >/dev/null 2>&1; then
      return 0
    fi
  done
  return 1
}

python_ready() {
  candidate="$1"
  python_version_ok "$candidate" || return 1
  "$candidate" -c 'import venv, ensurepip' >/dev/null 2>&1
}

find_python() {
  for candidate in python3 python; do
    if python_ready "$candidate"; then
      PYTHON="$candidate"
      return 0
    fi
  done
  return 1
}

PYTHON=""
if ! find_python; then
  if ! has_compatible_version && has_python3; then
    echo "[run] найден Python 3, но его версия старше 3.8." >&2
    echo "[run] обновите Python и запустите снова." >&2
    exit 1
  fi
  if [ "${MEGATOOL_AUTO_INSTALL:-1}" = "0" ]; then
    echo "[run] не найден Python 3.8+ с модулем venv." >&2
    echo "[run] установите пакеты вручную или выполните: sh \"$ROOT/install.sh\"" >&2
    exit 1
  fi
  echo "[run] не найден Python 3.8+ с модулем venv; ставлю системные пакеты..." >&2
  echo "[run] может потребоваться sudo; отключить: MEGATOOL_AUTO_INSTALL=0" >&2
  sh "$ROOT/install.sh" --system-only
  if ! find_python; then
    echo "[run] Python 3.8+ с venv всё ещё недоступен." >&2
    echo "[run] проверьте установку вручную: sh \"$ROOT/install.sh\"" >&2
    exit 1
  fi
fi

if [ -n "${MEGATOOL_HOST:-}" ]; then
  set -- --host "$MEGATOOL_HOST" "$@"
fi
if [ -n "${MEGATOOL_PORT:-}" ]; then
  set -- --port "$MEGATOOL_PORT" "$@"
fi
exec "$PYTHON" "$ROOT/start.py" "$@"

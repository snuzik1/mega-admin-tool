#!/usr/bin/env bash
# Проверяет настоящий первый запуск: start.py создаёт venv, ставит
# зависимости и поднимает сервер. Запускать только на Linux (в CI).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/megatool-smoke.XXXXXX")"
PYTHON="${PYTHON:-python3}"
LOG="$TMP/server.log"
PID=""

export MEGATOOL_HOME="$TMP/home"
export MEGATOOL_VENV="$TMP/venv"
export MEGATOOL_AUTH_FILE="$MEGATOOL_HOME/auth.json"
export MEGATOOL_AUTH_ITERATIONS="${MEGATOOL_AUTH_ITERATIONS:-10000}"
mkdir -p "$MEGATOOL_HOME"
chmod 700 "$MEGATOOL_HOME"

# Учётные данные готовим до запуска сервера: smoke не должен зависеть от TTY.
PYTHONPATH="$ROOT" "$PYTHON" - <<'PY'
from megatool.auth import AuthManager

manager = AuthManager()
manager.set_password("smoke-password-123")
PY

PORT="$("$PYTHON" - <<'PY'
import socket

sock = socket.socket()
sock.bind(("127.0.0.1", 0))
print(sock.getsockname()[1])
sock.close()
PY
)"

cleanup() {
  if [ -n "${PID:-}" ] && kill -0 "$PID" 2>/dev/null; then
    kill -TERM -- "-$PID" 2>/dev/null || kill -TERM "$PID" 2>/dev/null || true
    wait "$PID" 2>/dev/null || true
  fi
  rm -rf "$TMP"
}
trap cleanup EXIT

echo "[smoke] запускаю start.py на порту $PORT"
setsid env MEGATOOL_NO_LOGIN_PROMPT=1 "$PYTHON" "$ROOT/start.py" --no-browser --port "$PORT" >"$LOG" 2>&1 &
PID=$!

READY=0
for _ in $(seq 1 90); do
  if curl -fsS --max-time 2 "http://127.0.0.1:$PORT/api/health" >"$TMP/health.json" 2>/dev/null; then
    READY=1
    break
  fi
  if ! kill -0 "$PID" 2>/dev/null; then
    echo "[smoke] MegaTool завершился до готовности" >&2
    cat "$LOG" >&2 || true
    exit 1
  fi
  sleep 1
done

if [ "$READY" -ne 1 ]; then
  echo "[smoke] MegaTool не ответил за 90 секунд" >&2
  cat "$LOG" >&2 || true
  exit 1
fi

if ! grep -Eq '"ok"[[:space:]]*:[[:space:]]*true' "$TMP/health.json"; then
  echo "[smoke] неожиданный ответ /api/health" >&2
  cat "$TMP/health.json" >&2 || true
  exit 1
fi

# Без сессии API должен быть закрыт.
UNAUTH_CODE="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:$PORT/api/state" || true)"
if [ "$UNAUTH_CODE" != "401" ]; then
  echo "[smoke] /api/state без входа вернул $UNAUTH_CODE вместо 401" >&2
  exit 1
fi

curl -fsS --max-time 5 -c "$TMP/cookies.txt" \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"smoke-password-123"}' \
  "http://127.0.0.1:$PORT/api/auth/login" >"$TMP/login.json"
curl -fsS --max-time 5 -b "$TMP/cookies.txt" "http://127.0.0.1:$PORT/api/state" >"$TMP/state.json"
if ! grep -Eq '"ok"[[:space:]]*:[[:space:]]*true' "$TMP/state.json"; then
  echo "[smoke] /api/state после входа не отвечает" >&2
  cat "$TMP/state.json" >&2 || true
  exit 1
fi

curl -fsS --max-time 5 "http://127.0.0.1:$PORT/" -o "$TMP/index.html"
if ! grep -q "<title>MegaTool" "$TMP/index.html"; then
  echo "[smoke] интерфейс не отдаётся" >&2
  exit 1
fi

echo "[smoke] OK: venv создан, сервер отвечает, вход работает, интерфейс отдаётся"

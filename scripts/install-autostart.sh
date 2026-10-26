#!/usr/bin/env bash
# Автозапуск MegaTool в Linux через пользовательскую службу systemd.
#
#   bash scripts/install-autostart.sh            # при входе пользователя
#   bash scripts/install-autostart.sh --linger   # и без входа (после перезагрузки)
#
# Каталог окружения можно задать через MEGATOOL_VENV (как в start.py).
# Служба работает от текущего пользователя, не требует root для установки.
# Удаление: bash scripts/uninstall-autostart.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_NAME="megatool.service"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
VENV=""
PYTHON=""
LINGER=0

for arg in "$@"; do
  case "$arg" in
    --linger) LINGER=1 ;;
    -h|--help)
      echo "Использование: bash scripts/install-autostart.sh [--linger]"
      exit 0
      ;;
    *)
      echo "Неизвестный аргумент: $arg" >&2
      exit 1
      ;;
  esac
done

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemd не найден. Запускайте MegaTool вручную: bash run.sh" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3.8+ не найден. Установите python3 и запустите снова." >&2
  exit 1
fi

VENV="$(python3 "$ROOT/scripts/venv_path.py" "$ROOT" "${MEGATOOL_VENV:-.venv}")"
PYTHON="$VENV/bin/python"

if [ ! -x "$PYTHON" ]; then
  echo "Виртуальное окружение не найдено: $PYTHON" >&2
  echo "Сначала запустите bash run.sh — он создаст окружение и поставит зависимости." >&2
  exit 1
fi

MEGATOOL_HOME_EFFECTIVE="${MEGATOOL_HOME:-$HOME/.megatool}"
AUTH_FILE="${MEGATOOL_AUTH_FILE:-$MEGATOOL_HOME_EFFECTIVE/auth.json}"
if [ ! -f "$AUTH_FILE" ]; then
  echo "Файл учётных данных не найден: $AUTH_FILE" >&2
  echo "Сначала запустите bash run.sh в терминале и задайте пароль администратора." >&2
  exit 1
fi

mkdir -p "$UNIT_DIR"
cat > "$UNIT_DIR/$UNIT_NAME" <<EOF
[Unit]
Description=MegaTool — локальная консоль администратора и DevOps
Documentation=file://$ROOT/README.md
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory="$ROOT"
ExecStart="$PYTHON" -m megatool --no-browser --no-login-prompt
Environment=PYTHONIOENCODING=utf-8
Environment=PYTHONUNBUFFERED=1
Environment="MEGATOOL_HOME=$MEGATOOL_HOME_EFFECTIVE"
Environment="MEGATOOL_AUTH_FILE=$AUTH_FILE"
Restart=on-failure
RestartSec=3
# Мягкое завершение: uvicorn успевает закрыть задачи и балансировщики.
KillSignal=SIGINT
TimeoutStopSec=15

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now "$UNIT_NAME"

if [ "$LINGER" = "1" ]; then
  if command -v loginctl >/dev/null 2>&1; then
    loginctl enable-linger "$USER" || echo "Не удалось включить linger — служба будет стартовать после входа." >&2
  fi
fi

PORT="$(sed -n 's/.*"port"[[:space:]]*:[[:space:]]*\([0-9]*\).*/\1/p' "$MEGATOOL_HOME_EFFECTIVE/config.json" 2>/dev/null | head -n1 || true)"
PORT="${PORT:-8787}"

echo ""
echo "Автозапуск включён: $UNIT_NAME"
echo "Интерфейс: http://127.0.0.1:$PORT/"
echo "Статус:    systemctl --user status $UNIT_NAME"
echo "Журнал:    journalctl --user -u $UNIT_NAME -f"
echo "Удалить:   bash scripts/uninstall-autostart.sh"

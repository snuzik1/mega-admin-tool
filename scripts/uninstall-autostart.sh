#!/usr/bin/env bash
# Удаление автозапуска MegaTool (пользовательская служба systemd).

set -euo pipefail

UNIT_NAME="megatool.service"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemd не найден — удалять нечего." >&2
  exit 0
fi

systemctl --user disable --now "$UNIT_NAME" 2>/dev/null || true
rm -f "$UNIT_DIR/$UNIT_NAME"
systemctl --user daemon-reload 2>/dev/null || true
systemctl --user reset-failed "$UNIT_NAME" 2>/dev/null || true

echo "Автозапуск MegaTool удалён. Запущенные вручную экземпляры не затронуты."

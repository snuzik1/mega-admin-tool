#!/bin/sh
# MegaTool — скачать и запустить одной командой.
#
#   curl -fsSL https://raw.githubusercontent.com/snuzik1/mega-admin-tool/main/quickstart.sh | sh
#   wget -qO- https://raw.githubusercontent.com/snuzik1/mega-admin-tool/main/quickstart.sh | sh
#
# Скрипт скачивает архив репозитория без git в ~/.local/share/megatool
# (или в MEGATOOL_INSTALL_DIR) и запускает run.sh. Если рядом уже есть
# start.py/run.sh, используется локальная копия.
#
# Переменные:
#   MEGATOOL_INSTALL_DIR  куда скачать код (по умолчанию ~/.local/share/megatool)
#   MEGATOOL_REF          ветка, тег или коммит (по умолчанию main)
#   MEGATOOL_UPDATE=1     перекачать код даже если он уже установлен
#   MEGATOOL_ARCHIVE_URL  другой URL tar.gz (например, зеркало)
#   MEGATOOL_AUTO_INSTALL=0  отключить автоматическую установку системных пакетов
set -eu

REPO="snuzik1/mega-admin-tool"
REF="${MEGATOOL_REF:-main}"
ARCHIVE_URL="${MEGATOOL_ARCHIVE_URL:-https://codeload.github.com/$REPO/tar.gz/$REF}"

if [ -z "${XDG_DATA_HOME:-}" ] && [ -z "${HOME:-}" ]; then
  echo "[quickstart] не заданы HOME и XDG_DATA_HOME." >&2
  exit 1
fi
TARGET="${MEGATOOL_INSTALL_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/megatool}"
case "$TARGET" in
  "~"|"~/"*)
    if [ -z "${HOME:-}" ]; then
      echo "[quickstart] для пути с ~ не задан HOME." >&2
      exit 1
    fi
    ;;
esac
case "$TARGET" in
  "~") TARGET="$HOME" ;;
  "~/"*) TARGET="$HOME/${TARGET#\~/}" ;;
esac
while [ "$TARGET" != "/" ] && [ "${TARGET%/}" != "$TARGET" ]; do
  TARGET="${TARGET%/}"
done
case "$TARGET" in
  ""|"/"|"."|"..")
    echo "[quickstart] недопустимый MEGATOOL_INSTALL_DIR: $TARGET" >&2
    exit 1
    ;;
esac
if [ -n "${HOME:-}" ] && [ "$TARGET" = "$HOME" ]; then
  echo "[quickstart] не устанавливаю MegaTool прямо в \$HOME." >&2
  echo "[quickstart] задайте подкаталог через MEGATOOL_INSTALL_DIR." >&2
  exit 1
fi

# Если скрипт запущен из уже распакованного/склонированного репозитория,
# не скачиваем его повторно. Локальная копия важнее установленной в TARGET.
case "$0" in
  */*) SCRIPT_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd || true)" ;;
  *) SCRIPT_DIR="" ;;
esac
if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/start.py" ] && [ -f "$SCRIPT_DIR/run.sh" ]; then
  exec sh "$SCRIPT_DIR/run.sh" "$@"
fi
if [ -f "./start.py" ] && [ -f "./run.sh" ]; then
  exec sh "./run.sh" "$@"
fi

if [ -f "$TARGET/start.py" ] && [ -f "$TARGET/run.sh" ] &&
   [ "${MEGATOOL_UPDATE:-0}" != "1" ]; then
  echo "[quickstart] использую уже установленный код: $TARGET"
  exec sh "$TARGET/run.sh" "$@"
fi

if [ -e "$TARGET" ] && [ ! -d "$TARGET" ]; then
  echo "[quickstart] $TARGET существует, но это не каталог." >&2
  echo "[quickstart] задайте другой MEGATOOL_INSTALL_DIR." >&2
  exit 1
fi
if [ -d "$TARGET" ] && [ "${MEGATOOL_UPDATE:-0}" != "1" ]; then
  echo "[quickstart] $TARGET уже существует, но не похож на MegaTool." >&2
  echo "[quickstart] удалите его или задайте другой MEGATOOL_INSTALL_DIR." >&2
  exit 1
fi
if [ -d "$TARGET" ] && [ "${MEGATOOL_UPDATE:-0}" = "1" ] &&
   [ ! -f "$TARGET/.megatool-managed" ]; then
  echo "[quickstart] не обновляю $TARGET: каталог создан не quickstart.sh." >&2
  echo "[quickstart] задайте другой MEGATOOL_INSTALL_DIR." >&2
  exit 1
fi

if ! command -v tar >/dev/null 2>&1; then
  echo "[quickstart] нужен tar." >&2
  exit 1
fi
if ! command -v mktemp >/dev/null 2>&1; then
  echo "[quickstart] нужен mktemp." >&2
  exit 1
fi

TMP="$(mktemp -d "${TMPDIR:-/tmp}/megatool-quickstart.XXXXXX")"
cleanup() {
  rm -rf "$TMP"
}
trap cleanup 0
trap 'exit 1' HUP INT TERM

ARCHIVE="$TMP/megatool.tar.gz"
EXTRACT="$TMP/extract"
mkdir -p "$EXTRACT"

echo "[quickstart] скачиваю $REPO@$REF..."
rm -f "$ARCHIVE"
if command -v curl >/dev/null 2>&1; then
  curl -fsSL "$ARCHIVE_URL" -o "$ARCHIVE" || rm -f "$ARCHIVE"
fi
if [ ! -s "$ARCHIVE" ] && command -v wget >/dev/null 2>&1; then
  wget -qO "$ARCHIVE" "$ARCHIVE_URL" || rm -f "$ARCHIVE"
fi
if [ ! -s "$ARCHIVE" ]; then
  echo "[quickstart] не удалось скачать архив." >&2
  echo "[quickstart] проверьте сеть, URL и наличие curl или wget." >&2
  exit 1
fi

tar -xzf "$ARCHIVE" -C "$EXTRACT"
SRC=""
for candidate in "$EXTRACT"/*; do
  if [ -d "$candidate" ] && [ -f "$candidate/start.py" ] && [ -f "$candidate/run.sh" ]; then
    SRC="$candidate"
    break
  fi
done
if [ -z "$SRC" ]; then
  echo "[quickstart] в архиве нет start.py/run.sh." >&2
  exit 1
fi

if [ -d "$TARGET" ] && [ "${MEGATOOL_UPDATE:-0}" = "1" ]; then
  rm -rf "$TARGET"
fi
mkdir -p "$(dirname "$TARGET")"
mv "$SRC" "$TARGET"
printf '%s\n' "$REF" > "$TARGET/.megatool-managed"
trap - 0 HUP INT TERM
rm -rf "$TMP"

echo "[quickstart] код установлен в $TARGET"
exec sh "$TARGET/run.sh" "$@"

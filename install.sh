#!/bin/sh
# MegaTool — установка системных предпосылок и Python-зависимостей.
#
#   sh install.sh                 системные пакеты + .venv + runtime-зависимости
#   sh install.sh --system-only   только системные пакеты
#   sh install.sh --dry-run       показать пакеты, ничего не устанавливая
#
# Скрипт определяет пакетный менеджер (apt-get, dnf/yum, pacman, apk, zypper)
# и использует sudo, если запущен не от root. Docker, kubectl, Ansible, nginx
# и SSH-клиент сюда не входят: приложение работает без них.
set -eu

ROOT="$(cd "$(dirname "$0")" && pwd)"
SYSTEM_ONLY=0
DRY_RUN=0

usage() {
  cat <<'EOF'
Использование: sh install.sh [--system-only] [--dry-run]

  --system-only  установить только системные пакеты Python/venv/pip
  --dry-run      показать выбранный пакетный менеджер и пакеты, но не менять систему
  -h, --help     показать эту справку

Без --system-only после системных пакетов создаётся .venv и ставятся
runtime-зависимости через: python3 start.py --install-only.

Поддерживаются apt-get (Debian/Ubuntu), dnf/yum (Fedora/RHEL), pacman (Arch),
apk (Alpine) и zypper (openSUSE). Для установки может потребоваться sudo.
Автоматический вызов из run.sh отключается через MEGATOOL_AUTO_INSTALL=0.
EOF
}

for arg in "$@"; do
  case "$arg" in
    --system-only) SYSTEM_ONLY=1 ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[install] неизвестный аргумент: $arg" >&2
      usage >&2
      exit 2
      ;;
  esac
done

MANAGER=""
PACKAGES=""

detect_manager() {
  if command -v apt-get >/dev/null 2>&1; then
    MANAGER="apt-get"
    PACKAGES="python3 python3-venv python3-pip bash"
  elif command -v dnf >/dev/null 2>&1; then
    MANAGER="dnf"
    PACKAGES="python3 python3-pip bash"
  elif command -v yum >/dev/null 2>&1; then
    MANAGER="yum"
    PACKAGES="python3 python3-pip bash"
  elif command -v pacman >/dev/null 2>&1; then
    MANAGER="pacman"
    PACKAGES="python python-pip bash"
  elif command -v apk >/dev/null 2>&1; then
    MANAGER="apk"
    PACKAGES="python3 py3-pip bash build-base linux-headers python3-dev"
  elif command -v zypper >/dev/null 2>&1; then
    MANAGER="zypper"
    PACKAGES="python3 python3-pip bash"
  else
    echo "[install] не удалось определить пакетный менеджер." >&2
    echo "[install] установите Python 3.8+, venv и pip вручную." >&2
    exit 1
  fi
}

detect_manager

if [ "$DRY_RUN" -eq 1 ]; then
  echo "[install] пакетный менеджер: $MANAGER"
  echo "[install] системные пакеты: $PACKAGES"
  if [ "$SYSTEM_ONLY" -eq 0 ]; then
    echo "[install] Python-окружение: python3 \"$ROOT/start.py\" --install-only"
  fi
  exit 0
fi

if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
elif command -v sudo >/dev/null 2>&1; then
  SUDO="sudo"
else
  echo "[install] нужны права root или sudo." >&2
  echo "[install] запустите от root: sh \"$ROOT/install.sh\"" >&2
  exit 1
fi

run_root() {
  if [ -n "${SUDO:-}" ]; then
    "$SUDO" "$@"
  else
    "$@"
  fi
}

install_system() {
  echo "[install] системные пакеты ($MANAGER): $PACKAGES"
  case "$MANAGER" in
    apt-get)
      run_root apt-get update
      run_root env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends $PACKAGES
      ;;
    dnf)
      run_root dnf install -y $PACKAGES
      ;;
    yum)
      run_root yum install -y $PACKAGES
      ;;
    pacman)
      run_root pacman -Sy --needed --noconfirm $PACKAGES
      ;;
    apk)
      run_root apk add --no-cache $PACKAGES
      ;;
    zypper)
      run_root zypper --non-interactive install $PACKAGES
      ;;
  esac
}

install_system

PYTHON=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
    PYTHON="$candidate"
    break
  fi
done

if [ -z "$PYTHON" ]; then
  echo "[install] нужен Python 3.8 или новее." >&2
  if command -v python3 >/dev/null 2>&1; then
    python3 --version >&2 || true
  fi
  exit 1
fi
if ! "$PYTHON" -c 'import venv, ensurepip' >/dev/null 2>&1; then
  echo "[install] у $PYTHON нет модуля venv/ensurepip." >&2
  echo "[install] Debian/Ubuntu: sudo apt install python3-venv" >&2
  exit 1
fi

if [ "$SYSTEM_ONLY" -eq 1 ]; then
  echo "[install] системные пакеты готовы."
  exit 0
fi

if [ ! -f "$ROOT/start.py" ]; then
  echo "[install] не найден $ROOT/start.py." >&2
  echo "[install] запускайте install.sh из каталога проекта." >&2
  exit 1
fi

echo "[install] создаю .venv и ставлю Python-зависимости..."
MEGATOOL_AUTO_INSTALL=0 "$PYTHON" "$ROOT/start.py" --install-only
echo "[install] готово. Запуск: sh \"$ROOT/run.sh\""

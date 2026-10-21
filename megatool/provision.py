"""Серверы по SSH: инвентаризация, каталог задач и генерация скриптов.

Модуль сам ничего не запускает «в фоне»: он собирает argv для ssh/scp,
готовит переносимые sh-скрипты и Ansible-плейбуки, а выполнение отдаёт
JobManager (живой журнал) или util.run (синхронная проверка).

Безопасность:
- все значения параметров проверяются регулярками (они попадают в скрипт);
- пароли не хранятся: аутентификация — ключ из настроек или ssh-agent;
- удалённые операции отключаются настройкой safety.allow_remote_exec.
"""

from __future__ import annotations

import base64
import re
import threading
import time
import uuid
from typing import Any, Iterable

from .config import Config
from .i18n import t
from .jobs import JobManager
from .util import CmdResult, iso, run, which

#: Допустимые адреса серверов: имя, IPv4, IPv6 в скобках.
HOST_RE = re.compile(r"^[A-Za-z0-9.\-:\[\]]{1,253}$")
#: Пользователь ssh-подключения (латиница в обоих регистрах, цифры, . _ -).
SERVER_USER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9._-]{0,31}$")
#: Пользователь, которого создаёт задача (строгие правила POSIX).
TASK_USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
#: Часовой пояс: Europe/Moscow, UTC, Etc/GMT+3 и т.п.
TZ_RE = re.compile(r"^[A-Za-z0-9_+\-/]{1,64}$")
#: SSH-ключ: префикс типа + тело ключа + необязательный комментарий.
SSHKEY_RE = re.compile(
    r"^(ssh-rsa|ssh-ed25519|ecdsa-sha2-[a-z0-9-]+|sk-[a-z0-9@.\-]+)"
    r"[ \t]+[A-Za-z0-9+/=]{1,2048}"
    r"([ \t]+[A-Za-z0-9@._:\-]{0,200})?$"
)
#: Дополнительные пакеты (kind=text): только безопасные символы имён пакетов.
PACKAGES_RE = re.compile(r"^[A-Za-z0-9.+=_@:\-]*([ \t]+[A-Za-z0-9.+=_@:\-]+)*$")

#: Порядок групп задач в каталоге и в сгенерированном скрипте.
GROUPS = ("base", "system", "user", "security", "docker", "web")

#: Сколько символов команды/ключа имеет смысл принимать.
MAX_COMMAND = 4000

#: Маркер отчёта проверки — по нему parse_report понимает, что связь есть.
REPORT_MARKER = "MEGATOOL_REPORT=1"


# --------------------------------------------------------------------------- #
#  SSH
# --------------------------------------------------------------------------- #

def ssh_available(cfg: Config) -> str | None:
    """Путь к клиенту ssh: настройка engines.ssh или поиск в PATH."""
    override = str(cfg.get("engines.ssh", "") or "")
    return which("ssh", override or None)


def scp_available(cfg: Config) -> str | None:
    """Путь к клиенту scp (используется для копирования файлов)."""
    override = str(cfg.get("engines.scp", "") or "")
    return which("scp", override or None)


def build_ssh_args(cfg: Config, server: dict[str, Any], extra: Iterable[str] | None = None) -> list[str]:
    """Собирает argv ssh: неинтерактивно, без пароля, с ключом при наличии."""
    binary = ssh_available(cfg) or "ssh"
    port = int(server.get("port") or 22)
    user = str(server.get("user") or "root")
    host = str(server.get("host") or "")
    args = [
        binary,
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=10",
        "-p", str(port),
    ]
    key = str(server.get("key") or "").strip()
    if key:
        args += ["-i", key]
    args.append(f"{user}@{host}")
    if extra:
        args += [str(item) for item in extra if str(item)]
    return args


def run_remote(cfg: Config, server: dict[str, Any], script: str, timeout: float = 60) -> CmdResult:
    """Выполняет sh-скрипт на сервере, передавая его на stdin (sh -s)."""
    binary = ssh_available(cfg)
    if not binary:
        return CmdResult(False, 127, missing=True, error=t("servers.noSsh"))
    cmd = build_ssh_args(cfg, server)
    return run(cmd + ["sh", "-s"], input_text=script, timeout=timeout)


def pipe_script(script: str) -> str:
    """Команда для ssh: безопасно передаёт скрипт через base64 в stdin sh.

    Нужна там, где нельзя отдать stdin процесса (фоновые задачи, у которых
    stdin занят или закрыт). В аргументе нет опасных символов, поэтому
    удалённая оболочка разберёт строку предсказуемо.
    """
    payload = base64.b64encode((script or "").encode("utf-8")).decode("ascii")
    return f"echo {payload} | base64 -d | sh"


# --------------------------------------------------------------------------- #
#  Проверка сервера (инвентарь)
# --------------------------------------------------------------------------- #

#: Портативный sh-скрипт: печатает KEY=VALUE. Ошибки отдельных команд скрыты,
#: чтобы отчёт формировался даже на минимальной системе.
CHECK_SCRIPT = r"""#!/bin/sh
# MegaTool: инвентаризация удалённого сервера (POSIX sh, без bash-расширений).
echo "MEGATOOL_REPORT=1"
printf 'HOSTNAME=%s\n' "$(hostname 2>/dev/null)"
printf 'USER=%s\n' "$(id -un 2>/dev/null)"
printf 'KERNEL=%s\n' "$(uname -r 2>/dev/null)"
printf 'ARCH=%s\n' "$(uname -m 2>/dev/null)"
if [ -r /etc/os-release ]; then
  . /etc/os-release 2>/dev/null
  printf 'OS_ID=%s\n' "${ID:-}"
  printf 'OS_PRETTY=%s\n' "${PRETTY_NAME:-${NAME:-}}"
  printf 'OS_LIKE=%s\n' "${ID_LIKE:-}"
fi
if sudo -n true 2>/dev/null; then
  echo "SUDO_NOPASS=yes"
else
  echo "SUDO_NOPASS=no"
fi
mt_tool() {
  mt_name=$1
  shift
  mt_out=""
  if command -v "$1" >/dev/null 2>&1; then
    mt_out=$("$@" 2>&1 | head -n1)
  fi
  printf 'TOOL_%s=%s\n' "$mt_name" "$mt_out"
}
mt_tool DOCKER docker --version
mt_tool COMPOSE docker compose version --short
mt_tool PODMAN podman --version
mt_tool NGINX nginx -v
mt_tool UFW ufw --version
mt_tool FIREWALL_CMD firewall-cmd --version
mt_tool SYSTEMCTL systemctl --version
mt_tool FAIL2BAN fail2ban-client --version
mt_tool CURL curl --version
mt_tool GIT git --version
mt_disk=$(df -Ph / 2>/dev/null | awk 'NR==2{print $4; exit}')
printf 'DISK_AVAIL=%s\n' "$mt_disk"
mt_mem=$(awk '/^MemTotal:/{printf "%d", $2/1024; exit}' /proc/meminfo 2>/dev/null)
if [ -z "$mt_mem" ]; then
  mt_mem=$(free -m 2>/dev/null | awk '/^Mem:/{print $2; exit}')
fi
printf 'MEM_MB=%s\n' "$mt_mem"
"""

#: Семейства дистрибутивов: id → семейство.
_FAMILIES: dict[str, tuple[str, ...]] = {
    "debian": ("debian", "ubuntu", "raspbian", "linuxmint", "pop", "devuan",
               "kali", "neon", "elementary", "zorin", "mx", "deepin"),
    "redhat": ("rhel", "centos", "fedora", "rocky", "almalinux", "ol", "oracle",
               "amzn", "cloudlinux", "alma", "scientific"),
    "arch": ("arch", "manjaro", "endeavouros", "garuda", "artix", "cachyos"),
    "suse": ("suse", "opensuse", "sles", "sled", "opensuse-leap", "opensuse-tumbleweed"),
}


def _family(os_id: str, os_like: str = "") -> str:
    """Определяет семейство пакетного менеджера по os-release."""
    candidates = [os_id.lower()] + re.split(r"[\s,]+", (os_like or "").lower())
    for item in candidates:
        item = item.strip()
        if not item:
            continue
        for family, ids in _FAMILIES.items():
            if item in ids or any(item.startswith(prefix) for prefix in ids):
                return family
    return "unknown"


def _as_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_report(text: str) -> dict[str, Any]:
    """Разбирает вывод CHECK_SCRIPT (строки KEY=VALUE) в структуру отчёта."""
    data: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key:
            data[key] = value.strip()

    os_id = (data.get("OS_ID") or "").lower()
    os_pretty = data.get("OS_PRETTY") or ""
    tools = {
        "docker": data.get("TOOL_DOCKER") or None,
        "compose": data.get("TOOL_COMPOSE") or None,
        "podman": data.get("TOOL_PODMAN") or None,
        "nginx": data.get("TOOL_NGINX") or None,
        "ufw": data.get("TOOL_UFW") or None,
        "firewall_cmd": data.get("TOOL_FIREWALL_CMD") or None,
        "systemctl": data.get("TOOL_SYSTEMCTL") or None,
        "fail2ban": data.get("TOOL_FAIL2BAN") or None,
        "curl": data.get("TOOL_CURL") or None,
        "git": data.get("TOOL_GIT") or None,
    }
    return {
        "reachable": data.get("MEGATOOL_REPORT") == "1",
        "os_id": os_id,
        "os_pretty": os_pretty,
        "family": _family(os_id, data.get("OS_LIKE", "")),
        "kernel": data.get("KERNEL") or "",
        "arch": data.get("ARCH") or "",
        "hostname": data.get("HOSTNAME") or "",
        "user": data.get("USER") or "",
        "sudo_nopass": (data.get("SUDO_NOPASS") or "").lower() in ("1", "yes", "true"),
        "tools": tools,
        "disk_avail": data.get("DISK_AVAIL") or "",
        "mem_mb": _as_int(data.get("MEM_MB")),
    }


def installed_tasks(report: dict[str, Any] | None) -> dict[str, bool]:
    """Помечает задачи, чьё ПО уже есть на сервере (по отчёту проверки)."""
    tools = (report or {}).get("tools") or {}
    result: dict[str, bool] = {}
    if "curl" in tools:
        result["base-tools"] = bool(tools.get("curl") and tools.get("git"))
    if "docker" in tools:
        result["docker-install"] = bool(tools.get("docker"))
    if "nginx" in tools:
        result["web-nginx"] = bool(tools.get("nginx"))
    if "fail2ban" in tools:
        result["security-fail2ban"] = bool(tools.get("fail2ban"))
    if "ufw" in tools:
        result["security-fw"] = bool(tools.get("ufw") or tools.get("firewall_cmd"))
    return result


def check_server(cfg: Config, server: dict[str, Any], timeout: float = 30) -> dict[str, Any]:
    """Синхронная проверка сервера: доступность и инвентарь."""
    result = run_remote(cfg, server, CHECK_SCRIPT, timeout=timeout)
    if result.missing:
        return {"ok": False, "reachable": False, "error": t("servers.noSsh")}
    if not result.ok:
        detail = (result.err or result.out or result.error or "").strip()
        return {
            "ok": False,
            "reachable": False,
            "error": t("servers.checkFailed", error=detail[:400] or t("servers.unknownError")),
            "output": (result.out or "")[-4000:],
        }
    report = parse_report(result.out)
    if not report["reachable"]:
        return {
            "ok": False,
            "reachable": False,
            "error": t("servers.notReachable"),
            "output": (result.out or "")[-4000:],
        }
    report["ok"] = True
    report["installed"] = installed_tasks(report)
    report["checked_at"] = iso()
    return report


# --------------------------------------------------------------------------- #
#  Параметры задач
# --------------------------------------------------------------------------- #

def _param_label(task: dict[str, Any], spec: dict[str, Any]) -> str:
    label = t(f"tasks.{task['id']}.params.{spec['name']}.label")
    return label if label != f"tasks.{task['id']}.params.{spec['name']}.label" else spec["name"]


def _bad_param(task: dict[str, Any], spec: dict[str, Any]) -> ValueError:
    return ValueError(t("servers.badParam", param=_param_label(task, spec), task=task["id"]))


def _check_param(task: dict[str, Any], spec: dict[str, Any], raw: Any) -> str:
    """Проверяет одно значение параметра, возвращает строку для скрипта."""
    kind = spec.get("kind", "text")
    value = "" if raw is None else str(raw).strip()

    if kind == "user":
        if not value:
            if spec.get("optional"):
                return ""
            raise _bad_param(task, spec)
        if not TASK_USER_RE.match(value):
            raise _bad_param(task, spec)
        return value

    if kind == "int":
        number = _as_int(value)
        if number is None:
            raise _bad_param(task, spec)
        low = int(spec.get("min", 0))
        high = int(spec.get("max", 2 ** 31 - 1))
        if not low <= number <= high:
            raise _bad_param(task, spec)
        return str(number)

    if kind == "tz":
        if not TZ_RE.match(value):
            raise _bad_param(task, spec)
        return value

    if kind == "sshkey":
        if not value:
            if spec.get("optional"):
                return ""
            raise _bad_param(task, spec)
        if "\n" in raw_str(raw) or "\r" in raw_str(raw) or not SSHKEY_RE.match(value):
            raise _bad_param(task, spec)
        return value

    if kind == "choice":
        choices = [str(item) for item in (spec.get("choices") or [])]
        if value not in choices:
            raise _bad_param(task, spec)
        return value

    # kind == "text"
    value = value.replace("\r", "").replace("\n", " ")
    limit = int(spec.get("maxLen", 200))
    if len(value) > limit:
        raise _bad_param(task, spec)
    pattern = spec.get("pattern")
    if value and pattern and not re.match(pattern, value):
        raise _bad_param(task, spec)
    return value


def raw_str(value: Any) -> str:
    """Строковое представление «как есть» (без strip) для проверки переводов строк."""
    return "" if value is None else str(value)


def validate_params(task_ids: Iterable[str], params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Проверяет параметры выбранных задач.

    Возвращает {"ok": True, "values": {...}} либо {"ok": False, "error": "..."}.
    Значения нормализованы и безопасны для подстановки в sh-скрипт.
    """
    ids = [str(item).strip() for item in (task_ids or []) if str(item).strip()]
    if not ids:
        return {"ok": False, "error": t("servers.noTasks")}
    known = {task["id"]: task for task in TASKS}
    for task_id in ids:
        if task_id not in known:
            return {"ok": False, "error": t("servers.unknownTask", task=task_id)}

    values: dict[str, str] = {}
    source = params if isinstance(params, dict) else {}
    for task in select_tasks(ids):
        for spec in task["params"]:
            raw = source.get(spec["name"], spec.get("default", ""))
            try:
                values[spec["name"]] = _check_param(task, spec, raw)
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
    return {"ok": True, "values": values}


def _render(text: str, values: dict[str, str]) -> str:
    """Подставляет значения параметров, не трогая shell-переменные вида ${HOME}."""
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return text


def _sh_quote(text: str) -> str:
    """Безопасная строка для sh: одинарные кавычки с экранированием."""
    return "'" + str(text).replace("'", "'\\''") + "'"


# --------------------------------------------------------------------------- #
#  Каталог задач
# --------------------------------------------------------------------------- #

#: Общие фрагменты установки через пакетный менеджер. Вставлены в задачи,
#: которым нужны пакеты: так каждый фрагмент самодостаточен (в том числе
#: для sudo-обёртки и для Ansible, где он выполняется отдельным процессом).
_INSTALL_HELPER = r"""mt_install() {
  if command -v apt-get >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y "$@"
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y "$@"
  elif command -v yum >/dev/null 2>&1; then
    yum install -y "$@"
  elif command -v pacman >/dev/null 2>&1; then
    pacman -S --needed --noconfirm "$@"
  elif command -v zypper >/dev/null 2>&1; then
    zypper --non-interactive install -y "$@"
  else
    echo "MegaTool: пакетный менеджер не найден" >&2
    return 1
  fi
}
"""

TASKS: list[dict[str, Any]] = [
    {
        "id": "base-update",
        "group": "base",
        "risk": "safe",
        "sudo": True,
        "params": [],
        "check": None,
        "script": r"""if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
elif command -v dnf >/dev/null 2>&1; then
  dnf -y makecache
elif command -v yum >/dev/null 2>&1; then
  yum -y makecache
elif command -v pacman >/dev/null 2>&1; then
  pacman -Sy --noconfirm
elif command -v zypper >/dev/null 2>&1; then
  zypper --non-interactive refresh
else
  echo "MegaTool: не удалось определить пакетный менеджер" >&2
  exit 1
fi""",
    },
    {
        "id": "base-tools",
        "group": "base",
        "risk": "safe",
        "sudo": True,
        "params": [
            {"name": "extra", "kind": "text", "default": "", "maxLen": 200,
             "pattern": PACKAGES_RE.pattern},
        ],
        "check": "command -v git >/dev/null 2>&1",
        "script": _INSTALL_HELPER + r"""packages="curl git htop vim tmux unzip jq ca-certificates {extra}"
echo "MegaTool: устанавливаем базовые утилиты:$packages"
mt_install $packages""",
    },
    {
        "id": "system-tz",
        "group": "system",
        "risk": "medium",
        "sudo": True,
        "params": [{"name": "tz", "kind": "tz", "default": "UTC"}],
        "check": "[ -n \"$(timedatectl show -p Timezone --value 2>/dev/null)\" ]",
        "script": r"""zone="{tz}"
if [ ! -f "/usr/share/zoneinfo/$zone" ]; then
  echo "MegaTool: зона $zone не найдена в /usr/share/zoneinfo" >&2
  exit 1
fi
if command -v timedatectl >/dev/null 2>&1; then
  timedatectl set-timezone "$zone"
else
  ln -sf "/usr/share/zoneinfo/$zone" /etc/localtime
  echo "$zone" > /etc/timezone
fi
date '+MegaTool: текущее время: %Y-%m-%d %H:%M:%S %Z'""",
    },
    {
        "id": "system-swap",
        "group": "system",
        "risk": "medium",
        "sudo": True,
        "params": [{"name": "size_gb", "kind": "int", "default": "2", "min": 1, "max": 64}],
        "check": "grep -qs '^/swapfile ' /etc/fstab",
        "script": r"""size_gb={size_gb}
swapfile=/swapfile
if [ -e "$swapfile" ]; then
  echo "MegaTool: $swapfile уже существует — размер не меняем"
else
  if command -v fallocate >/dev/null 2>&1; then
    fallocate -l "$size_gb"G "$swapfile"
  else
    dd if=/dev/zero of="$swapfile" bs=1M count=$((size_gb * 1024)) 2>/dev/null
  fi
  chmod 600 "$swapfile"
  mkswap "$swapfile"
fi
if ! swapon --show 2>/dev/null | grep -q "$swapfile"; then
  swapon "$swapfile" 2>/dev/null || true
fi
if ! grep -q "^$swapfile " /etc/fstab 2>/dev/null; then
  echo "$swapfile none swap sw 0 0" >> /etc/fstab
fi
free -m 2>/dev/null | head -n 2 || true""",
    },
    {
        "id": "user-create",
        "group": "user",
        "risk": "medium",
        "sudo": True,
        "params": [
            {"name": "user", "kind": "user", "default": "deploy"},
            {"name": "sshkey", "kind": "sshkey", "default": "", "optional": True},
        ],
        "check": "id {user} >/dev/null 2>&1",
        "script": r"""user="{user}"
if id "$user" >/dev/null 2>&1; then
  echo "MegaTool: пользователь $user уже существует"
else
  shell=/bin/bash
  [ -x "$shell" ] || shell=/bin/sh
  if command -v useradd >/dev/null 2>&1; then
    useradd -m -s "$shell" "$user"
  else
    adduser -D -s "$shell" "$user"
  fi
  echo "MegaTool: пользователь $user создан"
fi
key="{sshkey}"
if [ -n "$key" ]; then
  home_dir=$(getent passwd "$user" 2>/dev/null | cut -d: -f6)
  [ -n "$home_dir" ] || home_dir="/home/$user"
  if [ -d "$home_dir" ]; then
    mkdir -p "$home_dir/.ssh"
    chmod 700 "$home_dir/.ssh"
    if grep -qF "$key" "$home_dir/.ssh/authorized_keys" 2>/dev/null; then
      echo "MegaTool: SSH-ключ уже добавлен"
    else
      echo "$key" >> "$home_dir/.ssh/authorized_keys"
      chmod 600 "$home_dir/.ssh/authorized_keys"
      echo "MegaTool: SSH-ключ добавлен в $home_dir/.ssh/authorized_keys"
    fi
    chown -R "$user" "$home_dir/.ssh" 2>/dev/null || true
  else
    echo "MegaTool: домашний каталог $home_dir не найден — ключ не добавлен" >&2
  fi
fi""",
    },
    {
        "id": "security-ssh",
        "group": "security",
        "risk": "high",
        "sudo": True,
        "params": [],
        "check": "grep -qs '^PasswordAuthentication no' /etc/ssh/sshd_config",
        "script": r"""sshd_config=/etc/ssh/sshd_config
if [ ! -f "$sshd_config" ]; then
  echo "MegaTool: $sshd_config не найден — SSH-сервер не установлен?" >&2
  exit 1
fi
backup="$sshd_config.megatool-$(date +%Y%m%d-%H%M%S)"
cp -a "$sshd_config" "$backup"
echo "MegaTool: резервная копия: $backup"
dropin=/etc/ssh/sshd_config.d/99-megatool-hardening.conf
marker="# MegaTool: вход только по ключам"
if grep -qs 'Include /etc/ssh/sshd_config.d/' "$sshd_config" || [ -d /etc/ssh/sshd_config.d ]; then
  mkdir -p /etc/ssh/sshd_config.d
  cat > "$dropin" <<'MEGATOOL_SSHD_EOF'
# MegaTool: вход только по ключам
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
PubkeyAuthentication yes
MEGATOOL_SSHD_EOF
  echo "MegaTool: настройки записаны в $dropin"
else
  if grep -q "$marker" "$sshd_config"; then
    echo "MegaTool: настройки уже применены"
  else
    {
      echo ""
      echo "$marker"
      echo "PermitRootLogin no"
      echo "PasswordAuthentication no"
      echo "KbdInteractiveAuthentication no"
      echo "PubkeyAuthentication yes"
    } >> "$sshd_config"
    echo "MegaTool: настройки добавлены в $sshd_config"
  fi
fi
if command -v sshd >/dev/null 2>&1; then
  if ! sshd -t 2>&1; then
    echo "MegaTool: sshd -t не подтвердил конфигурацию — возвращаем резервную копию" >&2
    rm -f "$dropin"
    cp -a "$backup" "$sshd_config"
    exit 1
  fi
  echo "MegaTool: конфигурация sshd корректна"
fi
if command -v systemctl >/dev/null 2>&1; then
  systemctl restart ssh 2>/dev/null || systemctl restart sshd 2>/dev/null || true
fi""",
    },
    {
        "id": "security-fw",
        "group": "security",
        "risk": "high",
        "sudo": True,
        "params": [
            {"name": "backend", "kind": "choice", "default": "auto",
             "choices": ["auto", "ufw", "firewalld"]},
        ],
        "check": "command -v ufw >/dev/null 2>&1 || command -v firewall-cmd >/dev/null 2>&1",
        "script": r"""backend="{backend}"
open_ports="22 80 443"
if [ "$backend" = "firewalld" ] || { [ "$backend" = "auto" ] && command -v firewall-cmd >/dev/null 2>&1; }; then
  if ! command -v firewall-cmd >/dev/null 2>&1; then
    echo "MegaTool: firewalld не установлен" >&2
    exit 1
  fi
  if command -v systemctl >/dev/null 2>&1; then
    systemctl enable --now firewalld 2>/dev/null || true
  fi
  for port in $open_ports; do
    firewall-cmd --permanent --add-port="$port/tcp" >/dev/null
  done
  firewall-cmd --reload >/dev/null
  echo "MegaTool: firewalld — открыты порты:$open_ports"
else
  if ! command -v ufw >/dev/null 2>&1; then
    echo "MegaTool: ufw не установлен — установите ufw или выберите firewalld" >&2
    exit 1
  fi
  for port in $open_ports; do
    ufw allow "$port/tcp" >/dev/null
  done
  ufw --force enable
  echo "MegaTool: ufw — открыты порты:$open_ports"
fi""",
    },
    {
        "id": "docker-install",
        "group": "docker",
        "risk": "medium",
        "sudo": True,
        "params": [{"name": "user", "kind": "user", "default": "", "optional": True}],
        "check": "command -v docker >/dev/null 2>&1",
        "script": r"""if command -v docker >/dev/null 2>&1; then
  echo "MegaTool: Docker уже установлен: $(docker --version 2>/dev/null | head -n1)"
else
  tmp_script=$(mktemp)
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL https://get.docker.com -o "$tmp_script"
  elif command -v wget >/dev/null 2>&1; then
    wget -qO "$tmp_script" https://get.docker.com
  else
    echo "MegaTool: нужен curl или wget (задача base-tools)" >&2
    rm -f "$tmp_script"
    exit 1
  fi
  sh "$tmp_script"
  rm -f "$tmp_script"
  echo "MegaTool: Docker установлен"
fi
if command -v systemctl >/dev/null 2>&1; then
  systemctl enable --now docker 2>/dev/null || true
fi
group_user="{user}"
if [ -n "$group_user" ]; then
  if id "$group_user" >/dev/null 2>&1; then
    groupadd -f docker 2>/dev/null || true
    if command -v usermod >/dev/null 2>&1; then
      usermod -aG docker "$group_user" || true
    fi
    echo "MegaTool: пользователь $group_user добавлен в группу docker (нужен повторный вход)"
  else
    echo "MegaTool: пользователь $group_user не найден — группа docker не изменена" >&2
  fi
fi""",
    },
    {
        "id": "web-nginx",
        "group": "web",
        "risk": "safe",
        "sudo": True,
        "params": [],
        "check": "command -v nginx >/dev/null 2>&1",
        "script": _INSTALL_HELPER + r"""if command -v nginx >/dev/null 2>&1; then
  echo "MegaTool: nginx уже установлен: $(nginx -v 2>&1 | head -n1)"
else
  mt_install nginx
  echo "MegaTool: nginx установлен"
fi
if command -v systemctl >/dev/null 2>&1; then
  systemctl enable nginx 2>/dev/null || true
  systemctl restart nginx 2>/dev/null || systemctl start nginx 2>/dev/null || true
else
  nginx -t && (nginx -s reload 2>/dev/null || nginx)
fi
echo 'MegaTool: nginx запущен'""",
    },
    {
        "id": "security-fail2ban",
        "group": "security",
        "risk": "medium",
        "sudo": True,
        "params": [],
        "check": "command -v fail2ban-client >/dev/null 2>&1",
        "script": _INSTALL_HELPER + r"""if command -v fail2ban-client >/dev/null 2>&1; then
  echo "MegaTool: fail2ban уже установлен"
else
  mt_install fail2ban
  echo "MegaTool: fail2ban установлен"
fi
if command -v systemctl >/dev/null 2>&1; then
  systemctl enable fail2ban 2>/dev/null || true
  systemctl restart fail2ban 2>/dev/null || systemctl start fail2ban 2>/dev/null || true
fi
echo 'MegaTool: fail2ban включён'""",
    },
]

_TASKS_BY_ID = {task["id"]: task for task in TASKS}


def select_tasks(task_ids: Iterable[str]) -> list[dict[str, Any]]:
    """Выбирает существующие задачи в каноническом порядке каталога."""
    wanted = {str(item).strip() for item in (task_ids or []) if str(item).strip()}
    return [task for task in TASKS if task["id"] in wanted]


def task_catalog(report: dict[str, Any] | None = None) -> dict[str, Any]:
    """Локализованный каталог задач (без текста скриптов)."""
    installed = installed_tasks(report) if report else {}
    items: list[dict[str, Any]] = []
    for task in TASKS:
        params = []
        for spec in task["params"]:
            name = spec["name"]
            base_key = f"tasks.{task['id']}.params.{name}"
            label = t(f"{base_key}.label")
            hint = t(f"{base_key}.hint")
            choices = [str(item) for item in (spec.get("choices") or [])]
            choice_labels = {}
            for choice in choices:
                key = f"{base_key}.choices.{choice}"
                text = t(key)
                choice_labels[choice] = text if text != key else choice
            params.append({
                "name": name,
                "kind": spec.get("kind", "text"),
                "default": spec.get("default", ""),
                "label": label if label != f"{base_key}.label" else name,
                "hint": hint if hint != f"{base_key}.hint" else "",
                "choices": choices,
                "choice_labels": choice_labels,
                "min": spec.get("min"),
                "max": spec.get("max"),
                "optional": bool(spec.get("optional")),
            })
        items.append({
            "id": task["id"],
            "group": task["group"],
            "risk": task["risk"],
            "sudo": bool(task["sudo"]),
            "title": t(f"tasks.{task['id']}.title"),
            "description": t(f"tasks.{task['id']}.description"),
            "params": params,
            "check": bool(task.get("check")),
            "installed": installed.get(task["id"]),
        })
    return {
        "items": items,
        "groups": [{"id": group, "title": t(f"tasks.groups.{group}")} for group in GROUPS],
        "risks": {risk: t(f"tasks.risk.{risk}") for risk in ("safe", "medium", "high")},
    }


# --------------------------------------------------------------------------- #
#  Генерация скриптов
# --------------------------------------------------------------------------- #

def _sudo_state(server: dict[str, Any], report: dict[str, Any] | None) -> bool | None:
    """Доступен ли sudo без пароля: True/False/None (неизвестно)."""
    if report is not None and "sudo_nopass" in report:
        return bool(report["sudo_nopass"])
    if "sudo_nopass" in server:
        value = server.get("sudo_nopass")
        return None if value is None else bool(value)
    return None


def _sudo_block(task: dict[str, Any], fragment: str, state: bool | None) -> str:
    """Оборачивает фрагмент в sudo с внятным сообщением при неудаче."""
    task_id = task["id"]
    if state is False:
        message = _sh_quote(t("servers.needRoot", task=task_id))
        return (
            f"echo {message} >&2\n"
            f'MEGATOOL_ERRORS="$MEGATOOL_ERRORS {task_id}"'
        )
    # Весь фрагмент передаётся одним аргументом sh -c: кавычки экранированы,
    # вложенные heredoc и фигурные скобки внутри не мешают разбору.
    command = f"sudo -n sh -c {_sh_quote(fragment)}"
    if state is True:
        return command
    message = _sh_quote(t("servers.needRoot", task=task_id))
    failure = _sh_quote(t("servers.commandFailed", task=task_id))
    return (
        "if sudo -n true 2>/dev/null; then\n"
        f"  if ! {command}; then\n"
        f"    echo {failure} >&2\n"
        f'    MEGATOOL_ERRORS="$MEGATOOL_ERRORS {task_id}"\n'
        "    exit 1\n"
        "  fi\n"
        "else\n"
        f"  echo {message} >&2\n"
        f'  MEGATOOL_ERRORS="$MEGATOOL_ERRORS {task_id}"\n'
        "fi"
    )


def _header_comment(server: dict[str, Any], tasks: list[dict[str, Any]]) -> list[str]:
    name = str(server.get("name") or server.get("host") or "")
    user = str(server.get("user") or "root")
    host = str(server.get("host") or "")
    port = int(server.get("port") or 22)
    ids = ", ".join(task["id"] for task in tasks)
    return [
        "#!/bin/sh",
        f"# MegaTool — настройка сервера «{name}» ({user}@{host}:{port})",
        f"# Задачи: {ids}",
        f"# Создано: {iso()}",
    ]


def build_script(
    server: dict[str, Any],
    task_ids: Iterable[str],
    params: dict[str, Any] | None,
    cfg: Config,
    report: dict[str, Any] | None = None,
) -> str:
    """Собирает один sh-скрипт из выбранных задач.

    Скрипт рассчитан на `ssh ... sh -s` (stdin) и на Ansible shell.
    Задачи, которым нужен root, оборачиваются в sudo -n; если права
    заведомо недоступны — задача пропускается с понятным сообщением.
    """
    del cfg  # настройки пока не влияют на текст, оставлены для совместимости API
    check = validate_params(task_ids, params)
    if not check["ok"]:
        raise ValueError(check["error"])
    tasks = select_tasks(task_ids)
    values = check["values"]
    state = _sudo_state(server, report)

    lines = _header_comment(server, tasks)
    lines += [
        "set -e",
        'MEGATOOL_ERRORS=""',
        f"echo {_sh_quote(t('servers.startSetup', name=server.get('name') or server.get('host') or '', count=len(tasks)))}",
        "",
    ]
    for task in tasks:
        task_id = task["id"]
        fragment = _render(task["script"], values)
        lines.append(f'echo "== MegaTool: {task_id} =="')
        if task.get("check"):
            condition = _render(task["check"], values)
            lines.append(f"if {condition}; then")
            lines.append(f"  echo {_sh_quote(t('servers.alreadyInstalled', task=task_id))}")
            lines.append("fi")
        if task["sudo"]:
            lines.append(_sudo_block(task, fragment, state))
        else:
            lines.append(fragment)
        lines.append("")

    lines += [
        'if [ -n "$MEGATOOL_ERRORS" ]; then',
        f"  echo {_sh_quote(t('servers.finishedErrors'))}"'"$MEGATOOL_ERRORS" >&2',
        "  exit 1",
        "fi",
        f"echo {_sh_quote(t('servers.finished'))}",
        "",
    ]
    return "\n".join(lines)


def build_playbook(
    server: dict[str, Any],
    task_ids: Iterable[str],
    params: dict[str, Any] | None,
    cfg: Config,
    report: dict[str, Any] | None = None,
) -> str:
    """Собирает Ansible-плейбук (become: true) с теми же фрагментами задач."""
    del cfg
    del report  # become: true снимает необходимость в sudo -n
    check = validate_params(task_ids, params)
    if not check["ok"]:
        raise ValueError(check["error"])
    tasks = select_tasks(task_ids)
    values = check["values"]
    name = str(server.get("name") or server.get("host") or "")
    user = str(server.get("user") or "root")
    host = str(server.get("host") or "")
    port = int(server.get("port") or 22)
    key = str(server.get("key") or "")

    lines = [
        "---",
        "# MegaTool — generated Ansible playbook",
        f"# Server: {name} ({user}@{host}:{port})",
        "# Tasks: " + ", ".join(task["id"] for task in tasks),
        f"# Generated: {iso()}",
        "#",
        "# Example inventory (inventory.ini):",
        "#   [servers]",
        f"#   {name or 'web1'} ansible_host={host} ansible_user={user} "
        f"ansible_ssh_private_key_file={key or '~/.ssh/id_ed25519'}",
        "#",
        "# Run:",
        f"#   ansible-playbook -i inventory.ini {server.get('id') or 'servers'}.yml",
        "",
        f"- name: {_yaml_scalar('MegaTool: ' + t('servers.playbookName', name=name))}",
        "  hosts: all",
        "  become: true",
        "  gather_facts: false",
        "  tasks:",
    ]
    for task in tasks:
        title = t(f"tasks.{task['id']}.title")
        fragment = _render(task["script"], values)
        cmd = f'echo "== MegaTool: {task["id"]} =="\n{fragment}\n'
        lines += [
            f"    - name: {_yaml_scalar('MegaTool: ' + task['id'] + ' — ' + title)}",
            "      ansible.builtin.shell:",
            "        cmd: |",
        ]
        for cmd_line in cmd.rstrip("\n").splitlines():
            lines.append("          " + cmd_line if cmd_line else "")
        lines += ["        executable: /bin/sh", ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def _yaml_scalar(text: str) -> str:
    """Скаляр YAML в двойных кавычках (валиден для любых названий задач)."""
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


# --------------------------------------------------------------------------- #
#  Хранилище серверов
# --------------------------------------------------------------------------- #

def _clean_text(value: Any, limit: int = 500) -> str:
    text = "" if value is None else str(value)
    return "".join(ch for ch in text if ch == "\t" or ord(ch) >= 32).strip()[:limit]


def _validate_server(data: dict[str, Any], partial: bool = False) -> tuple[dict[str, Any], str]:
    """Проверяет поля сервера. Возвращает (очищенные поля, текст ошибки)."""
    clean: dict[str, Any] = {}

    if not partial or "name" in data:
        clean["name"] = _clean_text(data.get("name"), 64)
    if not partial or "host" in data:
        host = _clean_text(data.get("host"), 253)
        if not HOST_RE.match(host):
            return {}, t("servers.badHost")
        clean["host"] = host
    if not partial or "user" in data:
        user = _clean_text(data.get("user"), 32) or "root"
        if not SERVER_USER_RE.match(user):
            return {}, t("servers.badUser")
        clean["user"] = user
    if "port" in data or not partial:
        try:
            port = int(data.get("port", 22) or 22)
        except (TypeError, ValueError):
            return {}, t("servers.badPort")
        if not 1 <= port <= 65535:
            return {}, t("servers.badPort")
        clean["port"] = port
    if "key" in data or not partial:
        key = _clean_text(data.get("key"), 512)
        if "\n" in str(data.get("key") or "") or "\r" in str(data.get("key") or ""):
            return {}, t("servers.badKey")
        clean["key"] = key
    if "note" in data or not partial:
        clean["note"] = _clean_text(data.get("note"), 500)

    if not clean.get("name") and clean.get("host"):
        clean["name"] = clean["host"]
    return clean, ""


class ServersManager:
    """Список SSH-серверов в config.json (пароли не хранятся)."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._lock = threading.RLock()
        #: Последние отчёты проверки — только в памяти (не секреты, но и не модель).
        self.reports: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------ #
    def _items(self) -> list[dict[str, Any]]:
        raw = self.cfg.get("servers", []) or []
        return [dict(item) for item in raw if isinstance(item, dict) and item.get("id")]

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            items = self._items()
        return sorted(items, key=lambda item: (str(item.get("name", "")).lower(),
                                               str(item.get("id", ""))))

    def get(self, server_id: str) -> dict[str, Any] | None:
        with self._lock:
            for item in self._items():
                if item.get("id") == server_id:
                    return item
        return None

    # ------------------------------------------------------------------ #
    def add(self, data: Any) -> dict[str, Any]:
        payload = data if isinstance(data, dict) else {}
        clean, error = _validate_server(payload, partial=False)
        if error:
            return {"ok": False, "error": error}
        server = {
            "id": uuid.uuid4().hex[:10],
            "name": clean.get("name") or clean.get("host", ""),
            "host": clean["host"],
            "port": clean["port"],
            "user": clean["user"],
            "key": clean.get("key", ""),
            "note": clean.get("note", ""),
            "created_ts": time.time(),
        }
        with self._lock:
            items = self._items()
            items.append(server)
            self.cfg.set("servers", items)
            self.cfg.save()
        return {"ok": True, "server": dict(server)}

    def update(self, server_id: str, data: Any) -> dict[str, Any]:
        payload = data if isinstance(data, dict) else {}
        with self._lock:
            items = self._items()
            index = next((i for i, item in enumerate(items) if item.get("id") == server_id), -1)
            if index < 0:
                return {"ok": False, "error": t("servers.notFound")}
            clean, error = _validate_server(payload, partial=True)
            if error:
                return {"ok": False, "error": error}
            server = dict(items[index])
            server.update(clean)
            server["id"] = server_id
            items[index] = server
            self.cfg.set("servers", items)
            self.cfg.save()
        return {"ok": True, "server": dict(server)}

    def delete(self, server_id: str) -> dict[str, Any]:
        with self._lock:
            items = self._items()
            remaining = [item for item in items if item.get("id") != server_id]
            if len(remaining) == len(items):
                return {"ok": False, "error": t("servers.notFound")}
            self.cfg.set("servers", remaining)
            self.cfg.save()
            self.reports.pop(server_id, None)
        return {"ok": True, "deleted": server_id}

    # ------------------------------------------------------------------ #
    def remember_report(self, server_id: str, report: dict[str, Any]) -> None:
        """Запоминает результат проверки для подсказок «уже установлено»."""
        with self._lock:
            self.reports[server_id] = dict(report)

    def last_report(self, server_id: str) -> dict[str, Any] | None:
        with self._lock:
            report = self.reports.get(server_id)
            return dict(report) if report else None


# --------------------------------------------------------------------------- #
#  Запуск через менеджер задач
# --------------------------------------------------------------------------- #

def submit_script_job(
    cfg: Config,
    jobs: JobManager,
    server: dict[str, Any],
    script: str,
    title: str,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Запускает сгенерированный скрипт как фоновую задачу (kind=ssh)."""
    if not ssh_available(cfg):
        return {"ok": False, "error": t("servers.noSsh")}
    base = build_ssh_args(cfg, server)
    try:
        job = jobs.submit(title=title, cmd=base + ["sh", "-s"], kind="ssh",
                          meta=meta or {}, stdin_text=script)
    except TypeError:  # старая версия JobManager без stdin_text
        job = jobs.submit(title=title, cmd=base + [pipe_script(script)], kind="ssh",
                          meta=meta or {})
    return {"ok": True, "job": job.to_dict()}


def submit_command_job(
    cfg: Config,
    jobs: JobManager,
    server: dict[str, Any],
    command: str,
    title: str,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Запускает свободную команду на сервере как фоновую задачу (kind=ssh)."""
    if not ssh_available(cfg):
        return {"ok": False, "error": t("servers.noSsh")}
    base = build_ssh_args(cfg, server)
    job = jobs.submit(title=title, cmd=base + [command], kind="ssh", meta=meta or {})
    return {"ok": True, "job": job.to_dict()}


__all__ = [
    "TASKS", "CHECK_SCRIPT", "ServersManager", "GROUPS", "MAX_COMMAND",
    "ssh_available", "scp_available", "build_ssh_args", "run_remote", "pipe_script",
    "parse_report", "check_server", "installed_tasks", "validate_params",
    "select_tasks", "task_catalog", "build_script", "build_playbook",
    "submit_script_job", "submit_command_job",
]

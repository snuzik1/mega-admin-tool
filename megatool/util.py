"""Низкоуровневые утилиты: запуск внешних команд, поиск бинарников, форматирование.

Здесь нет бизнес-логики: только безопасные помощники, которые используют остальные
модули. Все внешние программы запускаются БЕЗ shell (argv-список), поэтому ввод
пользователя не может превратиться в произвольную команду.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

from .i18n import t

#: Регулярка для «безопасных» идентификаторов (имена контейнеров, сервисов, групп).
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}$")
_SAFE_LIMIT_RE = re.compile(r"^[A-Za-z0-9_\-\.,:&!*\[\]]{1,256}$")


@dataclass
class CmdResult:
    """Результат запуска внешней команды."""

    ok: bool
    code: int
    out: str = ""
    err: str = ""
    duration: float = 0.0
    cmd: List[str] = field(default_factory=list)
    missing: bool = False  # бинарник не найден в системе
    error: str = ""  # текстовая причина сбоя (timeout и т.п.)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "code": self.code,
            "out": self.out,
            "err": self.err,
            "duration": round(self.duration, 3),
            "missing": self.missing,
            "error": self.error,
        }

    def lines(self, limit: Optional[int] = None) -> List[str]:
        data = (self.out or "").splitlines()
        if limit:
            data = data[:limit]
        return data


def which(name: str, override: Optional[str] = None) -> Optional[str]:
    """Ищет бинарник: сначала пользовательский путь из настроек, потом PATH."""
    if override:
        p = Path(override)
        if p.is_file():
            return str(p)
    if not name:
        return None
    return shutil.which(name)


def run(
    cmd: Sequence[str],
    timeout: float = 20.0,
    cwd: Optional[Union[str, os.PathLike]] = None,
    env: Optional[Dict[str, str]] = None,
    input_text: Optional[str] = None,
    extra_env: Optional[Dict[str, str]] = None,
) -> CmdResult:
    """Запускает команду и возвращает CmdResult. Никогда не бросает исключений."""
    argv = [str(c) for c in cmd]
    started = time.perf_counter()
    real_env = dict(os.environ) if env is None else dict(env)
    if extra_env:
        real_env.update({k: str(v) for k, v in extra_env.items()})
    real_env.setdefault("PYTHONIOENCODING", "utf-8")
    # Подсказка CLI-инструментам, что вывод должен быть без интерактива/цвета.
    real_env.setdefault("NO_COLOR", "1")
    real_env.setdefault("TERM", "dumb")
    real_env.setdefault("LANG", real_env.get("LANG", "C.UTF-8"))

    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            env=real_env,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return CmdResult(
            ok=proc.returncode == 0,
            code=proc.returncode,
            out=proc.stdout or "",
            err=proc.stderr or "",
            duration=time.perf_counter() - started,
            cmd=argv,
        )
    except FileNotFoundError:
        return CmdResult(False, 127, cmd=argv, missing=True,
                         error=t("util.programNotFound", name=argv[0]),
                         duration=time.perf_counter() - started)
    except subprocess.TimeoutExpired:
        return CmdResult(False, -1, cmd=argv, error=t("util.timeoutExceeded", timeout=f"{timeout:g}"),
                         duration=time.perf_counter() - started)
    except OSError as exc:  # права, битый образ и т.п.
        return CmdResult(False, -1, cmd=argv, error=str(exc),
                         duration=time.perf_counter() - started)


def run_json(cmd: Sequence[str], **kwargs: Any) -> Optional[Any]:
    """Запускает команду и пытается разобрать stdout как JSON."""
    res = run(cmd, **kwargs)
    if not res.ok or not res.out.strip():
        return None
    try:
        return json.loads(res.out)
    except json.JSONDecodeError:
        return None


def json_lines(text: str) -> List[Dict[str, Any]]:
    """Парсит построчный JSON (docker --format '{{json .}}')."""
    items: List[Dict[str, Any]] = []
    for line in (text or "").splitlines():
        line = line.strip().rstrip(",")
        if not line or line in ("[", "]"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            items.append(obj)
        elif isinstance(obj, list):
            items.extend([o for o in obj if isinstance(o, dict)])
    return items


def json_any(text: str) -> Optional[Any]:
    """Достаёт первый JSON-массив/объект из текста вывода (терпимо к мусору)."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("[", "]"), ("{", "}")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    return None


# --------------------------------------------------------------------------- #
#  Форматирование значений
# --------------------------------------------------------------------------- #

def human_bytes(value: Optional[Union[float, int]], suffix: str = "B") -> str:
    if value is None:
        return "—"
    value = float(value)
    for unit in ("", "K", "M", "G", "T", "P"):
        if abs(value) < 1024 or unit == "P":
            if unit == "":
                return f"{value:.0f} {suffix}"
            return f"{value:.1f} {unit}{suffix}"
        value /= 1024
    return f"{value:.1f} P{suffix}"


def human_rate(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return t("util.perSecond", value=human_bytes(value))


def human_duration(seconds: Optional[Union[float, int]]) -> str:
    if seconds is None:
        return "—"
    seconds = int(max(0, seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return t("util.durationDays", days=days, hours=hours, minutes=minutes)
    if hours:
        return t("util.durationHours", hours=hours, minutes=minutes)
    if minutes:
        return t("util.durationMinutes", minutes=minutes, secs=secs)
    return t("util.durationSeconds", secs=secs)


def human_pct(value: Optional[float], digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}%"


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# --------------------------------------------------------------------------- #
#  Время
# --------------------------------------------------------------------------- #

def now_ts() -> float:
    return time.time()


def iso(ts: Optional[float] = None) -> str:
    dt = datetime.fromtimestamp(ts if ts is not None else time.time(), tz=timezone.utc)
    return dt.astimezone().isoformat(timespec="seconds")


def parse_iso(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
#  Файлы и строки
# --------------------------------------------------------------------------- #

def ensure_dir(path: Union[str, os.PathLike]) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def is_within(path: Union[str, os.PathLike], root: Union[str, os.PathLike]) -> bool:
    """True, если путь лежит внутри root (замена Path.is_relative_to из 3.9)."""
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
    except (OSError, ValueError):
        return False
    return True


def write_text(path: Union[str, os.PathLike], text: str, mode: Optional[int] = None) -> None:
    """Пишет текст, сохраняя переводы строк.

    `Path.write_text(newline=...)` появился только в Python 3.10, поэтому
    используем обычный open — поведение одинаково на всех поддерживаемых версиях.
    """
    target = Path(path)
    ensure_dir(target.parent)
    with open(target, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    if mode is not None:
        try:
            os.chmod(target, mode)
        except OSError:
            pass


def atomic_write(path: Union[str, os.PathLike], text: str, mode: Optional[int] = None) -> None:
    """Пишет файл через временный, чтобы не оставить «половину» при сбое.

    Временный файл уникален: параллельные записи в один target (например,
    статус задачи из потока-исполнителя и из cancel) не перетирают tmp-файл
    друг друга. Побеждает последняя атомарная замена.
    """
    target = Path(path)
    ensure_dir(target.parent)
    final_mode = mode
    if final_mode is None:
        try:
            final_mode = target.stat().st_mode & 0o777
        except OSError:
            final_mode = 0o644
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-megatool-", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            fd = -1
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp_name, final_mode)
        except OSError:
            pass
        os.replace(tmp_name, target)
        if mode is not None:
            try:
                os.chmod(target, mode)
            except OSError:
                pass
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


def read_text(path: Union[str, os.PathLike], max_bytes: Optional[int] = None) -> str:
    data = Path(path).read_bytes()
    if max_bytes is not None and len(data) > max_bytes:
        data = data[:max_bytes]
    return data.decode("utf-8", errors="replace")


def read_json(path: Union[str, os.PathLike], default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Union[str, os.PathLike], data: Any, mode: Optional[int] = None) -> None:
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2), mode=mode)


def append_jsonl(path: Union[str, os.PathLike], data: Dict[str, Any]) -> None:
    ensure_dir(Path(path).parent)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(data, ensure_ascii=False) + "\n")


def read_jsonl(path: Union[str, os.PathLike], limit: Optional[int] = None) -> List[Dict[str, Any]]:
    p = Path(path)
    if not p.is_file():
        return []
    out: List[Dict[str, Any]] = []
    try:
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                out.append(obj)
    except OSError:
        return []
    if limit:
        out = out[-limit:]
    return out


def tail_lines(text: str, count: int) -> List[str]:
    lines = (text or "").splitlines()
    return lines[-count:] if count > 0 else lines


def safe_id(value: str, default: str = "") -> str:
    """Проверяет идентификатор (имя контейнера/сервиса/группы) на безопасность."""
    value = (value or "").strip()
    if _SAFE_ID_RE.match(value):
        return value
    return default


def is_safe_limit(value: str) -> bool:
    """Ansible --limit допускает список хостов/групп, шаблоны и разделители."""
    return bool(_SAFE_LIMIT_RE.match((value or "").strip()))


def rand_token(nbytes: int = 24) -> str:
    return secrets.token_urlsafe(nbytes)


def rand_password(length: int = 20) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#%^*-_=+"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def slugify(value: str, fallback: str = "app") -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-._")
    return value or fallback


def redact(value: str, keep: int = 4) -> str:
    """Маскирует секрет для логов и UI."""
    if not value:
        return ""
    if len(value) <= keep:
        return "•" * len(value)
    return value[:keep] + "•" * min(12, len(value) - keep)


def split_kv(items: Iterable[str]) -> Dict[str, str]:
    """Преобразует ['a=1', 'b=2'] в словарь (значения могут содержать '=')."""
    out: Dict[str, str] = {}
    for item in items or []:
        if not item:
            continue
        key, _, val = str(item).partition("=")
        key = key.strip()
        if key:
            out[key] = val.strip()
    return out

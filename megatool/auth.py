"""Локальная аутентификация MegaTool: одна учётная запись администратора.

Пароль не хранится в открытом виде. В ``auth.json`` лежат только соль и
PBKDF2-SHA256-хеш; файл создаётся с правами ``0600``, каталог — ``0700``.
По умолчанию файл находится рядом с ``config.json`` (``~/.megatool/auth.json``),
но путь можно переопределить переменной ``MEGATOOL_AUTH_FILE``.

Учётная запись одна и имеет полный доступ ко всем разделам MegaTool. Для
неё нет отдельных ролей: это осознанно простая модель для локального
инструмента. Веб-сессия живёт в памяти процесса, поэтому после перезапуска
MegaTool нужно войти заново.
"""

from __future__ import annotations

import base64
import binascii
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import HOME_DIR
from .i18n import t

#: Имя cookie с сессией. Менять нужно согласованно с frontend и middleware.
COOKIE_NAME = "megatool_session"

AUTH_VERSION = 1
DEFAULT_USERNAME = "admin"
USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
ALGORITHM = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 600_000
SALT_BYTES = 16
HASH_BYTES = 32
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_BYTES = 4096
SESSION_TTL_SECONDS = 12 * 60 * 60
MAX_SESSIONS = 32
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 60


class AuthError(Exception):
    """Ошибка конфигурации или файла учётных данных."""


def default_auth_path(config_path: Optional[Path] = None) -> Path:
    """Путь к auth.json: env override, иначе рядом с config.json."""
    override = os.environ.get("MEGATOOL_AUTH_FILE", "").strip()
    if override:
        return Path(os.path.expandvars(override)).expanduser()
    if config_path is not None:
        return Path(config_path).expanduser().parent / "auth.json"
    return HOME_DIR / "auth.json"


def _iterations() -> int:
    """Число итераций PBKDF2. Переопределяется только для тестов/стендов."""
    raw = os.environ.get("MEGATOOL_AUTH_ITERATIONS", "")
    if not raw:
        return PBKDF2_ITERATIONS
    try:
        value = int(raw)
    except ValueError:
        return PBKDF2_ITERATIONS
    return min(10_000_000, max(1_000, value))


def validate_password(password: str) -> None:
    """Проверяет длину пароля до хеширования."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(t("auth.passwordShort", n=MIN_PASSWORD_LENGTH))
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise AuthError(t("auth.passwordLong"))


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(value: Any, expected: Optional[int] = None) -> bytes:
    try:
        data = base64.b64decode(str(value), validate=True)
    except (ValueError, binascii.Error):
        raise AuthError(t("auth.fileInvalid"))
    if expected is not None and len(data) != expected:
        raise AuthError(t("auth.fileInvalid"))
    return data


def _password_hash(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations, dklen=HASH_BYTES
    )


def make_record(username: str, password: str) -> Dict[str, Any]:
    """Создаёт запись с солью и хешем. Пароль в запись не попадает."""
    username = (username or DEFAULT_USERNAME).strip()
    if not USERNAME_RE.match(username):
        raise AuthError(t("auth.badUsername"))
    validate_password(password)
    salt = secrets.token_bytes(SALT_BYTES)
    iterations = _iterations()
    digest = _password_hash(password, salt, iterations)
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    return {
        "version": AUTH_VERSION,
        "username": username,
        "algorithm": ALGORITHM,
        "iterations": iterations,
        "salt": _b64(salt),
        "hash": _b64(digest),
        "created_at": now,
        "updated_at": now,
    }


def _check_dir_security(directory: Path) -> None:
    """Каталог с секретом не должен быть доступен группе/остальным."""
    if os.name != "posix":
        return
    try:
        info = directory.stat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise AuthError(t("auth.fileUnreadable", path=directory, error=exc))
    if not stat.S_ISDIR(info.st_mode):
        raise AuthError(t("auth.dirNotDir", path=directory))
    if info.st_uid != os.geteuid():
        raise AuthError(t("auth.dirOwner", path=directory))
    mode = stat.S_IMODE(info.st_mode)
    if mode & 0o077:
        raise AuthError(t("auth.dirPermissions", path=directory, mode=oct(mode)))


def _check_file_security(path: Path) -> None:
    """Не даём читать чужой/слишком открытый файл с секретом."""
    if path.is_symlink():
        raise AuthError(t("auth.fileSymlink", path=path))
    try:
        info = path.stat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise AuthError(t("auth.fileUnreadable", path=path, error=exc))
    if not stat.S_ISREG(info.st_mode):
        raise AuthError(t("auth.fileNotRegular", path=path))
    if os.name != "posix":
        return
    if info.st_mode & 0o077:
        raise AuthError(t("auth.filePermissions", path=path, mode=oct(info.st_mode & 0o777)))
    if info.st_uid != os.geteuid():
        raise AuthError(t("auth.fileOwner", path=path))
    _check_dir_security(path.parent)


def load_record(path: Path) -> Optional[Dict[str, Any]]:
    """Читает и валидирует auth.json. None — файла ещё нет."""
    path = Path(path)
    _check_file_security(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise AuthError(t("auth.fileUnreadable", path=path, error=exc))
    try:
        record = json.loads(text)
    except json.JSONDecodeError:
        raise AuthError(t("auth.fileInvalid"))
    if not isinstance(record, dict):
        raise AuthError(t("auth.fileInvalid"))
    if record.get("version") != AUTH_VERSION or record.get("algorithm") != ALGORITHM:
        raise AuthError(t("auth.fileUnsupported"))
    username = record.get("username")
    if not isinstance(username, str) or not USERNAME_RE.match(username):
        raise AuthError(t("auth.fileInvalid"))
    try:
        iterations = int(record.get("iterations"))
    except (TypeError, ValueError):
        raise AuthError(t("auth.fileInvalid"))
    if iterations < 1_000 or iterations > 10_000_000:
        raise AuthError(t("auth.fileInvalid"))
    _unb64(record.get("salt"), SALT_BYTES)
    _unb64(record.get("hash"), HASH_BYTES)
    return record


def save_record(path: Path, record: Dict[str, Any]) -> None:
    """Атомарно пишет auth.json с правами 0600 и каталогом 0700."""
    path = Path(path)
    parent = path.parent
    created = not parent.exists()
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AuthError(t("auth.fileWrite", path=parent, error=exc))
    if os.name == "posix":
        if created:
            try:
                os.chmod(parent, 0o700)
            except OSError as exc:
                raise AuthError(t("auth.fileWrite", path=parent, error=exc))
        _check_dir_security(parent)
    if path.exists():
        _check_file_security(path)

    fd = -1
    tmp_name = ""
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=".auth-", suffix=".tmp", dir=str(parent))
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            fd = -1
            json.dump(record, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == "posix":
            os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
        if os.name == "posix":
            os.chmod(path, 0o600)
    except OSError as exc:
        raise AuthError(t("auth.fileWrite", path=path, error=exc))
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp_name:
            try:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
            except OSError:
                pass


def verify_password(record: Dict[str, Any], password: str) -> bool:
    """Проверяет пароль по записи; не бросает исключений."""
    try:
        iterations = int(record.get("iterations"))
        salt = _unb64(record.get("salt"), SALT_BYTES)
        expected = _unb64(record.get("hash"), HASH_BYTES)
        actual = _password_hash(str(password), salt, iterations)
    except (AuthError, TypeError, ValueError):
        return False
    return hmac.compare_digest(expected, actual)


class SessionStore:
    """Сессии в памяти процесса: случайный токен, TTL и ограничение количества."""

    def __init__(self, ttl: int = SESSION_TTL_SECONDS, max_sessions: int = MAX_SESSIONS) -> None:
        self.ttl = int(ttl)
        self.max_sessions = max(1, int(max_sessions))
        self._items: Dict[str, float] = {}
        self._lock = threading.Lock()

    def _cleanup(self, now: float) -> None:
        expired = [token for token, expires in self._items.items() if expires <= now]
        for token in expired:
            self._items.pop(token, None)

    def create(self) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self._cleanup(now)
            if len(self._items) >= self.max_sessions:
                oldest = min(self._items, key=self._items.get)
                self._items.pop(oldest, None)
            self._items[token] = now + self.ttl
        return token

    def validate(self, token: Optional[str]) -> bool:
        if not token:
            return False
        now = time.time()
        with self._lock:
            expires = self._items.get(token)
            if expires is None:
                return False
            if expires <= now:
                self._items.pop(token, None)
                return False
            return True

    def revoke(self, token: Optional[str]) -> None:
        if not token:
            return
        with self._lock:
            self._items.pop(token, None)

    def revoke_all(self) -> None:
        with self._lock:
            self._items.clear()


class LoginLimiter:
    """Простое ограничение попыток входа по IP/ключу."""

    def __init__(self, max_attempts: int = LOGIN_MAX_ATTEMPTS,
                 window: int = LOGIN_WINDOW_SECONDS) -> None:
        self.max_attempts = max(1, int(max_attempts))
        self.window = max(1, int(window))
        self._failures: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> List[float]:
        items = [stamp for stamp in self._failures.get(key, []) if now - stamp < self.window]
        if items:
            self._failures[key] = items
        else:
            self._failures.pop(key, None)
        return items

    def retry_after(self, key: str) -> int:
        now = time.monotonic()
        with self._lock:
            items = self._recent(key, now)
            if len(items) < self.max_attempts:
                return 0
            return max(1, int(self.window - (now - items[0])) + 1)

    def fail(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            items = self._recent(key, now)
            items.append(now)
            self._failures[key] = items

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


class AuthManager:
    """Загружает auth.json, проверяет пароль и хранит веб-сессии."""

    def __init__(self, path: Optional[Path] = None, username: str = DEFAULT_USERNAME) -> None:
        self.path = Path(path) if path is not None else default_auth_path()
        self.username = username
        self.sessions = SessionStore()
        self.limiter = LoginLimiter()
        self._lock = threading.RLock()
        self._record: Optional[Dict[str, Any]] = None
        self._error = ""
        self._fingerprint: Any = None
        self.reload()

    def _signature(self) -> Any:
        """Отпечаток файла и каталога, чтобы замечать изменения на ходу.

        Сравнения содержимого здесь нет: хеш файла читается только после
        изменения mtime/размера/inode/прав. Это позволяет ``--set-password``
        и восстановлению из резервной копии применяться без перезапуска
        сервера, не читая auth.json на каждый запрос.
        """

        def info(path: Path) -> Any:
            try:
                item = path.stat()
            except FileNotFoundError:
                return None
            except OSError as exc:
                return ("error", type(exc).__name__)
            return (
                getattr(item, "st_mtime_ns", 0),
                getattr(item, "st_ctime_ns", 0),
                item.st_size,
                getattr(item, "st_ino", 0),
                stat.S_IMODE(item.st_mode),
                getattr(item, "st_uid", -1),
            )

        try:
            file_link = self.path.is_symlink()
        except OSError:
            file_link = False
        try:
            parent_link = self.path.parent.is_symlink()
        except OSError:
            parent_link = False
        return file_link, info(self.path), parent_link, info(self.path.parent)

    def _reload_locked(self) -> bool:
        signature = self._signature()
        previous = self._record
        try:
            record = load_record(self.path)
            error = ""
        except AuthError as exc:
            record = None
            error = str(exc)
        self._record = record
        self._error = error
        self._fingerprint = signature
        if record is not None:
            self.username = str(record.get("username") or self.username)
        if previous != record:
            # Пароль/файл изменился или исчез: старые cookie больше недействительны.
            self.sessions.revoke_all()
        return record is not None

    def reload(self) -> bool:
        with self._lock:
            return self._reload_locked()

    def _refresh_if_changed(self) -> None:
        with self._lock:
            if self._signature() != self._fingerprint:
                self._reload_locked()

    @property
    def configured(self) -> bool:
        self._refresh_if_changed()
        return self._record is not None

    @property
    def error(self) -> str:
        self._refresh_if_changed()
        return self._error

    def verify(self, username: str, password: str) -> bool:
        self._refresh_if_changed()
        with self._lock:
            record = dict(self._record) if self._record else None
        if not record:
            return False
        user_ok = hmac.compare_digest(str(username or ""), str(record.get("username", "")))
        # Хеш считаем даже при неверном имени: не раскрываем, что именно не так.
        password_ok = verify_password(record, str(password or ""))
        return bool(user_ok and password_ok)

    def authenticate(self, token: Optional[str]) -> bool:
        self._refresh_if_changed()
        return self.sessions.validate(token)

    def set_password(self, password: str, username: Optional[str] = None) -> None:
        record = make_record(username or self.username, password)
        save_record(self.path, record)
        with self._lock:
            self._record = record
            self._error = ""
            self.username = str(record["username"])
            self._fingerprint = self._signature()
            self.sessions.revoke_all()

    def status(self) -> Dict[str, Any]:
        return {
            "configured": self.configured,
            "username": self.username if self.configured else "",
            "path": str(self.path),
            "error": self.error,
        }


# --------------------------------------------------------------------------- #
#  Терминальный вход/первичная настройка
# --------------------------------------------------------------------------- #

def tty_available() -> bool:
    """Есть ли управляющий терминал (важно для curl | sh и systemd)."""
    if os.name == "posix":
        try:
            fd = os.open("/dev/tty", os.O_RDWR)
        except OSError:
            return False
        else:
            os.close(fd)
            return True
    try:
        return bool(sys.stdin and sys.stdin.isatty())
    except (AttributeError, ValueError):
        return False


def _read_secret(prompt: str) -> Optional[str]:
    try:
        return getpass.getpass(prompt)
    except (EOFError, KeyboardInterrupt):
        return None


def setup_interactive(manager: AuthManager) -> bool:
    """Первичная настройка: два ввода пароля и запись файла."""
    print(t("auth.setupTitle"))
    print(t("auth.setupAccount", user=manager.username))
    for _ in range(3):
        first = _read_secret(t("auth.passwordNew"))
        if first is None:
            return False
        try:
            validate_password(first)
        except AuthError as exc:
            print(str(exc))
            continue
        second = _read_secret(t("auth.passwordRepeat"))
        if second is None:
            return False
        if first != second:
            print(t("auth.passwordMismatch"))
            continue
        try:
            manager.set_password(first)
        except AuthError as exc:
            print(str(exc))
            return False
        print(t("auth.saved", path=manager.path))
        return True
    print(t("auth.setupFailed"))
    return False


def login_interactive(manager: AuthManager) -> bool:
    """Просит пароль администратора до трёх раз."""
    if not manager.configured:
        return False
    for _ in range(3):
        password = _read_secret(t("auth.passwordPrompt", user=manager.username))
        if password is None:
            return False
        if manager.verify(manager.username, password):
            return True
        print(t("auth.badPassword"))
    return False


def ensure_login(manager: AuthManager, no_prompt: bool = False) -> bool:
    """Готовит доступ перед запуском сервера.

    * нет файла + есть TTY — первичная настройка;
    * нет файла + нет TTY — отказ (нельзя молча запускать незащищённый сервер);
    * файл есть + TTY — спрашиваем пароль;
    * файл есть + нет TTY / --no-login-prompt — пропускаем терминальный
      вопрос, но веб-вход всё равно обязателен.
    """
    no_prompt = no_prompt or os.environ.get("MEGATOOL_NO_LOGIN_PROMPT", "") == "1"
    if manager.error:
        print(t("auth.fileError", path=manager.path, error=manager.error))
        return False

    env_password = os.environ.get("MEGATOOL_ADMIN_PASSWORD", "")
    if not manager.configured and env_password:
        try:
            manager.set_password(env_password)
        except AuthError as exc:
            print(str(exc))
            return False
        print(t("auth.created", path=manager.path))
        return True

    if not manager.configured:
        if no_prompt or not tty_available():
            print(t("auth.notConfigured", path=manager.path))
            return False
        return setup_interactive(manager)

    if no_prompt or not tty_available():
        return True
    return login_interactive(manager)


def set_password_cli(manager: AuthManager) -> int:
    """Команда --set-password: смена пароля с проверкой текущего."""
    if manager.error:
        print(t("auth.fileError", path=manager.path, error=manager.error))
        return 1
    if manager.configured:
        if not tty_available():
            print(t("auth.changeNeedsTty"))
            return 1
        if not login_interactive(manager):
            return 1

    env_new = os.environ.get("MEGATOOL_NEW_PASSWORD", "")
    if env_new:
        try:
            manager.set_password(env_new)
        except AuthError as exc:
            print(str(exc))
            return 1
        print(t("auth.saved", path=manager.path))
        return 0

    if not tty_available():
        print(t("auth.changeNeedsTty"))
        return 1
    for _ in range(3):
        first = _read_secret(t("auth.passwordNew"))
        if first is None:
            return 1
        try:
            validate_password(first)
        except AuthError as exc:
            print(str(exc))
            continue
        second = _read_secret(t("auth.passwordRepeat"))
        if second is None:
            return 1
        if first != second:
            print(t("auth.passwordMismatch"))
            continue
        try:
            manager.set_password(first)
        except AuthError as exc:
            print(str(exc))
            return 1
        print(t("auth.saved", path=manager.path))
        return 0
    print(t("auth.setupFailed"))
    return 1


def status_text(manager: AuthManager) -> str:
    """Текст для --auth-status (без секретов)."""
    if manager.error:
        return t("auth.fileError", path=manager.path, error=manager.error)
    if not manager.configured:
        return t("auth.statusNotConfigured", path=manager.path)
    mode = ""
    try:
        mode = oct(manager.path.stat().st_mode & 0o777)
    except OSError:
        pass
    return t("auth.statusConfigured", user=manager.username, path=manager.path, mode=mode or "?")

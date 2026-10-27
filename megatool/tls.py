"""Встроенный TLS для удалённого доступа без обратного прокси.

MegaTool умеет запускать uvicorn с HTTPS напрямую. Для локальной сети
сертификат создаётся самоподписанным; администратор может импортировать его
как доверенный или указать собственные файлы (например, Let's Encrypt).
"""

from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import shutil
import ssl
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Tuple

from .i18n import t
from .security import is_loopback, local_hostnames

if TYPE_CHECKING:  # pragma: no cover - только для аннотации
    from .config import Config

CERT_NAME = "server.crt"
KEY_NAME = "server.key"
_DNS_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_WILDCARDS = {"0.0.0.0", "::"}


class TlsError(RuntimeError):
    """Ошибка конфигурации или генерации TLS."""


def tls_dir(home: Path) -> Path:
    return Path(home).expanduser().resolve() / "tls"


def _resolve_path(value: Any, default: Path, base: Path) -> Path:
    text = str(value or "").strip()
    if not text:
        return default
    path = Path(os.path.expandvars(text)).expanduser()
    return path if path.is_absolute() else base / path


def cert_paths_for(cfg: Config, section: Any) -> Tuple[Path, Path]:
    """Пути сертификата/ключа для конкретной секции server.tls."""
    base = Path(cfg.path).expanduser().resolve().parent
    default_dir = tls_dir(base)
    if not isinstance(section, dict):
        section = {}
    cert = _resolve_path(section.get("certfile"), default_dir / CERT_NAME, base)
    key = _resolve_path(section.get("keyfile"), default_dir / KEY_NAME, base)
    return cert, key


def cert_paths(cfg: Config) -> Tuple[Path, Path]:
    """Возвращает (сертификат, ключ), подставляя путь по умолчанию."""
    return cert_paths_for(cfg, cfg.get("server.tls", {}) or {})


def tls_enabled(cfg: Config) -> bool:
    section = cfg.get("server.tls", {}) or {}
    return bool(isinstance(section, dict) and section.get("enabled") is True)


def _check_private_dir(path: Path) -> None:
    if path.is_symlink():
        raise TlsError(t("tls.dirSymlink", path=path))
    if not path.is_dir():
        raise TlsError(t("tls.dirMissing", path=path))
    info = path.stat()
    if os.name == "posix":
        if info.st_uid != os.geteuid():
            raise TlsError(t("tls.dirOwner", path=path))
        mode = info.st_mode & 0o777
        if mode & 0o077:
            # Каталог принадлежит нам: исправляем права, а не отказываемся
            # запускаться после неаккуратного копирования.
            try:
                os.chmod(path, 0o700)
            except OSError as exc:
                raise TlsError(t("tls.dirPermissions", path=path, error=exc)) from exc


def _ensure_tls_dir(path: Path) -> None:
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise TlsError(t("tls.dirCreate", path=path, error=exc)) from exc
    _check_private_dir(path)


def _check_key_dir(path: Path) -> None:
    """Каталог ключа должен быть настоящим и приватным (0700)."""
    if path.is_symlink():
        raise TlsError(t("tls.keyDir", path=path))
    if not path.is_dir():
        raise TlsError(t("tls.keyDir", path=path))
    if os.name == "posix":
        try:
            mode = path.stat().st_mode & 0o777
        except OSError:
            mode = 0
        if mode & 0o077:
            raise TlsError(t("tls.keyDirPermissions", path=path))


def _check_key_file(path: Path) -> None:
    if path.is_symlink():
        raise TlsError(t("tls.keySymlink", path=path))
    if not path.is_file():
        raise TlsError(t("tls.keyMissing", path=path))
    parent = path.parent
    _check_key_dir(parent)
    if os.name == "posix":
        info = path.stat()
        if info.st_uid != os.geteuid():
            raise TlsError(t("tls.keyOwner", path=path))
        mode = info.st_mode & 0o777
        if mode & 0o077:
            raise TlsError(t("tls.keyPermissions", path=path, mode=oct(mode)))


def validate_files(certfile: Path, keyfile: Path) -> str:
    """Проверяет пару сертификат/ключ и возвращает SHA-256 отпечаток."""
    certfile = Path(certfile)
    keyfile = Path(keyfile)
    if certfile.is_symlink():
        raise TlsError(t("tls.certSymlink", path=certfile))
    if not certfile.is_file():
        raise TlsError(t("tls.certMissing", path=certfile))
    _check_key_file(keyfile)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    try:
        context.load_cert_chain(certfile=str(certfile), keyfile=str(keyfile))
    except (OSError, ssl.SSLError, ValueError) as exc:
        raise TlsError(t("tls.certInvalid", error=exc)) from exc
    return fingerprint(certfile)


def fingerprint(certfile: Path) -> str:
    try:
        pem = Path(certfile).read_text(encoding="ascii")
        der = ssl.PEM_cert_to_DER_cert(pem)
    except (OSError, ValueError, UnicodeError) as exc:
        raise TlsError(t("tls.certRead", error=exc)) from exc
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))


def _not_after(certfile: Path) -> str:
    binary = shutil.which("openssl")
    if not binary:
        return ""
    try:
        result = subprocess.run(
            [binary, "x509", "-in", str(certfile), "-noout", "-enddate"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    text = result.stdout.strip()
    return text.split("=", 1)[1].strip() if "=" in text else text


def _valid_san_name(value: str) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    if not text:
        return ""
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        pass
    candidate = text.rstrip(".")
    if len(candidate) > 253:
        return ""
    if any(not _DNS_LABEL.fullmatch(part) for part in candidate.split(".")):
        return ""
    return candidate


def certificate_names(cfg: Config) -> List[str]:
    """SAN: loopback, имя машины, локальные IP, привязка и allowed_hosts."""
    section = cfg.get("server", {}) or {}
    values: List[str] = ["localhost", "127.0.0.1", "::1"]
    values.extend(sorted(local_hostnames()))
    host = str(section.get("host", "") or "").strip()
    if host and host not in _WILDCARDS:
        values.append(host)
    allowed = section.get("allowed_hosts", []) or []
    if isinstance(allowed, list):
        values.extend(str(item) for item in allowed)
    seen = set()
    result: List[str] = []
    for value in values:
        name = _valid_san_name(value)
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(name)
    if not any(_valid_san_name(name) for name in result):
        result = ["localhost"]
    return result


def _san_text(names: Iterable[str]) -> str:
    entries = []
    for name in names:
        try:
            ip = ipaddress.ip_address(name)
        except ValueError:
            entries.append(f"DNS:{name}")
        else:
            entries.append(f"IP:{ip}")
    return ",".join(entries)


def describe(cfg: Config) -> Dict[str, Any]:
    cert, key = cert_paths(cfg)
    enabled = tls_enabled(cfg)
    info: Dict[str, Any] = {
        "enabled": enabled,
        "certfile": str(cert),
        "keyfile": str(key),
        "exists": cert.is_file() and key.is_file(),
        "fingerprint": "",
        "not_after": "",
        "error": "",
    }
    if not info["exists"]:
        if enabled:
            info["error"] = (
                t("tls.certMissing", path=cert) if not cert.is_file()
                else t("tls.keyMissing", path=key)
            )
        return info
    try:
        info["fingerprint"] = validate_files(cert, key)
        info["not_after"] = _not_after(cert)
    except TlsError as exc:
        info["error"] = str(exc)
    return info


def external_bind_message(host: str, enabled: bool, allow_insecure: bool) -> str:
    """Пустая строка, если внешний bind разрешён; иначе понятная ошибка."""
    if is_loopback(host) or enabled or allow_insecure:
        return ""
    return t("tls.externalRequired")


def _openssl() -> str:
    binary = os.environ.get("MEGATOOL_OPENSSL", "").strip() or shutil.which("openssl")
    if not binary:
        raise TlsError(t("tls.opensslMissing"))
    return binary


def generate_self_signed(cfg: Config, force: bool = False) -> Dict[str, Any]:
    """Создаёт самоподписанный сертификат для текущего host/allowed_hosts."""
    cert, key = cert_paths(cfg)
    if cert.exists() and key.exists() and not force:
        validate_files(cert, key)
        return describe(cfg)
    if (cert.exists() or key.exists()) and not force:
        raise TlsError(t("tls.partial", cert=cert, key=key))

    binary = _openssl()
    default_dir = tls_dir(Path(cfg.path).expanduser().resolve().parent)
    if cert.parent == default_dir:
        _ensure_tls_dir(default_dir)
    else:
        if cert.parent.is_symlink():
            raise TlsError(t("tls.dirSymlink", path=cert.parent))
        if not cert.parent.is_dir():
            raise TlsError(t("tls.dirMissing", path=cert.parent))
    if key.parent == default_dir:
        _ensure_tls_dir(default_dir)
    else:
        _check_key_dir(key.parent)

    try:
        temp = Path(tempfile.mkdtemp(prefix=".tls-gen-", dir=str(cert.parent)))
    except OSError as exc:
        raise TlsError(t("tls.generateFailed", error=exc)) from exc
    try:
        try:
            os.chmod(temp, 0o700)
            tmp_key = temp / KEY_NAME
            tmp_cert = temp / CERT_NAME
            names = certificate_names(cfg)
            san = _san_text(names)
            command = [
                binary, "req", "-x509", "-nodes", "-newkey", "rsa:3072",
                "-sha256", "-days", "825",
                "-keyout", str(tmp_key), "-out", str(tmp_cert),
                "-subj", "/O=MegaTool/CN=MegaTool",
                "-addext", f"subjectAltName={san}",
                "-addext", "basicConstraints=critical,CA:TRUE",
                "-addext", "keyUsage=critical,digitalSignature,keyEncipherment,keyCertSign",
                "-addext", "extendedKeyUsage=serverAuth",
            ]
            try:
                result = subprocess.run(
                    command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, timeout=45, env={**os.environ, "LC_ALL": "C"},
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise TlsError(t("tls.generateFailed", error=exc)) from exc
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "").strip()[-800:]
                raise TlsError(t("tls.generateFailed", error=detail or f"openssl {result.returncode}"))
            os.chmod(tmp_key, 0o600)
            os.chmod(tmp_cert, 0o644)
            validate_files(tmp_cert, tmp_key)
            os.replace(tmp_key, key)
            os.replace(tmp_cert, cert)
            validate_files(cert, key)
        except TlsError:
            raise
        except OSError as exc:
            raise TlsError(t("tls.generateFailed", error=exc)) from exc
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    return describe(cfg)

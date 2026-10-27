"""Точка входа: `python -m megatool` или консольная команда `megatool`."""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import webbrowser
from typing import List, Optional, TYPE_CHECKING

from . import APP_NAME, __version__
from .i18n import set_language, t

if TYPE_CHECKING:  # pragma: no cover - только для аннотации
    from .config import Config


def _url_host(host: str) -> str:
    if ":" in host and not host.startswith("[") and host != "::":
        return f"[{host}]"
    return host


def _url(scheme: str, host: str, port: int) -> str:
    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    return f"{scheme}://{_url_host(shown)}:{port}/"


def _lan_urls(scheme: str, host: str, port: int) -> List[str]:
    from .security import is_loopback, local_addresses

    if host not in ("0.0.0.0", "::"):
        return [_url(scheme, host, port)]
    addresses = [item for item in local_addresses() if not is_loopback(item)]
    addresses.sort(key=lambda item: (":" in item, item))
    return [_url(scheme, item, port) for item in addresses]


def _banner(host: str, port: int, cfg: Config, tls_enabled: bool = False,
            tls_info: Optional[dict] = None, allow_insecure: bool = False) -> None:
    from .security import is_loopback

    scheme = "https" if tls_enabled else "http"
    url = _url(scheme, host, port)
    mode = t("cli.modeExternal") if not is_loopback(host) else t("cli.modeLocal")
    print("=" * 66)
    print(f"  {t('cli.tagline', app=APP_NAME, version=__version__)}")
    print("=" * 66)
    print(f"  {t('cli.interface', url=url)}")
    print(f"  {t('cli.apiDocs', url=url)}api/docs")
    print(f"  {t('cli.mode', mode=mode, host=host)}")
    if tls_enabled:
        info = tls_info or {}
        print(f"  {t('cli.tlsMode')}")
        if info.get("certfile"):
            print(f"  {t('cli.tlsCert', path=info['certfile'])}")
        if info.get("fingerprint"):
            print(f"  {t('cli.tlsFingerprint', fingerprint=info['fingerprint'])}")
        print(f"  {t('cli.tlsTrust')}")
        if not is_loopback(host):
            urls = _lan_urls(scheme, host, port)
            if urls:
                print(f"  {t('cli.lanUrls', urls=', '.join(urls))}")
    print(f"  {t('cli.settings', path=cfg.path)}")
    print(f"  {t('cli.stopHint', keys='Ctrl+C')}")
    if not is_loopback(host) and not tls_enabled:
        print(f"  {t('cli.networkWarning')}")
        if allow_insecure:
            print(f"  {t('cli.insecureRemote')}")
    if not is_loopback(host):
        print(f"  {t('cli.firewallHint', port=port)}")
    print("=" * 66)


def _ask(prompt_key: str, **params) -> str:
    prompt = t(prompt_key, **params)
    if not prompt.endswith((" ", "\t")):
        prompt += " "
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def _yes(value: str, default: bool = False) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return default
    return text in ("y", "yes", "д", "да", "1")


def _ask_port(current: int) -> Optional[int]:
    raw = _ask("cli.setupPort", port=current)
    if not raw:
        return current
    try:
        port = int(raw)
    except ValueError:
        print(t("cli.setupPortInvalid"))
        return None
    if not 1 <= port <= 65535:
        print(t("cli.setupPortInvalid"))
        return None
    return port


def _setup_wizard(cfg: Config) -> int:
    """Настраивает режим доступа: локальный HTTP или сетевой HTTPS."""
    from . import tls as tls_support

    print()
    print("=" * 66)
    print(f"  {t('cli.setupTitle', app=APP_NAME)}")
    print("=" * 66)
    current_host = cfg.get("server.host", "127.0.0.1")
    current_port = cfg.get("server.port", 8787)
    print(f"  {t('cli.setupCurrent', host=current_host, port=current_port)}")
    print(f"  {t('cli.setupMode')}")
    print(f"  {t('cli.setupLocal')}")
    print(f"  {t('cli.setupNetwork')}")
    choice = _ask("cli.setupChoice") or "1"
    if choice not in ("1", "2"):
        print(t("cli.setupCancelled"))
        return 1

    try:
        current_port = int(cfg.get("server.port", 8787) or 8787)
    except (TypeError, ValueError):
        current_port = 8787
    port = _ask_port(current_port)
    if port is None:
        return 1

    if choice == "1":
        cfg.set("server.host", "127.0.0.1")
        cfg.set("server.port", port)
        cfg.set("server.tls.enabled", False)
        cfg.set("server.allow_insecure_remote", False)
        cfg.save()
        print()
        print(t("cli.setupSaved"))
        print(f"  {t('cli.interface', url=_url('http', '127.0.0.1', port))}")
        print(f"  {t('cli.setupStart')}")
        return 0

    host = "0.0.0.0"
    # Генерируем SAN по новой конфигурации: адрес ещё не сохранён.
    cfg.set("server.host", host)
    cfg.set("server.port", port)
    cfg.set("server.allow_insecure_remote", False)
    cert, key = tls_support.cert_paths(cfg)
    regenerate = True
    if cert.exists() or key.exists():
        print()
        print(t("cli.setupCertExisting", cert=cert, key=key))
        regenerate = _yes(_ask("cli.setupRegenerate"), default=False)
    if regenerate:
        print(t("cli.setupGenerating"))
        try:
            tls_support.generate_self_signed(cfg, force=True)
        except tls_support.TlsError as exc:
            print(t("cli.setupTlsFailed", error=exc))
            return 1
    cfg.set("server.tls.enabled", True)
    try:
        tls_support.validate_files(*tls_support.cert_paths(cfg))
    except tls_support.TlsError as exc:
        print(t("cli.setupTlsFailed", error=exc))
        return 1
    cfg.save()
    info = tls_support.describe(cfg)
    print()
    print(t("cli.setupSaved"))
    print(f"  {t('cli.interface', url=_url('https', host, port))}")
    for url in _lan_urls("https", host, port):
        print(f"  {t('cli.setupUrl', url=url)}")
    print(f"  {t('cli.tlsCert', path=info.get('certfile', cert))}")
    print(f"  {t('cli.tlsFingerprint', fingerprint=info.get('fingerprint', '?'))}")
    print(f"  {t('cli.tlsTrust')}")
    print(f"  {t('cli.firewallHint', port=port)}")
    print(f"  {t('cli.setupStart')}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="megatool",
        description=f"{APP_NAME} — открытая консоль системного администратора и DevOps",
    )
    parser.add_argument("--host", default="", help="адрес прослушивания (по умолчанию из настроек)")
    parser.add_argument("--port", type=int, default=0, help="порт (по умолчанию из настроек)")
    parser.add_argument("--no-browser", action="store_true", help="не открывать браузер")
    parser.add_argument("--home", default="", help="рабочий каталог (MEGATOOL_HOME)")
    parser.add_argument("--config", default="", help="путь к config.json")
    parser.add_argument("--log-level", default="info",
                        choices=["critical", "error", "warning", "info", "debug", "trace"])
    parser.add_argument("--reload", action="store_true", help="перезапуск при изменении кода (разработка)")
    parser.add_argument("--setup", action="store_true",
                        help="интерактивно настроить доступ: локально или сеть с HTTPS")
    parser.add_argument("--tls-cert", default="", help="путь к PEM-сертификату для встроенного HTTPS")
    parser.add_argument("--tls-key", default="", help="путь к приватному ключу TLS")
    parser.add_argument("--allow-insecure-remote", action="store_true",
                        help="явно разрешить внешний bind без TLS (не рекомендуется)")
    parser.add_argument("--set-password", action="store_true",
                        help="задать или сменить пароль администратора и выйти")
    parser.add_argument("--auth-status", action="store_true",
                        help="показать состояние файла учётных данных и выйти")
    parser.add_argument("--no-login-prompt", action="store_true",
                        help="не спрашивать пароль в терминале (для systemd/автоматизации)")
    parser.add_argument("--auth-file", default="", help="путь к auth.json (MEGATOOL_AUTH_FILE)")
    parser.add_argument("--version", action="store_true", help="показать версию и выйти")
    args = parser.parse_args(argv)

    if args.version:
        print(f"{APP_NAME} {__version__}")
        return 0

    if sys.platform != "linux":
        print("MegaTool поддерживает только Linux.")
        return 1

    if args.home:
        os.environ["MEGATOOL_HOME"] = args.home
    if args.auth_file:
        os.environ["MEGATOOL_AUTH_FILE"] = args.auth_file

    # Импортируем config только после установки переменной окружения:
    # HOME_DIR вычисляется при импорте модуля.
    from pathlib import Path

    from .config import Config

    cfg = Config(Path(args.config).expanduser()) if args.config else Config()
    set_language(cfg.get("ui.language", "ru"))

    from . import tls as tls_support
    from .auth import AuthManager, default_auth_path, ensure_login, set_password_cli, status_text

    auth_manager = AuthManager(default_auth_path(cfg.path))
    if args.auth_status:
        print(status_text(auth_manager))
        return 0 if not auth_manager.error else 1
    if args.set_password:
        return set_password_cli(auth_manager)

    if args.setup:
        if auth_manager.error:
            print(t("auth.fileError", path=auth_manager.path, error=auth_manager.error))
            return 1
        if not auth_manager.configured:
            if not ensure_login(auth_manager, no_prompt=args.no_login_prompt):
                return 1
            os.environ.pop("MEGATOOL_ADMIN_PASSWORD", None)
            os.environ.pop("MEGATOOL_NEW_PASSWORD", None)
        return _setup_wizard(cfg)

    if not ensure_login(auth_manager, no_prompt=args.no_login_prompt):
        return 1
    # Пароли уже превращены в хеши; не оставляем их в окружении сервера и --reload.
    os.environ.pop("MEGATOOL_ADMIN_PASSWORD", None)
    os.environ.pop("MEGATOOL_NEW_PASSWORD", None)
    print(t("cli.auth", user=auth_manager.username, path=auth_manager.path))

    if args.tls_cert or args.tls_key:
        if not args.tls_cert or not args.tls_key:
            print(t("cli.tlsPairRequired"))
            return 2
        cfg.set("server.tls.certfile", args.tls_cert)
        cfg.set("server.tls.keyfile", args.tls_key)
        cfg.set("server.tls.enabled", True)
    host = args.host or str(cfg.get("server.host", "127.0.0.1"))
    port = args.port or int(cfg.get("server.port", 8787) or 8787)
    if args.host:
        cfg.set("server.host", host)
    if args.port:
        cfg.set("server.port", port)

    tls_enabled = tls_support.tls_enabled(cfg)
    env_allow = os.environ.get("MEGATOOL_ALLOW_INSECURE_REMOTE", "").strip().lower()
    allow_insecure = bool(
        args.allow_insecure_remote
        or env_allow in ("1", "true", "yes", "on")
        or cfg.get("server.allow_insecure_remote", False) is True
    )
    tls_info = {}
    if tls_enabled:
        try:
            cert, key = tls_support.cert_paths(cfg)
            tls_support.validate_files(cert, key)
            tls_info = tls_support.describe(cfg)
        except tls_support.TlsError as exc:
            print(t("cli.tlsFailed", error=exc))
            return 1
    bind_error = tls_support.external_bind_message(host, tls_enabled, allow_insecure)
    if bind_error:
        print(t("cli.tlsRequired"))
        print(bind_error)
        print(t("cli.setupHint"))
        return 1
    if args.host or args.port or args.tls_cert or args.tls_key:
        cfg.save()

    _banner(host, port, cfg, tls_enabled, tls_info, allow_insecure)

    if cfg.get("server.open_browser", True) and not args.no_browser:
        url = _url("https" if tls_enabled else "http", host, port)

        def _open() -> None:
            time.sleep(1.2)
            try:
                webbrowser.open(url)
            except Exception:
                pass

        threading.Thread(target=_open, daemon=True, name="megatool-browser").start()

    import uvicorn

    ssl_args = {}
    if tls_enabled:
        cert, key = tls_support.cert_paths(cfg)
        ssl_args = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}

    if args.reload:
        os.environ.setdefault("MEGATOOL_HOME", str(cfg.path.parent))
        os.environ.setdefault("MEGATOOL_AUTH_FILE", str(auth_manager.path))
        uvicorn.run("megatool.server:app", host=host, port=port, log_level=args.log_level,
                    reload=True, **ssl_args)
        return 0

    from .server import create_app

    app = create_app(cfg, auth=auth_manager)
    try:
        uvicorn.run(app, host=host, port=port, log_level=args.log_level,
                    access_log=False, **ssl_args)
    except KeyboardInterrupt:  # pragma: no cover
        print("\n" + t("cli.stoppedByUser"))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

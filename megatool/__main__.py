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


def _banner(host: str, port: int, cfg: Config) -> None:
    from .security import is_loopback

    shown_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{shown_host}:{port}/"
    mode = t("cli.modeExternal") if not is_loopback(host) else t("cli.modeLocal")
    print("=" * 66)
    print(f"  {t('cli.tagline', app=APP_NAME, version=__version__)}")
    print("=" * 66)
    print(f"  {t('cli.interface', url=url)}")
    print(f"  {t('cli.apiDocs', url=url)}api/docs")
    print(f"  {t('cli.mode', mode=mode, host=host)}")
    print(f"  {t('cli.settings', path=cfg.path)}")
    print(f"  {t('cli.stopHint', keys='Ctrl+C')}")
    if not is_loopback(host):
        print(f"  {t('cli.networkWarning')}")
    print("=" * 66)


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
    from .config import Config

    # Настройки читаем, чтобы значения по умолчанию жили в config.json,
    # а не только в аргументах командной строки.
    from pathlib import Path

    cfg = Config(Path(args.config).expanduser()) if args.config else Config()
    set_language(cfg.get("ui.language", "ru"))

    from .auth import AuthManager, default_auth_path, ensure_login, set_password_cli, status_text

    auth_manager = AuthManager(default_auth_path(cfg.path))
    if args.auth_status:
        print(status_text(auth_manager))
        return 0 if not auth_manager.error else 1
    if args.set_password:
        return set_password_cli(auth_manager)

    if not ensure_login(auth_manager, no_prompt=args.no_login_prompt):
        return 1
    # Пароль уже превращён в хеш; не оставляем его в окружении сервера и --reload.
    os.environ.pop("MEGATOOL_ADMIN_PASSWORD", None)
    print(t("cli.auth", user=auth_manager.username, path=auth_manager.path))

    host = args.host or str(cfg.get("server.host", "127.0.0.1"))
    port = args.port or int(cfg.get("server.port", 8787) or 8787)
    if args.host:
        cfg.set("server.host", host)
    if args.port:
        cfg.set("server.port", port)
    if args.host or args.port:
        cfg.save()

    _banner(host, port, cfg)

    if cfg.get("server.open_browser", True) and not args.no_browser:
        url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}/"

        def _open() -> None:
            time.sleep(1.2)
            try:
                webbrowser.open(url)
            except Exception:
                pass

        threading.Thread(target=_open, daemon=True, name="megatool-browser").start()

    import uvicorn

    if args.reload:
        os.environ.setdefault("MEGATOOL_HOME", str(cfg.path.parent))
        os.environ.setdefault("MEGATOOL_AUTH_FILE", str(auth_manager.path))
        uvicorn.run("megatool.server:app", host=host, port=port, log_level=args.log_level,
                    reload=True)
        return 0

    from .server import create_app

    app = create_app(cfg, auth=auth_manager)
    try:
        uvicorn.run(app, host=host, port=port, log_level=args.log_level, access_log=False)
    except KeyboardInterrupt:  # pragma: no cover
        print("\n" + t("cli.stoppedByUser"))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

"""Изоляция тестов: свой рабочий каталог MegaTool, чтобы не трогать реальный."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Рабочий каталог задаём ДО импорта megatool: config.py читает переменную при загрузке.
_TMP_HOME = Path(tempfile.mkdtemp(prefix="megatool-test-"))
os.environ["MEGATOOL_HOME"] = str(_TMP_HOME)
# Тестовый пароль и ускоренный PBKDF2: проверяем логику, а не скорость хеша.
os.environ.setdefault("MEGATOOL_AUTH_ITERATIONS", "10000")
TEST_AUTH_USER = "admin"
TEST_AUTH_PASSWORD = "test-password-123"

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def home() -> Path:
    return _TMP_HOME


@pytest.fixture(scope="session")
def auth_manager():
    from megatool.auth import AuthManager, default_auth_path

    manager = AuthManager(default_auth_path())
    manager.set_password(TEST_AUTH_PASSWORD)
    return manager


@pytest.fixture(scope="session")
def client(auth_manager):
    from fastapi.testclient import TestClient

    from megatool.server import create_app

    application = create_app(auth=auth_manager)
    # Явный loopback-адрес: защита от DNS-rebinding отклоняет чужие имена Host
    # (по умолчанию TestClient использует «testserver»).
    with TestClient(application, base_url="http://127.0.0.1:8799") as test_client:
        login = test_client.post("/api/auth/login", json={
            "username": TEST_AUTH_USER,
            "password": TEST_AUTH_PASSWORD,
        })
        assert login.status_code == 200, login.text
        yield test_client

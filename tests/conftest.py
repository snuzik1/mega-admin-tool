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

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def home() -> Path:
    return _TMP_HOME


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from megatool.server import create_app

    application = create_app()
    # Явный loopback-адрес: защита от DNS-rebinding отклоняет чужие имена Host
    # (по умолчанию TestClient использует «testserver»).
    with TestClient(application, base_url="http://127.0.0.1:8799") as test_client:
        yield test_client

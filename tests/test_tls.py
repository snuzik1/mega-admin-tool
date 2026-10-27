"""Встроенный TLS: генерация, проверка файлов и политика внешнего bind."""

from __future__ import annotations

import os
import shutil
import ssl
import stat
import subprocess
from pathlib import Path

import pytest

from megatool import tls
from megatool.config import Config

pytestmark = pytest.mark.skipif(
    not shutil.which("openssl") or os.name != "posix",
    reason="для теста нужен openssl и POSIX-права",
)


def _config(tmp_path: Path) -> Config:
    cfg = Config(tmp_path / "config.json")
    cfg.set("server.host", "0.0.0.0")
    cfg.set("server.allowed_hosts", ["megatool.local"])
    cfg.set("server.tls.enabled", True)
    return cfg


def test_generate_self_signed_certificate(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    info = tls.generate_self_signed(cfg, force=True)

    cert = Path(info["certfile"])
    key = Path(info["keyfile"])
    assert cert.is_file()
    assert key.is_file()
    assert info["exists"] is True
    assert info["fingerprint"].count(":") == 31
    assert info["not_after"]

    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    assert stat.S_IMODE(cert.stat().st_mode) == 0o644
    assert stat.S_IMODE(cert.parent.stat().st_mode) == 0o700

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(cert), keyfile=str(key))

    details = subprocess.run(
        ["openssl", "x509", "-in", str(cert), "-noout", "-ext", "subjectAltName"],
        check=True, stdout=subprocess.PIPE, text=True,
    ).stdout
    assert "DNS:localhost" in details
    assert "IP Address:127.0.0.1" in details
    assert "DNS:megatool.local" in details


def test_existing_certificate_is_not_overwritten_without_force(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    first = tls.generate_self_signed(cfg, force=True)
    before = Path(first["certfile"]).read_text(encoding="ascii")
    second = tls.generate_self_signed(cfg)
    assert second["fingerprint"] == first["fingerprint"]
    assert Path(first["certfile"]).read_text(encoding="ascii") == before


def test_private_key_permissions_are_enforced(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    info = tls.generate_self_signed(cfg, force=True)
    key = Path(info["keyfile"])
    os.chmod(key, 0o644)
    with pytest.raises(tls.TlsError, match="0600"):
        tls.validate_files(Path(info["certfile"]), key)


def test_missing_key_is_an_error(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    info = tls.generate_self_signed(cfg, force=True)
    Path(info["keyfile"]).unlink()
    with pytest.raises(tls.TlsError):
        tls.validate_files(Path(info["certfile"]), Path(info["keyfile"]))


def test_private_key_directory_must_be_private(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    info = tls.generate_self_signed(cfg, force=True)
    key_dir = Path(info["keyfile"]).parent
    os.chmod(key_dir, 0o755)
    with pytest.raises(tls.TlsError, match="0700"):
        tls.validate_files(Path(info["certfile"]), Path(info["keyfile"]))


def test_external_bind_policy() -> None:
    assert tls.external_bind_message("0.0.0.0", False, False)
    assert not tls.external_bind_message("0.0.0.0", True, False)
    assert not tls.external_bind_message("0.0.0.0", False, True)
    assert not tls.external_bind_message("127.0.0.1", False, False)
    assert not tls.external_bind_message("::1", False, False)


def test_openssl_is_required(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _config(tmp_path)
    monkeypatch.setattr(tls.shutil, "which", lambda _name: None)
    monkeypatch.delenv("MEGATOOL_OPENSSL", raising=False)
    with pytest.raises(tls.TlsError, match="openssl"):
        tls.generate_self_signed(cfg, force=True)

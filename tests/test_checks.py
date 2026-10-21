"""Проверки доступности: HTTP, TCP и защита от некорректной конфигурации."""

from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - сигнатура http.server
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "ftp://127.0.0.1/secret")
            self.end_headers()
            return
        body = b"mega-ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


@pytest.fixture()
def http_target():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/health"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def test_checks_validation(client):
    try:
        bad_list = client.put("/api/checks", json={"checks": "nope"}).json()
        assert bad_list["ok"] is False

        bad_url = client.put("/api/checks", json={"checks": [{
            "type": "http", "url": "ftp://example.com", "name": "ftp",
        }]}).json()
        assert bad_url["ok"] is False

        credentials = client.put("/api/checks", json={"checks": [{
            "type": "http", "url": "http://user:pass@127.0.0.1/", "name": "creds",
        }]}).json()
        assert credentials["ok"] is False

        too_many = client.put("/api/checks", json={"checks": [
            {"type": "tcp", "host": "127.0.0.1", "port": 80, "name": str(index)}
            for index in range(51)
        ]}).json()
        assert too_many["ok"] is False
    finally:
        client.put("/api/checks", json={"checks": []})


def test_http_and_tcp_checks(client, http_target):
    port = _free_port()
    try:
        saved = client.put("/api/checks", json={"checks": [
            {
                "type": "http", "url": http_target, "name": "Local HTTP",
                "expected_status": 200, "keyword": "mega-ok", "interval_sec": 60,
            },
            {
                "type": "tcp", "host": "127.0.0.1", "port": port,
                "name": "Closed TCP", "interval_sec": 60,
            },
            {
                "type": "http", "url": http_target.replace("/health", "/redirect"),
                "name": "Bad redirect", "interval_sec": 60,
            },
        ]}).json()
        assert saved["ok"] is True, saved
        items = saved["data"]["items"]
        assert len(items) == 3
        assert all(item["state"] == "unknown" for item in items)

        by_name = {item["name"]: item for item in items}
        http_id = by_name["Local HTTP"]["id"]
        tcp_id = by_name["Closed TCP"]["id"]
        redirect_id = by_name["Bad redirect"]["id"]

        ran = client.post("/api/checks/run", json={"id": http_id}).json()
        assert ran["ok"] is True, ran
        http_item = next(item for item in ran["data"]["items"] if item["id"] == http_id)
        assert http_item["state"] == "up", http_item
        assert "HTTP 200" in http_item["detail"]
        assert http_item["history"]

        ran = client.post("/api/checks/run", json={"id": tcp_id}).json()
        assert ran["ok"] is True, ran
        tcp_item = next(item for item in ran["data"]["items"] if item["id"] == tcp_id)
        assert tcp_item["state"] == "down", tcp_item
        assert tcp_item["detail"]

        ran = client.post("/api/checks/run", json={"id": redirect_id}).json()
        assert ran["ok"] is True, ran
        redirect_item = next(item for item in ran["data"]["items"] if item["id"] == redirect_id)
        assert redirect_item["state"] == "down", redirect_item
        assert "схем" in redirect_item["detail"].lower() or "scheme" in redirect_item["detail"].lower()

        summary = ran["data"]["summary"]
        assert summary["up"] == 1
        assert summary["down"] == 2

        state = client.get("/api/state").json()["data"]
        assert state["checks"]["down"] == 2

        # Провайдер предупреждений отдаёт недоступную проверку коллектору.
        alerts = client.app.state.rt.checks.alerts()
        assert any("Closed TCP" in alert["title"] for alert in alerts)

        missing = client.post("/api/checks/run", json={"id": "nope"}).json()
        assert missing["ok"] is False
    finally:
        client.put("/api/checks", json={"checks": []})
        rest = client.get("/api/checks").json()["data"]
        assert rest["items"] == []

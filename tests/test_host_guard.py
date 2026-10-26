"""Защита от DNS-rebinding: Host и Origin проверяются для API и WebSocket."""

from __future__ import annotations


def test_host_header_is_checked(client):
    """Чужое имя Host при прослушивании loopback отклоняется."""
    response = client.get("/api/health", headers={"host": "evil.example"})
    assert response.status_code == 421
    assert response.json()["ok"] is False


def test_loopback_host_is_allowed(client):
    """Свои адреса (127.0.0.1/localhost) проходят проверку."""
    response = client.get("/api/health", headers={"host": "localhost:8799"})
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_origin_is_checked_for_mutations(client):
    """Кросс-доменный Origin у изменяющего запроса отклоняется."""
    response = client.patch(
        "/api/settings",
        json={"ui": {}},
        headers={"origin": "http://evil.example"},
    )
    assert response.status_code == 403
    assert response.json()["ok"] is False


def test_same_origin_mutation_is_allowed(client):
    response = client.patch(
        "/api/settings",
        json={"ui": {}},
        headers={"origin": "http://127.0.0.1:8799"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_origin_port_must_match_host(client):
    """Другой порт того же хоста — уже другой origin (CSRF)."""
    response = client.patch(
        "/api/settings",
        json={"ui": {}},
        headers={"origin": "http://127.0.0.1:9999"},
    )
    assert response.status_code == 403
    assert response.json()["ok"] is False

"""TASK-310: GET /healthz — проверка живости, вне префикса /api/modbus."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.services import build_services
from app.modbus.config import Config


def _client() -> TestClient:
    return TestClient(create_app(services=build_services(Config()), run_engine=False))


def test_healthz_ok() -> None:
    with _client() as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthz_outside_api_prefix() -> None:
    with _client() as client:
        assert client.get("/api/modbus/healthz").status_code == 404

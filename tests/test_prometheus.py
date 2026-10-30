"""TASK-904: GET /metrics — экспозиция счётчиков в формате Prometheus."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.services import build_services
from app.modbus.config import Config


def test_prometheus_exposition_format() -> None:
    """HELP/TYPE для каждой метрики и значения из снимка счётчиков."""
    services = build_services(Config())
    services.metrics.request()
    services.metrics.request(error=True)
    with TestClient(create_app(services=services, run_engine=False)) as client:
        response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
    body = response.text
    for name in (
        "modbus_active_connections",
        "modbus_requests_total",
        "modbus_errors_total",
        "modbus_requests_per_second",
        "modbus_errors_per_second",
        "modbus_uptime_seconds",
    ):
        assert f"# HELP {name} " in body
        assert f"# TYPE {name} " in body
    assert "modbus_requests_total 2" in body
    assert "modbus_errors_total 1" in body
    assert "modbus_active_connections 0" in body

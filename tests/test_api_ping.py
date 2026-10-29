"""Проба соединения: REST `/api/modbus/ping` и `probe_slave` (TASK-702).

Серверный опрос slave через loopback: min/avg/max RTT и число успешных/
ошибочных попыток. Живой сервер — через `serve` (как в conftest), REST —
httpx ASGITransport (async) или TestClient (sync, без слушателя).
"""
from __future__ import annotations

from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.services import build_services
from app.modbus.config import Config
from app.modbus.ping import MAX_ATTEMPTS, probe_slave, probe_target
from app.modbus.server import serve


def _client(config: Config) -> TestClient:
    return TestClient(create_app(services=build_services(config), run_engine=False))


def test_probe_target_wildcard_maps_to_loopback() -> None:
    assert probe_target(replace(Config(), MODBUS_TCP_HOST="0.0.0.0"))[0] == "127.0.0.1"
    assert probe_target(replace(Config(), MODBUS_TCP_HOST="::"))[0] == "127.0.0.1"
    assert probe_target(replace(Config(), MODBUS_TCP_HOST="192.0.2.1"))[0] == "192.0.2.1"


async def test_probe_slave_validates_arguments(config: Config) -> None:
    for slave in (0, 11):
        with pytest.raises(ValueError):
            await probe_slave(config, slave, 1)
    for attempts in (0, MAX_ATTEMPTS + 1):
        with pytest.raises(ValueError):
            await probe_slave(config, 1, attempts)


def test_ping_rest_validates_input(config: Config) -> None:
    with _client(config) as client:
        for payload in ({"slave": 0}, {"slave": 11}, {"slave": 1, "count": 0}, {"slave": 1, "count": 11}):
            response = client.post("/api/modbus/ping", json=payload)
            assert response.status_code == 422, payload


def test_ping_rest_when_modbus_server_down(config: Config) -> None:
    """Слушатель недоступен → все попытки ошибки, RTT пуст (TASK-702)."""
    with _client(config) as client:
        response = client.post("/api/modbus/ping", json={"slave": 1, "count": 2})
    assert response.status_code == 200
    body = response.json()
    assert body["slave"] == 1
    assert body["attempts"] == 2
    assert body["ok"] == 0
    assert body["errors"] == 2
    assert body["rtt_min_ms"] is None
    assert body["rtt_avg_ms"] is None
    assert body["rtt_max_ms"] is None
    assert body["last_error"]


def test_ping_rest_rate_limited(config: Config) -> None:
    """Проба считается действием из UI: лимит WEB_MAX_WRITE_PER_MIN_PER_IP."""
    limited = replace(config, WEB_MAX_WRITE_PER_MIN_PER_IP=2)
    with _client(limited) as client:
        for _ in range(2):
            assert client.post("/api/modbus/ping", json={"slave": 1, "count": 1}).status_code == 200
        response = client.post("/api/modbus/ping", json={"slave": 1, "count": 1})
    assert response.status_code == 429


async def test_probe_slave_live_server(config: Config) -> None:
    """Живой сервер: чтения успешны, RTT согласован (min ≤ avg ≤ max)."""
    async with serve(config):
        result = await probe_slave(config, 1, 5)
    assert result.slave == 1
    assert result.attempts == 5
    assert result.ok == 5
    assert result.errors == 0
    assert result.last_error is None
    assert result.rtt_min_ms is not None
    assert result.rtt_avg_ms is not None
    assert result.rtt_max_ms is not None
    assert 0 < result.rtt_min_ms <= result.rtt_avg_ms <= result.rtt_max_ms


async def test_probe_slave10_reads_ir(config: Config) -> None:
    """Slave 10 опрашивается FC 4 (IR), ответ успешен (TASK-702)."""
    async with serve(config):
        result = await probe_slave(config, 10, 3)
    assert result.ok == 3
    assert result.errors == 0
    assert result.last_error is None


async def test_ping_rest_live_server(config: Config) -> None:
    """REST-проба поверх живого Modbus-слушателя: полный стек TCP."""
    app = create_app(services=build_services(config), run_engine=False)
    transport = httpx.ASGITransport(app=app)
    async with serve(config), httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/modbus/ping", json={"slave": 2, "count": 3})
    assert response.status_code == 200
    body = response.json()
    assert body["slave"] == 2
    assert body["attempts"] == 3
    assert body["ok"] == 3
    assert body["errors"] == 0
    assert body["last_error"] is None

"""Счётчики Modbus-сервера: ServerMetrics, хуки сервера, REST (TASK-703).

Unit-часть — на фейковых часах (события, окно скоростей, старение бакетов);
интеграционная — живой сервер + REST `/api/modbus/metrics` на тех же метриках,
что и слушатель (`serve(..., metrics=...)`).
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx
from pymodbus.client import AsyncModbusTcpClient

from app.api.app import create_app
from app.api.services import build_services
from app.modbus.config import Config
from app.modbus.metrics import RATE_WINDOW_S, ServerMetrics
from app.modbus.server import serve


class _FakeClock:
    """Управляемые часы для детерминированных проверок окна скоростей."""

    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


async def _wait_until(predicate: Callable[[], bool], timeout_s: float = 2.0) -> bool:
    """Дождаться асинхронного события сервера (колбэки соединения/запросы)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


def test_metrics_counts_connections_and_totals() -> None:
    clock = _FakeClock()
    metrics = ServerMetrics(clock=clock)
    metrics.connection_opened()
    metrics.connection_opened()
    metrics.connection_closed()
    metrics.request()
    metrics.request(error=True)
    snapshot = metrics.snapshot()
    assert snapshot.active_connections == 1
    assert snapshot.requests_total == 2
    assert snapshot.errors_total == 1
    assert snapshot.uptime_s == 0.0


def test_metrics_connection_closed_never_goes_negative() -> None:
    metrics = ServerMetrics(clock=_FakeClock())
    metrics.connection_closed()
    assert metrics.snapshot().active_connections == 0


def test_metrics_rates_window_and_stale_buckets() -> None:
    clock = _FakeClock()
    metrics = ServerMetrics(clock=clock)
    for _ in range(5):
        metrics.request()
    for _ in range(2):
        metrics.request(error=True)
    snapshot = metrics.snapshot()
    assert snapshot.req_per_s == 7 / RATE_WINDOW_S  # ошибочные запросы — тоже запросы
    assert snapshot.err_per_s == 2 / RATE_WINDOW_S
    clock.now += RATE_WINDOW_S + 1.0  # окно опустело, тоталы остались
    stale = metrics.snapshot()
    assert stale.req_per_s == 0.0
    assert stale.err_per_s == 0.0
    assert stale.requests_total == 7
    assert stale.errors_total == 2


def test_metrics_uptime_follows_clock() -> None:
    clock = _FakeClock()
    metrics = ServerMetrics(clock=clock)
    clock.now += 42.5
    assert metrics.snapshot().uptime_s == 42.5


async def test_server_tracks_connections_and_errors(config: Config) -> None:
    """Живой сервер: соединения и запросы в ServerMetrics (TASK-703)."""
    metrics = ServerMetrics()
    async with serve(config, metrics=metrics):
        client = AsyncModbusTcpClient("127.0.0.1", port=config.MODBUS_TCP_PORT)
        assert await client.connect()
        assert await _wait_until(lambda: metrics.snapshot().active_connections == 1)
        for _ in range(3):
            response = await client.read_holding_registers(1, count=1, device_id=1)
            assert not response.isError()
        # slave 10 не имеет HR → exception 0x02: ошибка учитывается
        bad = await client.read_holding_registers(1, count=1, device_id=10)
        assert bad.isError()
        client.close()
        assert await _wait_until(lambda: metrics.snapshot().active_connections == 0)
    snapshot = metrics.snapshot()
    assert snapshot.requests_total == 4
    assert snapshot.errors_total == 1


async def test_metrics_rest_live(config: Config) -> None:
    """REST /metrics отдаёт счётчики слушателя (общий объект метрик)."""
    services = build_services(config)
    app = create_app(services=services, run_engine=False)
    transport = httpx.ASGITransport(app=app)
    async with serve(config, metrics=services.metrics):
        client = AsyncModbusTcpClient("127.0.0.1", port=config.MODBUS_TCP_PORT)
        assert await client.connect()
        response = await client.read_holding_registers(1, count=1, device_id=2)
        assert not response.isError()
        assert await _wait_until(lambda: services.metrics.snapshot().requests_total == 1)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            payload = (await http.get("/api/modbus/metrics")).json()
        client.close()
    assert payload["active_connections"] == 1
    assert payload["requests_total"] == 1
    assert payload["errors_total"] == 0
    assert payload["uptime_s"] >= 0.0

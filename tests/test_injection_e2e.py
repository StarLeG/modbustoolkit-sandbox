"""TASK-807: сквозные тесты инъекций — REST → Modbus-провод → сброс → восстановление.

REST и Modbus-слушатель делят один `InjectionRegistry` (`build_services`),
поэтому инъекция из `POST /api/modbus/inject` обязана менять обмен эталонного
pymodbus-клиента на TCP. Покрыто: каждый код исключения (0x01/0x02/0x03/0x04/
0x06) через REST, автосброс по TTL, сброс по id и полный сброс, delay-инъекция
(замедление ответа) и восстановление после clear.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from pymodbus.client import AsyncModbusTcpClient

from app.api.app import create_app
from app.api.services import build_services
from app.modbus.config import Config
from app.modbus.injection import EXCEPTION_CODES
from app.modbus.server import serve


@asynccontextmanager
async def _stack(
    config: Config,
) -> AsyncIterator[tuple[httpx.AsyncClient, AsyncModbusTcpClient]]:
    """REST и Modbus-слушатель на одном реестре инъекций; клиент — эталонный."""
    services = build_services(config)
    app = create_app(services=services, run_engine=False)
    transport = httpx.ASGITransport(app=app)
    async with serve(config, injections=services.injections):
        tcp = AsyncModbusTcpClient("127.0.0.1", port=config.MODBUS_TCP_PORT)
        assert await tcp.connect()
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as web:
                yield web, tcp
        finally:
            tcp.close()


@pytest.mark.parametrize("code", EXCEPTION_CODES)
async def test_rest_exception_each_code_reaches_wire(config: Config, code: int) -> None:
    """Каждый код исключения из REST возвращается клиенту; clear возвращает обмен."""
    async with _stack(config) as (web, tcp):
        baseline = await tcp.read_holding_registers(1, count=1, device_id=1)
        assert not baseline.isError()
        response = await web.post(
            "/api/modbus/inject",
            json={"kind": "exception", "slave": 1, "code": code, "ttl_s": 60},
        )
        assert response.status_code == 200, response.text
        broken = await tcp.read_holding_registers(1, count=1, device_id=1)
        assert broken.isError()
        assert broken.exception_code == code  # type: ignore[attr-defined]
        cleared = await web.post("/api/modbus/inject/clear", json={})
        assert cleared.status_code == 200
        assert cleared.json()["cleared"] == 1
        restored = await tcp.read_holding_registers(1, count=1, device_id=1)
        assert not restored.isError()


async def test_rest_injection_ttl_autorestores(config: Config) -> None:
    """Инъекция с коротким TTL перестаёт действовать сама, без clear (TASK-604)."""
    async with _stack(config) as (web, tcp):
        response = await web.post(
            "/api/modbus/inject",
            json={"kind": "exception", "slave": 1, "code": 0x04, "ttl_s": 0.5},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ttl_s"] == 0.5
        assert 0 < body["remaining_s"] <= 0.5
        broken = await tcp.read_holding_registers(1, count=1, device_id=1)
        assert broken.isError()
        assert broken.exception_code == 0x04  # type: ignore[attr-defined]
        await asyncio.sleep(0.9)
        restored = await tcp.read_holding_registers(1, count=1, device_id=1)
        assert not restored.isError()
        listed = await web.get("/api/modbus/inject")
        assert listed.json()["items"] == []


async def test_rest_clear_by_id_restores_only_target(config: Config) -> None:
    """Сброс по id снимает одну инъекцию; вторая продолжает действовать на проводе."""
    async with _stack(config) as (web, tcp):
        first = await web.post(
            "/api/modbus/inject",
            json={"kind": "exception", "slave": 1, "code": 0x02, "ttl_s": 60},
        )
        second = await web.post(
            "/api/modbus/inject",
            json={"kind": "exception", "slave": 2, "code": 0x03, "ttl_s": 60},
        )
        assert first.status_code == 200 and second.status_code == 200
        first_id = first.json()["id"]
        broken1 = await tcp.read_holding_registers(1, count=1, device_id=1)
        broken2 = await tcp.read_holding_registers(1, count=1, device_id=2)
        assert broken1.isError() and broken1.exception_code == 0x02  # type: ignore[attr-defined]
        assert broken2.isError() and broken2.exception_code == 0x03  # type: ignore[attr-defined]
        cleared = await web.post("/api/modbus/inject/clear", json={"id": first_id})
        assert cleared.json()["cleared"] == 1
        restored1 = await tcp.read_holding_registers(1, count=1, device_id=1)
        assert not restored1.isError()
        still_broken = await tcp.read_holding_registers(1, count=1, device_id=2)
        assert still_broken.isError()
        rest = await web.post("/api/modbus/inject/clear", json={})
        assert rest.json()["cleared"] == 1
        restored2 = await tcp.read_holding_registers(1, count=1, device_id=2)
        assert not restored2.isError()


async def test_rest_delay_injection_slows_wire_then_clear(config: Config) -> None:
    """delay из REST замедляет ответ на проводе; clear возвращает нормальную скорость."""
    async with _stack(config) as (web, tcp):
        response = await web.post(
            "/api/modbus/inject",
            json={"kind": "delay", "slave": 1, "delay_ms": 300, "ttl_s": 60},
        )
        assert response.status_code == 200
        started = time.monotonic()
        delayed = await tcp.read_holding_registers(1, count=1, device_id=1)
        elapsed = time.monotonic() - started
        assert not delayed.isError()
        assert elapsed >= 0.25, f"ответ пришёл слишком быстро: {elapsed:.3f} с"
        cleared = await web.post("/api/modbus/inject/clear", json={})
        assert cleared.json()["cleared"] == 1
        started = time.monotonic()
        fast = await tcp.read_holding_registers(1, count=1, device_id=1)
        elapsed = time.monotonic() - started
        assert not fast.isError()
        assert elapsed < 0.2, f"ответ остался медленным: {elapsed:.3f} с"

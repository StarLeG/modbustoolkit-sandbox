"""Эпик 6: enforcement инъекций ошибок в Modbus-пути (ADR-007).

Инъекции применяются в `GuardedRequestHandler.handle_request` до обработки
запроса: exception — ответ кодом вместо обработки (TASK-601), delay — пауза
перед обработкой (TASK-602), timeout — молчание (TASK-603). Истёкшие инъекции
перестают действовать сами (TASK-604, ленивый TTL).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from pymodbus.client import AsyncModbusTcpClient

from app.modbus.config import Config
from app.modbus.injection import (
    EXCEPTION_CODES,
    Injection,
    InjectionRegistry,
    find_injection,
)
from app.modbus.server import SlaveMapContext, serve
from app.modbus.slave_map import SlaveMap
from app.modbus.types import SLAVE_SPECS

DeviceMaps = dict[int, SlaveMap]


def _injection(
    injection_id: int,
    kind: str,
    slave: int,
    *,
    area: str | None = None,
    address: int | None = None,
    count: int | None = None,
    code: int | None = None,
    delay_ms: int | None = None,
) -> Injection:
    return Injection(
        id=injection_id,
        kind=kind,  # type: ignore[arg-type]
        slave=slave,
        ttl_s=60.0,
        created_at=0.0,
        expires_at=60.0,
        area=area,  # type: ignore[arg-type]
        address=address,
        count=count,
        code=code,
        delay_ms=delay_ms,
    )


@asynccontextmanager
async def _serving(
    config: Config, injections: InjectionRegistry
) -> AsyncIterator[tuple[AsyncModbusTcpClient, DeviceMaps]]:
    """Сервер с данным реестром инъекций + подключённый клиент."""
    maps: DeviceMaps = {slave_id: SlaveMap(slave_id) for slave_id in SLAVE_SPECS}
    async with serve(config, SlaveMapContext(maps), injections):
        client = AsyncModbusTcpClient("127.0.0.1", port=config.MODBUS_TCP_PORT)
        assert await client.connect()
        yield client, maps
        client.close()


# --- сопоставление запроса с инъекцией (TASK-601, ADR-007 п. 2) ------------------------


def test_find_injection_whole_slave_matches_any_area() -> None:
    injections = [_injection(1, "exception", 1, code=0x02)]
    assert find_injection(injections, 1, "hr", 5, 1) is injections[0]
    assert find_injection(injections, 1, "cl", 3, 1) is injections[0]
    assert find_injection(injections, 2, "hr", 5, 1) is None  # другой слейв


def test_find_injection_range_and_area_filter() -> None:
    injections = [_injection(1, "exception", 1, area="hr", address=5, count=2, code=0x02)]
    assert find_injection(injections, 1, "hr", 5, 1) is injections[0]
    assert find_injection(injections, 1, "hr", 6, 1) is injections[0]
    assert find_injection(injections, 1, "hr", 7, 1) is None  # за диапазоном
    assert find_injection(injections, 1, "cl", 5, 1) is None  # другая область


def test_find_injection_32bit_raw_window_touches_value() -> None:
    """Сетка значений: для slave 2 raw N+1 задевает значение N (как у политики)."""
    injections = [_injection(1, "exception", 2, area="hr", address=1, count=1, code=0x02)]
    assert find_injection(injections, 2, "hr", 1, 2) is injections[0]  # raw 1..2 → value 1
    assert find_injection(injections, 2, "hr", 2, 2) is injections[0]  # raw 2..3 → 1 и 3
    assert find_injection(injections, 2, "hr", 3, 2) is None  # raw 3..4 → только value 3


def test_find_injection_first_match_wins() -> None:
    first = _injection(1, "delay", 1, delay_ms=10)
    second = _injection(2, "exception", 1, code=0x02)
    assert find_injection([first, second], 1, "hr", 5, 1) is first
    assert find_injection([second, first], 1, "hr", 5, 1) is second


def test_find_injection_no_match_cases() -> None:
    injections = [_injection(1, "exception", 1, area="ir", address=300, count=1, code=0x02)]
    assert find_injection([], 1, "hr", 5, 1) is None
    assert find_injection(injections, 11, "hr", 5, 1) is None  # вне SLAVE_SPECS
    assert find_injection(injections, 1, "ir", 301, 1) is None  # соседний адрес


# --- TASK-601: exception-инъекции ------------------------------------------------------


async def test_exception_injection_each_code(config: Config) -> None:
    """Каждый код из EXCEPTION_CODES возвращается вместо обработки запроса."""
    injections = InjectionRegistry()
    async with _serving(config, injections) as (client, _maps):
        for code in EXCEPTION_CODES:
            injections.clear()
            injections.add("exception", 1, code=code)
            response = await client.read_holding_registers(5, count=1, device_id=1)
            assert response.isError(), code
            assert response.exception_code == code
        injections.clear()  # автосброс через снятие — запрос снова штатный
        response = await client.read_holding_registers(5, count=1, device_id=1)
        assert not response.isError()


async def test_exception_injection_skips_processing(config: Config) -> None:
    """Запись под exception-инъекцией не применяется (ADR-007 п. 3)."""
    injections = InjectionRegistry()
    injections.add("exception", 1, code=0x02)
    async with _serving(config, injections) as (client, maps):
        write = await client.write_register(5, 0x1234, device_id=1)
        assert write.isError() and write.exception_code == 0x02
        assert maps[1].get_registers("hr", 5, 1) == [0]

        coil = await client.write_coil(3, True, device_id=1)
        assert coil.isError() and coil.exception_code == 0x02
        assert maps[1].get_bits("cl", 3, 1) == [False]

        multi = await client.write_registers(5, [1, 2], device_id=1)
        assert multi.isError() and multi.exception_code == 0x02
        assert maps[1].get_registers("hr", 5, 2) == [0, 0]


async def test_exception_injection_target_range(config: Config) -> None:
    """Инъекция на диапазон задевает только его (и только свою область)."""
    injections = InjectionRegistry()
    injections.add("exception", 1, area="hr", address=5, count=2, code=0x03)
    async with _serving(config, injections) as (client, _maps):
        hit = await client.read_holding_registers(5, count=1, device_id=1)
        assert hit.isError() and hit.exception_code == 0x03
        miss_address = await client.read_holding_registers(7, count=1, device_id=1)
        assert not miss_address.isError()
        miss_area = await client.read_coils(5, count=1, device_id=1)
        assert not miss_area.isError()
        miss_slave = await client.read_holding_registers(5, count=1, device_id=2)
        assert not miss_slave.isError()

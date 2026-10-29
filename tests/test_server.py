"""TASK-101: Modbus-TCP сервер — запуск/остановка, 10 unit ID, гейт FC."""
from __future__ import annotations

import socket
from dataclasses import replace

import pytest
from pymodbus.client import AsyncModbusTcpClient
from pymodbus.constants import ExcCodes
from pymodbus.exceptions import NoSuchIdException
from pymodbus.pdu import ModbusPDU

from app.modbus.config import Config
from app.modbus.server import (
    RejectedRequest,
    build_context,
    serve,
)
from app.modbus.slave_map import SlaveMap


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def config() -> Config:
    return replace(Config(), MODBUS_TCP_HOST="127.0.0.1", MODBUS_TCP_PORT=_free_port())


# --- контекст без сети ----------------------------------------------------------------


async def test_context_read_write_round_trip() -> None:
    ctx = build_context()
    assert await ctx.async_getValues(1, 3, 1, 1) == [0]
    assert await ctx.async_setValues(1, 6, 1, [0x1234]) is None
    assert await ctx.async_getValues(1, 3, 1, 1) == [0x1234]
    assert await ctx.async_setValues(1, 5, 2, [True]) is None
    assert await ctx.async_getValues(1, 1, 2, 1) == [True]


async def test_context_fc5_fc6_echo_reads() -> None:
    # pymodbus после записи перечитывает значение тем же FC.
    ctx = build_context()
    await ctx.async_setValues(1, 6, 7, [42])
    assert await ctx.async_getValues(1, 6, 7, 1) == [42]
    await ctx.async_setValues(1, 5, 7, [True])
    assert await ctx.async_getValues(1, 5, 7, 1) == [True]


async def test_context_fc16_max_two_registers() -> None:
    ctx = build_context()
    assert await ctx.async_setValues(1, 16, 1, [1, 2]) is None
    assert await ctx.async_setValues(1, 16, 3, [1, 2, 3]) == ExcCodes.ILLEGAL_VALUE


async def test_context_forbidden_fc_rejected() -> None:
    ctx = build_context()
    assert await ctx.async_getValues(1, 22, 1, 1) == ExcCodes.ILLEGAL_FUNCTION
    assert await ctx.async_setValues(1, 15, 1, [True]) == ExcCodes.ILLEGAL_FUNCTION


async def test_context_unknown_device_raises() -> None:
    ctx = build_context()
    with pytest.raises(NoSuchIdException):
        await ctx.async_getValues(11, 3, 1, 1)
    with pytest.raises(NoSuchIdException):
        await ctx.async_setValues(0, 6, 1, [1])


async def test_context_slave10_ir_is_computed() -> None:
    ctx = build_context()
    year = await ctx.async_getValues(10, 4, 1, 1)
    assert isinstance(year, list)
    assert int(year[0]) >= 2024  # год по UTC
    assert await ctx.async_getValues(10, 3, 1, 1) == ExcCodes.ILLEGAL_ADDRESS


async def test_rejected_request_answers_illegal_function() -> None:
    original = ModbusPDU()
    original.function_code = 15
    rejected = RejectedRequest(original)
    response = await rejected.datastore_update(build_context(), 1)
    # ExceptionResponse OR-ит 0x80 в function code: 15 | 0x80 = 0x8F.
    assert response.function_code == 15 | 0x80
    assert response.exception_code == ExcCodes.ILLEGAL_FUNCTION  # type: ignore[attr-defined]


def test_build_context_has_all_ten_devices() -> None:
    ctx = build_context()
    assert ctx.device_ids() == list(range(1, 11))
    assert isinstance(ctx._maps[10], SlaveMap)


# --- интеграция: запуск, 10 unit ID, остановка ---------------------------------------


async def test_server_serves_all_ten_unit_ids(config: Config) -> None:
    async with serve(config) as server:
        client = AsyncModbusTcpClient("127.0.0.1", port=config.MODBUS_TCP_PORT)
        assert await client.connect()
        for device_id in range(1, 10):
            response = await client.read_holding_registers(1, count=1, device_id=device_id)
            assert not response.isError(), f"slave {device_id}: {response}"
            assert response.registers == [0]
        response = await client.read_input_registers(1, count=1, device_id=10)
        assert not response.isError()
        assert response.registers[0] >= 2024  # год по UTC
        client.close()
        assert server.transport is not None


async def test_graceful_shutdown_closes_listener(config: Config) -> None:
    async with serve(config):
        client = AsyncModbusTcpClient("127.0.0.1", port=config.MODBUS_TCP_PORT)
        assert await client.connect()
        # Остановка с активным соединением — shutdown закрывает и его.
        client.close()
    with (
        pytest.raises(OSError),
        socket.create_connection(("127.0.0.1", config.MODBUS_TCP_PORT), timeout=1),
    ):
        pass

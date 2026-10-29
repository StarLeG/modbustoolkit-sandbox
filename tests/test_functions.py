"""Эпик 1: функции Modbus FC 1/2/3/4/5/6/16 (TASK-111…115).

Каждый FC проверяется эталонным клиентом `AsyncModbusTcpClient` поверх
реального TCP-соединения. Значения для чтения предзаписываются в карты
(`client_with_maps`) или пишутся самим клиентом.

Биты на проводе упакованы по 8 на байт, поэтому `response.bits` содержит
8 * ceil(count/8) значений — сверяются первые `count` бит (срезом).
"""
from __future__ import annotations

import time

from pymodbus.client import AsyncModbusTcpClient

from app.modbus.codec import decode
from app.modbus.slave_map import SlaveMap
from app.modbus.types import RegisterFormat

# --- TASK-111: FC 1/2 — чтение CL/DI --------------------------------------------------


async def test_fc1_read_coils(
    client_with_maps: tuple[AsyncModbusTcpClient, dict[int, SlaveMap]],
) -> None:
    client, maps = client_with_maps
    maps[1].set_bits("cl", 1, [True, False, True])
    response = await client.read_coils(1, count=3, device_id=1)
    assert not response.isError()
    assert response.bits[:3] == [True, False, True]


async def test_fc2_read_discrete_inputs(
    client_with_maps: tuple[AsyncModbusTcpClient, dict[int, SlaveMap]],
) -> None:
    client, maps = client_with_maps
    maps[2].set_bits("di", 10, [True, True, False])
    response = await client.read_discrete_inputs(10, count=3, device_id=2)
    assert not response.isError()
    assert response.bits[:3] == [True, True, False]


async def test_fc1_fc2_on_slaves_1_to_9(
    client_with_maps: tuple[AsyncModbusTcpClient, dict[int, SlaveMap]],
) -> None:
    client, maps = client_with_maps
    for device_id in range(1, 10):
        maps[device_id].set_bits("cl", 64, [True])
        maps[device_id].set_bits("di", 1, [True])
        coils = await client.read_coils(64, count=1, device_id=device_id)
        assert coils.bits[:1] == [True], f"slave {device_id}"
        discrete = await client.read_discrete_inputs(1, count=1, device_id=device_id)
        assert discrete.bits[:1] == [True], f"slave {device_id}"


async def test_fc1_fc2_slave10_absent(client: AsyncModbusTcpClient) -> None:
    for reader in (client.read_coils, client.read_discrete_inputs):
        response = await reader(1, count=1, device_id=10)
        assert response.isError()
        assert response.exception_code == 0x02


# --- TASK-112: FC 3/4 — чтение HR/IR --------------------------------------------------


async def test_fc3_read_holding_registers(
    client_with_maps: tuple[AsyncModbusTcpClient, dict[int, SlaveMap]],
) -> None:
    client, maps = client_with_maps
    maps[1].set_registers("hr", 5, [0x1234, 0xABCD])
    response = await client.read_holding_registers(5, count=2, device_id=1)
    assert not response.isError()
    assert response.registers == [0x1234, 0xABCD]


async def test_fc4_read_input_registers(
    client_with_maps: tuple[AsyncModbusTcpClient, dict[int, SlaveMap]],
) -> None:
    client, maps = client_with_maps
    # Слэйв 6 (FLOAT_ABCD) — читаем сырые регистры попарно.
    maps[6].set_registers("ir", 1, [0x4321, 0xDCBA])
    response = await client.read_input_registers(1, count=2, device_id=6)
    assert not response.isError()
    assert response.registers == [0x4321, 0xDCBA]


async def test_fc3_fc4_on_slaves_1_to_9(
    client_with_maps: tuple[AsyncModbusTcpClient, dict[int, SlaveMap]],
) -> None:
    client, maps = client_with_maps
    for device_id in range(1, 10):
        # Сырое окно у границы карты (1023..1024 валидно и для 32-битных форматов).
        maps[device_id].set_registers("hr", 1023, [0x0001, 0x0002])
        hr = await client.read_holding_registers(1023, count=2, device_id=device_id)
        assert hr.registers == [0x0001, 0x0002], f"slave {device_id}"
        maps[device_id].set_registers("ir", 1, [device_id * 0x1111])
        ir = await client.read_input_registers(1, count=1, device_id=device_id)
        assert ir.registers == [device_id * 0x1111], f"slave {device_id}"


async def test_fc4_slave10_computed_clock(client: AsyncModbusTcpClient) -> None:
    year = await client.read_input_registers(1, count=1, device_id=10)
    assert not year.isError()
    assert year.registers[0] >= 2024  # год по UTC
    ts = await client.read_input_registers(300, count=2, device_id=10)
    assert not ts.isError()
    value = decode(ts.registers, RegisterFormat.UINT32_ABCD)
    assert isinstance(value, int)
    assert abs(value - time.time()) <= 2.0


async def test_fc3_slave10_absent(client: AsyncModbusTcpClient) -> None:
    response = await client.read_holding_registers(1, count=1, device_id=10)
    assert response.isError()
    assert response.exception_code == 0x02


# --- TASK-113: FC 5 — запись одного coil (CL) ------------------------------------------


async def test_fc5_write_single_coil_true(client: AsyncModbusTcpClient) -> None:
    response = await client.write_coil(5, True, device_id=1)
    assert not response.isError()
    back = await client.read_coils(5, count=1, device_id=1)
    assert back.bits[:1] == [True]


async def test_fc5_write_single_coil_false_with_echo(client: AsyncModbusTcpClient) -> None:
    response = await client.write_coil(6, False, device_id=2)
    assert not response.isError()
    # Echo-ответ FC 5 содержит состояние coil после записи.
    assert response.bits[:1] == [False]
    back = await client.read_coils(6, count=1, device_id=2)
    assert back.bits[:1] == [False]


async def test_fc5_on_slaves_1_to_9(client: AsyncModbusTcpClient) -> None:
    for device_id in range(1, 10):
        response = await client.write_coil(64, True, device_id=device_id)
        assert not response.isError(), f"slave {device_id}"
        back = await client.read_coils(64, count=1, device_id=device_id)
        assert back.bits[:1] == [True], f"slave {device_id}"


async def test_fc5_slave10_absent(client: AsyncModbusTcpClient) -> None:
    response = await client.write_coil(1, True, device_id=10)
    assert response.isError()
    assert response.exception_code == 0x02


# --- TASK-114: FC 6 — запись одного holding register (HR) ------------------------------


async def test_fc6_write_single_register(client: AsyncModbusTcpClient) -> None:
    response = await client.write_register(10, 0xBEEF, device_id=3)
    assert not response.isError()
    # Echo-ответ FC 6 содержит записанное значение.
    assert response.registers == [0xBEEF]
    back = await client.read_holding_registers(10, count=1, device_id=3)
    assert back.registers == [0xBEEF]


async def test_fc6_on_slaves_1_to_9(client: AsyncModbusTcpClient) -> None:
    for device_id in range(1, 10):
        value = device_id * 0x1001
        response = await client.write_register(1, value, device_id=device_id)
        assert not response.isError(), f"slave {device_id}"
        back = await client.read_holding_registers(1, count=1, device_id=device_id)
        assert back.registers == [value], f"slave {device_id}"


async def test_fc6_slave10_absent(client: AsyncModbusTcpClient) -> None:
    response = await client.write_register(1, 2024, device_id=10)
    assert response.isError()
    assert response.exception_code == 0x02

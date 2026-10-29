"""TASK-122: интеграционный тест эталонным клиентом по всем 10 слейвам.

Полный цикл через провод: значение кодируется в формат слейва (codec),
пишется FC 6/16 (или предзаписью в карту для IR/DI), читается обратно
`AsyncModbusTcpClient` и сверяется с ожиданием из `docs/HRIR_SPEC.md`.
"""
from __future__ import annotations

import time

from pymodbus.client import AsyncModbusTcpClient

from app.modbus.codec import decode, encode
from app.modbus.slave_map import SlaveMap
from app.modbus.types import SLAVE_SPECS, RegisterFormat

# Эталонные значения: по одному на каждый формат (float — 1.5, представимо точно).
REFERENCE_VALUES: dict[int, int | float] = {
    1: 0x1234,  # UINT16
    2: 0x12345678,  # UINT32_ABCD
    3: 0x12345678,  # UINT32_CDAB
    4: 0x12345678,  # UINT32_BADC
    5: 0x12345678,  # UINT32_DCBA
    6: 1.5,  # FLOAT_ABCD
    7: 1.5,  # FLOAT_CDAB
    8: 1.5,  # FLOAT_BADC
    9: 1.5,  # FLOAT_DCBA
}

# Таблица раскодирования HRIR_SPEC.md: регистры [0x1234, 0x5678] → uint32.
HRIR_UINT32_TABLE: dict[int, int] = {
    2: 0x12345678,  # ABCD
    3: 0x56781234,  # CDAB
    4: 0x34127856,  # BADC
    5: 0x78563412,  # DCBA
}


async def test_reference_holding_each_slave(client: AsyncModbusTcpClient) -> None:
    """Запись эталонного значения в формате слейва → чтение → декодирование."""
    for device_id, value in REFERENCE_VALUES.items():
        fmt = SLAVE_SPECS[device_id].fmt
        regs = encode(value, fmt)
        if len(regs) == 1:
            response = await client.write_register(1, regs[0], device_id=device_id)
        else:
            response = await client.write_registers(1, regs, device_id=device_id)
        assert not response.isError(), f"slave {device_id}: {response}"
        back = await client.read_holding_registers(1, count=len(regs), device_id=device_id)
        assert back.registers == regs, f"slave {device_id}"
        assert decode(back.registers, fmt) == value, f"slave {device_id}"


async def test_reference_input_each_slave(
    client_with_maps: tuple[AsyncModbusTcpClient, dict[int, SlaveMap]],
) -> None:
    """IR заполняются «движком» (предзапись в карту), чтение эталонным клиентом."""
    client, maps = client_with_maps
    for device_id, value in REFERENCE_VALUES.items():
        fmt = SLAVE_SPECS[device_id].fmt
        regs = encode(value, fmt)
        maps[device_id].set_registers("ir", 1, regs)
        back = await client.read_input_registers(1, count=len(regs), device_id=device_id)
        assert back.registers == regs, f"slave {device_id}"
        assert decode(back.registers, fmt) == value, f"slave {device_id}"


async def test_reference_hrir_table_on_wire(client: AsyncModbusTcpClient) -> None:
    """Эталонная таблица HRIR_SPEC: [0x1234, 0x5678] на проводе → uint32 по формату."""
    for device_id, expected in HRIR_UINT32_TABLE.items():
        response = await client.write_registers(1, [0x1234, 0x5678], device_id=device_id)
        assert not response.isError(), f"slave {device_id}"
        back = await client.read_holding_registers(1, count=2, device_id=device_id)
        assert decode(back.registers, SLAVE_SPECS[device_id].fmt) == expected, f"slave {device_id}"


async def test_reference_bits_each_slave(
    client_with_maps: tuple[AsyncModbusTcpClient, dict[int, SlaveMap]],
) -> None:
    """CL — запись FC 5 → чтение FC 1; DI — предзапись → чтение FC 2."""
    client, maps = client_with_maps
    for device_id in range(1, 10):
        response = await client.write_coil(1, True, device_id=device_id)
        assert not response.isError(), f"slave {device_id}"
        coils = await client.read_coils(1, count=1, device_id=device_id)
        assert coils.bits[:1] == [True], f"slave {device_id}"
        maps[device_id].set_bits("di", 1, [True])
        discrete = await client.read_discrete_inputs(1, count=1, device_id=device_id)
        assert discrete.bits[:1] == [True], f"slave {device_id}"


async def test_reference_slave10_moscow_and_timestamp(client: AsyncModbusTcpClient) -> None:
    """Slave 10: зона Z=3 (UTC+03) и Unix timestamp 300..301."""
    offset = await client.read_input_registers(38, count=1, device_id=10)  # 8 + 10*3
    assert offset.registers == [3]
    month = await client.read_input_registers(32, count=1, device_id=10)  # 2 + 10*3
    assert 1 <= month.registers[0] <= 12
    weekday = await client.read_input_registers(37, count=1, device_id=10)  # 7 + 10*3
    assert 1 <= weekday.registers[0] <= 7
    ts = await client.read_input_registers(300, count=2, device_id=10)
    value = decode(ts.registers, RegisterFormat.UINT32_ABCD)
    assert isinstance(value, int)
    assert abs(value - time.time()) <= 2.0

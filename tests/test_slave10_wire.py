"""TASK-805: slave 10 эталонным клиентом через TCP — 27 зон, резервы, timestamp.

Все 27 зон Z=0..26 читаются блоками FC 4 и сверяются с системным временем
(метрики — местное время зоны, offset по `zone_offset_hours`). Резервные
слоты каждого блока (9, 10) — нули; окно через «дырку» 269..299 → 0x02.
Unix timestamp 300..301 (UINT32_ABCD) — ±2 с от системного (сетевая задержка).
"""
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from pymodbus.client import AsyncModbusTcpClient

from app.modbus.codec import decode
from app.modbus.slave10 import ZONE_COUNT, ZONE_SLOTS, zone_offset_hours
from app.modbus.types import RegisterFormat

ILLEGAL_ADDRESS = 0x02


def _local_metrics(now: datetime, zone: int) -> list[int]:
    """Ожидаемый блок зоны: год..секунда, weekday 1..7, offset (int16)."""
    local = now.astimezone(UTC) + timedelta(hours=zone_offset_hours(zone))
    return [
        local.year,
        local.month,
        local.day,
        local.hour,
        local.minute,
        local.second,
        local.weekday() + 1,  # Пн=1 .. Вс=7
        zone_offset_hours(zone) & 0xFFFF,
    ]


async def test_all_27_zones_on_wire(client: AsyncModbusTcpClient) -> None:
    """Каждая зона Z=0..26: блок метрик согласован с системным временем ±2 с."""
    for zone in range(ZONE_COUNT):
        address = 1 + ZONE_SLOTS * zone
        response = await client.read_input_registers(address, count=8, device_id=10)
        assert not response.isError(), f"зона Z={zone}: {response}"
        block = response.registers
        # Сходимость: собрать локальное время из метрик ≈ now + offset.
        offset = zone_offset_hours(zone)
        hour, minute, second = block[3], block[4], block[5]
        reconstructed = datetime(
            block[0], block[1], block[2], hour, minute, second, tzinfo=UTC
        ) - timedelta(hours=offset)
        drift = abs(reconstructed.timestamp() - time.time())
        assert drift <= 2.0, f"зона Z={zone}: расхождение {drift:.1f} с"
        # Согласованность метрик между собой.
        assert 1 <= block[1] <= 12, f"зона Z={zone}: месяц {block[1]}"
        assert 1 <= block[6] <= 7, f"зона Z={zone}: weekday {block[6]}"
        assert block[6] == datetime(block[0], block[1], block[2]).weekday() + 1
        assert block[7] == offset & 0xFFFF, f"зона Z={zone}: offset {block[7]}"


async def test_reserved_slots_zero_on_wire(client: AsyncModbusTcpClient) -> None:
    """Резервные слоты 9, 10 каждого блока (в пределах 1..268) — нули."""
    for zone in range(ZONE_COUNT):
        for k in (8, 9):
            address = 1 + ZONE_SLOTS * zone + k
            if address > 268:
                continue  # 269, 270 последней зоны — за картой
            response = await client.read_input_registers(address, count=1, device_id=10)
            assert not response.isError(), f"адрес {address}"
            assert response.registers == [0], f"адрес {address}: {response.registers}"


async def test_gap_269_299_rejected_on_wire(client: AsyncModbusTcpClient) -> None:
    """«Дырка» между блоками зон и timestamp: 269..299 → Illegal Data Address."""
    for address in (269, 280, 299):
        response = await client.read_input_registers(address, count=1, device_id=10)
        assert response.isError()
        assert response.exception_code == ILLEGAL_ADDRESS
    # Окно через дырку тоже запрещено.
    response = await client.read_input_registers(265, count=8, device_id=10)
    assert response.isError()
    assert response.exception_code == ILLEGAL_ADDRESS


async def test_timestamp_300_301_on_wire(client: AsyncModbusTcpClient) -> None:
    """Unix timestamp UINT32_ABCD в 300..301 — ±2 с от системного времени."""
    response = await client.read_input_registers(300, count=2, device_id=10)
    assert not response.isError()
    value = decode(response.registers, RegisterFormat.UINT32_ABCD)
    assert isinstance(value, int)
    assert abs(value - time.time()) <= 2.0


async def test_last_zone_block_ends_at_268_on_wire(client: AsyncModbusTcpClient) -> None:
    """Блок Z=26: адреса 261..268 читаются, 269 уже за картой."""
    response = await client.read_input_registers(261, count=8, device_id=10)
    assert not response.isError()
    assert len(response.registers) == 8
    response = await client.read_input_registers(268, count=2, device_id=10)
    assert response.isError()
    assert response.exception_code == ILLEGAL_ADDRESS

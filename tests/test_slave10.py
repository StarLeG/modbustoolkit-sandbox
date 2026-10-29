"""TASK-103: slave 10 — часовые пояса (27 зон), резервы → 0, Unix timestamp."""
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest

from app.modbus.codec import decode
from app.modbus.slave10 import (
    TIMESTAMP_ADDR,
    ZONE_COUNT,
    ZONE_SLOTS,
    read_registers,
    zone_offset_hours,
)
from app.modbus.slave_map import EXC_ILLEGAL_ADDRESS, EXC_ILLEGAL_VALUE, ModbusMapError, SlaveMap
from app.modbus.types import RegisterFormat

# Суббота 2024-06-15 12:34:56 UTC (weekday() = 5 → метрика «день недели» = 6).
NOW = datetime(2024, 6, 15, 12, 34, 56, tzinfo=UTC)


def _error_code(start: int, count: int) -> int:
    with pytest.raises(ModbusMapError) as excinfo:
        read_registers(start, count, now=NOW)
    return excinfo.value.exception_code


# --- смещения зон ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("zone", "offset"),
    [(0, 0), (1, 1), (3, 3), (14, 14), (15, -1), (19, -5), (26, -12)],
)
def test_zone_offset_hours(zone: int, offset: int) -> None:
    assert zone_offset_hours(zone) == offset


# --- блоки метрик ---------------------------------------------------------------------


def test_zone0_full_block() -> None:
    # Гринвич, Z=0: адреса 1..10.
    assert read_registers(1, 10, now=NOW) == [2024, 6, 15, 12, 34, 56, 6, 0, 0, 0]


def test_moscow_zone3_block() -> None:
    # UTC+03:00, Z=3: адреса 31..40; местное время 15:34:56.
    assert read_registers(31, 10, now=NOW) == [2024, 6, 15, 15, 34, 56, 6, 3, 0, 0]


def test_west_zone19_minus5_block() -> None:
    # UTC−05:00, Z=19: адреса 191..200; местное время 07:34:56; смещение −5 = 0xFFFB.
    assert read_registers(191, 10, now=NOW) == [2024, 6, 15, 7, 34, 56, 6, 0xFFFB, 0, 0]


def test_all_27_zones_clocks() -> None:
    for zone in range(ZONE_COUNT):
        offset = zone_offset_hours(zone)
        local = NOW + timedelta(hours=offset)
        block = read_registers(1 + ZONE_SLOTS * zone, 8, now=NOW)
        assert block == [
            local.year,
            local.month,
            local.day,
            local.hour,
            local.minute,
            local.second,
            local.weekday() + 1,
            offset & 0xFFFF,
        ], f"зона Z={zone}"


@pytest.mark.parametrize("address", [9, 10, 19, 20])
def test_reserved_registers_are_zero(address: int) -> None:
    assert read_registers(address, 1, now=NOW) == [0]


def test_last_zone_block_ends_at_268() -> None:
    # Z=26 (UTC−12:00): адреса 261..268; местное время 00:34:56; смещение −12 = 0xFFF4.
    assert read_registers(261, 8, now=NOW) == [2024, 6, 15, 0, 34, 56, 6, 0xFFF4]


# --- границы карты и «дырка» ----------------------------------------------------------


def test_map_boundaries() -> None:
    assert len(read_registers(268, 1, now=NOW)) == 1
    assert len(read_registers(TIMESTAMP_ADDR, 2, now=NOW)) == 2
    assert _error_code(0, 1) == EXC_ILLEGAL_ADDRESS
    assert _error_code(269, 1) == EXC_ILLEGAL_ADDRESS
    assert _error_code(270, 1) == EXC_ILLEGAL_ADDRESS
    assert _error_code(299, 1) == EXC_ILLEGAL_ADDRESS
    assert _error_code(302, 1) == EXC_ILLEGAL_ADDRESS


def test_window_across_gap_rejected() -> None:
    assert _error_code(268, 3) == EXC_ILLEGAL_ADDRESS
    assert _error_code(260, 12) == EXC_ILLEGAL_ADDRESS
    assert len(read_registers(301, 1, now=NOW)) == 1  # 301 — последний регистр карты
    assert _error_code(1, 0) == EXC_ILLEGAL_VALUE


# --- Unix timestamp (300..301, UINT32_ABCD) -------------------------------------------


def test_timestamp_exact() -> None:
    regs = read_registers(TIMESTAMP_ADDR, 2, now=NOW)
    assert decode(regs, RegisterFormat.UINT32_ABCD) == round(NOW.timestamp())


@pytest.mark.parametrize("microsecond", [0, 400_000, 600_000, 999_999])
def test_timestamp_precision_within_half_second(microsecond: int) -> None:
    now = datetime(2024, 6, 15, 12, 34, 56, microsecond=microsecond, tzinfo=UTC)
    regs = read_registers(TIMESTAMP_ADDR, 2, now=now)
    decoded = decode(regs, RegisterFormat.UINT32_ABCD)
    assert isinstance(decoded, int)
    assert abs(decoded - now.timestamp()) <= 0.5


# --- дни недели и переносы ------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "weekday_metric"),
    [(16, 7), (17, 1), (15, 6)],  # вс, пн, сб
)
def test_weekday_mapping(day: int, weekday_metric: int) -> None:
    now = datetime(2024, 6, day, 12, 0, 0, tzinfo=UTC)
    assert read_registers(7, 1, now=now) == [weekday_metric]


def test_day_rollover() -> None:
    # 23:30 UTC + 3 часа → следующий день.
    now = datetime(2024, 6, 15, 23, 30, 0, tzinfo=UTC)
    assert read_registers(31, 4, now=now) == [2024, 6, 16, 2]


def test_year_rollover() -> None:
    # 31.12 22:00 UTC + 3 часа → 01.01 следующего года.
    now = datetime(2024, 12, 31, 22, 0, 0, tzinfo=UTC)
    assert read_registers(31, 3, now=now) == [2025, 1, 1]


def test_naive_datetime_treated_as_utc() -> None:
    naive = datetime(2024, 6, 15, 12, 34, 56)
    assert read_registers(4, 1, now=naive) == [12]


# --- интеграция со SlaveMap -----------------------------------------------------------


def test_slave10_map_uses_computed_reader() -> None:
    smap = SlaveMap(10, ir_reader=lambda start, count: read_registers(start, count, now=NOW))
    assert smap.get_registers("ir", 31, 1) == [2024]
    assert smap.get_registers("ir", 34, 1) == [15]


def test_slave10_map_default_reader_is_live_clock() -> None:
    smap = SlaveMap(10)
    regs = smap.get_registers("ir", TIMESTAMP_ADDR, 2)
    decoded = decode(regs, RegisterFormat.UINT32_ABCD)
    assert isinstance(decoded, int)
    assert abs(decoded - time.time()) <= 2.0


def test_slave10_map_rejects_ir_writes() -> None:
    smap = SlaveMap(10)
    with pytest.raises(ValueError):
        smap.set_registers("ir", 1, [2024])

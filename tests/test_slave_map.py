"""TASK-102: модель карты адресов SlaveMap — окна, sparse-хранилище, 0x02/0x03."""
from __future__ import annotations

from collections.abc import Callable

import pytest

from app.modbus.slave_map import (
    EXC_ILLEGAL_ADDRESS,
    EXC_ILLEGAL_VALUE,
    ModbusMapError,
    SlaveMap,
)


def _map_error_code(func: Callable[..., object], *args: object) -> int:
    with pytest.raises(ModbusMapError) as excinfo:
        func(*args)
    return excinfo.value.exception_code


# --- конструирование -------------------------------------------------------------------


def test_construction_accepts_all_slaves() -> None:
    for slave_id in range(1, 11):
        assert SlaveMap(slave_id).spec.slave_id == slave_id


@pytest.mark.parametrize("slave_id", [0, 11, -1])
def test_construction_rejects_unknown_slave(slave_id: int) -> None:
    with pytest.raises(ValueError):
        SlaveMap(slave_id)


# --- значения по умолчанию ------------------------------------------------------------


def test_uninitialized_registers_read_zero() -> None:
    smap = SlaveMap(1)
    assert smap.get_registers("hr", 1, 10) == [0] * 10
    assert smap.get_registers("ir", 500, 3) == [0, 0, 0]


def test_uninitialized_bits_read_false() -> None:
    smap = SlaveMap(2)
    assert smap.get_bits("cl", 1, 64) == [False] * 64
    assert smap.get_bits("di", 60, 5) == [False] * 5


# --- запись/чтение --------------------------------------------------------------------


def test_registers_round_trip() -> None:
    smap = SlaveMap(1)
    smap.set_registers("hr", 10, [0x1234, 0, 0xFFFF])
    assert smap.get_registers("hr", 10, 3) == [0x1234, 0, 0xFFFF]
    assert smap.get_registers("hr", 11, 1) == [0]
    assert smap.get_registers("hr", 12, 1) == [0xFFFF]


def test_registers_round_trip_raw_window_on_32bit_slave() -> None:
    # Слэйв 3 (UINT32 CDAB): сырые регистры 1..1024 сплошняком, читать можно с любого.
    smap = SlaveMap(3)
    smap.set_registers("hr", 1, [0x5678, 0x1234, 0xABCD])
    assert smap.get_registers("hr", 2, 2) == [0x1234, 0xABCD]  # raw-окно с чётного адреса


def test_bits_round_trip() -> None:
    smap = SlaveMap(1)
    smap.set_bits("cl", 5, [True, False, True])
    assert smap.get_bits("cl", 5, 3) == [True, False, True]
    assert smap.get_bits("cl", 8, 1) == [False]


def test_write_to_ir_di_allowed_for_engine() -> None:
    # IR/DI генерируются движком — на уровне карты запись разрешена.
    smap = SlaveMap(1)
    smap.set_registers("ir", 7, [42])
    smap.set_bits("di", 7, [True])
    assert smap.get_registers("ir", 7, 1) == [42]
    assert smap.get_bits("di", 7, 1) == [True]


# --- валидация окон: 0x02 -------------------------------------------------------------


def test_slave1_window_boundaries() -> None:
    smap = SlaveMap(1)
    smap.get_registers("hr", 1024, 1)  # последний регистр — ок
    smap.get_registers("hr", 1023, 2)  # окно до конца — ок
    assert _map_error_code(smap.get_registers, "hr", 0, 1) == EXC_ILLEGAL_ADDRESS
    assert _map_error_code(smap.get_registers, "hr", 1025, 1) == EXC_ILLEGAL_ADDRESS
    assert _map_error_code(smap.get_registers, "hr", 1024, 2) == EXC_ILLEGAL_ADDRESS
    assert _map_error_code(smap.set_registers, "hr", 1025, [1]) == EXC_ILLEGAL_ADDRESS


def test_slave3_raw_registers_contiguous_to_1024() -> None:
    smap = SlaveMap(3)
    smap.get_registers("ir", 2, 2)  # raw-окно с чётного адреса допустимо
    smap.get_registers("ir", 1023, 2)  # значение 1023: регистры 1023..1024
    assert _map_error_code(smap.get_registers, "ir", 1024, 2) == EXC_ILLEGAL_ADDRESS
    assert _map_error_code(smap.get_registers, "ir", 0, 2) == EXC_ILLEGAL_ADDRESS


def test_bits_bounded_by_64() -> None:
    smap = SlaveMap(5)
    smap.get_bits("cl", 64, 1)
    assert _map_error_code(smap.get_bits, "cl", 65, 1) == EXC_ILLEGAL_ADDRESS
    assert _map_error_code(smap.set_bits, "di", 60, [False] * 6) == EXC_ILLEGAL_ADDRESS


def test_slave10_ir_window_and_gap() -> None:
    smap = SlaveMap(10)
    smap.get_registers("ir", 1, 268)  # 27 зон целиком
    smap.get_registers("ir", 268, 1)  # последняя метрика — ок
    smap.get_registers("ir", 300, 2)  # timestamp — ок
    # «Дырка» 269..299 и окно через неё — 0x02
    assert _map_error_code(smap.get_registers, "ir", 269, 1) == EXC_ILLEGAL_ADDRESS
    assert _map_error_code(smap.get_registers, "ir", 302, 1) == EXC_ILLEGAL_ADDRESS
    assert _map_error_code(smap.get_registers, "ir", 268, 34) == EXC_ILLEGAL_ADDRESS


def test_slave10_absent_hr_rejected() -> None:
    smap = SlaveMap(10)
    assert _map_error_code(smap.get_registers, "hr", 1, 1) == EXC_ILLEGAL_ADDRESS
    assert _map_error_code(smap.set_registers, "hr", 1, [0]) == EXC_ILLEGAL_ADDRESS


@pytest.mark.parametrize("area", ["cl", "di"])
def test_slave10_absent_bit_areas_rejected(area: str) -> None:
    smap = SlaveMap(10)
    assert _map_error_code(smap.get_bits, area, 1, 1) == EXC_ILLEGAL_ADDRESS  # type: ignore[arg-type]
    assert _map_error_code(smap.set_bits, area, 1, [True]) == EXC_ILLEGAL_ADDRESS  # type: ignore[arg-type]


def test_zero_or_negative_count_rejected() -> None:
    smap = SlaveMap(1)
    assert _map_error_code(smap.get_registers, "hr", 1, 0) == EXC_ILLEGAL_VALUE
    assert _map_error_code(smap.get_registers, "hr", 1, -3) == EXC_ILLEGAL_VALUE
    assert _map_error_code(smap.set_bits, "cl", 1, []) == EXC_ILLEGAL_VALUE


# --- ошибки вызывающего кода ----------------------------------------------------------


def test_area_kind_misuse_raises_value_error() -> None:
    smap = SlaveMap(1)
    with pytest.raises(ValueError):
        smap.get_registers("cl", 1, 1)
    with pytest.raises(ValueError):
        smap.set_registers("di", 1, [1])
    with pytest.raises(ValueError):
        smap.get_bits("hr", 1, 1)
    with pytest.raises(ValueError):
        smap.set_bits("ir", 1, [True])


def test_set_registers_rejects_bad_values() -> None:
    smap = SlaveMap(1)
    with pytest.raises(ValueError):
        smap.set_registers("hr", 1, [0x10000])
    with pytest.raises(ValueError):
        smap.set_registers("hr", 1, [-1])
    with pytest.raises(TypeError):
        smap.set_registers("hr", 1, [1.5])  # type: ignore[list-item]
    with pytest.raises(TypeError):
        smap.set_registers("hr", 1, [True])  # type: ignore[list-item]


def test_set_bits_rejects_non_bool() -> None:
    smap = SlaveMap(1)
    with pytest.raises(TypeError):
        smap.set_bits("cl", 1, [1])  # type: ignore[list-item]


# --- сквозная проверка окон по всем слейвам -------------------------------------------


@pytest.mark.parametrize("slave_id", range(1, 10))
def test_slaves_1_to_9_full_windows(slave_id: int) -> None:
    smap = SlaveMap(slave_id)
    smap.get_registers("hr", 1, 1024)
    smap.get_registers("ir", 1, 1024)
    smap.get_bits("cl", 1, 64)
    smap.get_bits("di", 1, 64)
    assert _map_error_code(smap.get_registers, "hr", 1025, 1) == EXC_ILLEGAL_ADDRESS

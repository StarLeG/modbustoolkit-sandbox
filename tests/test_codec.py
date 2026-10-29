"""TASK-121: кодек uint16/uint32/float ↔ ABCD/CDAB/BADC/DCBA (HRIR_SPEC.md)."""
from __future__ import annotations

import math
import struct

import pytest

from app.modbus import RegisterFormat
from app.modbus.codec import decode, encode

# Таблица раскодирования из docs/HRIR_SPEC.md:
# вход 4 байта 0x12 0x34 0x56 0x78 → регистры [0x1234, 0x5678]
HRIR_TABLE: list[tuple[RegisterFormat, int]] = [
    (RegisterFormat.UINT32_ABCD, 0x12345678),
    (RegisterFormat.UINT32_CDAB, 0x56781234),
    (RegisterFormat.UINT32_BADC, 0x34127856),
    (RegisterFormat.UINT32_DCBA, 0x78563412),
]

UINT32_ORDERS = [
    RegisterFormat.UINT32_ABCD,
    RegisterFormat.UINT32_CDAB,
    RegisterFormat.UINT32_BADC,
    RegisterFormat.UINT32_DCBA,
]

FLOAT_ORDERS = [
    RegisterFormat.FLOAT_ABCD,
    RegisterFormat.FLOAT_CDAB,
    RegisterFormat.FLOAT_BADC,
    RegisterFormat.FLOAT_DCBA,
]


def test_decode_hrir_reference_table() -> None:
    regs = [0x1234, 0x5678]
    for fmt, expected in HRIR_TABLE:
        assert decode(regs, fmt) == expected


def test_encode_hrir_reference_table() -> None:
    regs = [0x1234, 0x5678]
    for fmt, value in HRIR_TABLE:
        assert encode(value, fmt) == regs


@pytest.mark.parametrize("fmt", UINT32_ORDERS)
@pytest.mark.parametrize("value", [0, 1, 0x12345678, 0xFFFFFFFF, 0x80000000])
def test_uint32_round_trip(fmt: RegisterFormat, value: int) -> None:
    regs = encode(value, fmt)
    assert len(regs) == 2
    assert decode(regs, fmt) == value


@pytest.mark.parametrize("fmt", FLOAT_ORDERS)
@pytest.mark.parametrize("value", [0.0, -0.5, 1.5, 3.14159, -1e10, 5.6904566139e-28])
def test_float_round_trip(fmt: RegisterFormat, value: float) -> None:
    regs = encode(value, fmt)
    assert len(regs) == 2
    assert decode(regs, fmt) == pytest.approx(value)


def test_uint16_round_trip_and_boundaries() -> None:
    for value in (0, 1, 255, 0xFFFF):
        assert decode(encode(value, RegisterFormat.UINT16), RegisterFormat.UINT16) == value
    assert encode(0x1234, RegisterFormat.UINT16) == [0x1234]


def test_word_order_differs_across_formats() -> None:
    value = 0x12345678
    wires = {fmt: encode(value, fmt) for fmt in UINT32_ORDERS}
    assert wires[RegisterFormat.UINT32_ABCD] == [0x1234, 0x5678]
    assert wires[RegisterFormat.UINT32_CDAB] == [0x5678, 0x1234]
    assert wires[RegisterFormat.UINT32_BADC] == [0x3412, 0x7856]
    assert wires[RegisterFormat.UINT32_DCBA] == [0x7856, 0x3412]


# Таблица HRIR_SPEC.md для float: те же байты 12 34 56 78 трактуются
# перестановкой слов/байт формата (uint32-колонка таблицы → IEEE-754).
HRIR_FLOAT_TABLE: list[tuple[RegisterFormat, int]] = [
    (RegisterFormat.FLOAT_ABCD, 0x12345678),
    (RegisterFormat.FLOAT_CDAB, 0x56781234),
    (RegisterFormat.FLOAT_BADC, 0x34127856),
    (RegisterFormat.FLOAT_DCBA, 0x78563412),
]


def test_decode_float_hrir_reference_table() -> None:
    """Float decode использует ту же перестановку, что и uint32-колонка таблицы."""
    for fmt, bits in HRIR_FLOAT_TABLE:
        expected = struct.unpack(">f", struct.pack(">I", bits))[0]
        assert decode([0x1234, 0x5678], fmt) == expected


def test_float_one_point_zero_wire_registers() -> None:
    """1.0f = 0x3F800000: известные регистры на проводе для каждого порядка."""
    assert encode(1.0, RegisterFormat.FLOAT_ABCD) == [0x3F80, 0x0000]
    assert encode(1.0, RegisterFormat.FLOAT_CDAB) == [0x0000, 0x3F80]
    assert encode(1.0, RegisterFormat.FLOAT_BADC) == [0x803F, 0x0000]
    assert encode(1.0, RegisterFormat.FLOAT_DCBA) == [0x0000, 0x803F]


def test_float_negative_one_wire_registers() -> None:
    """−1.0f = 0xBF800000 — знак не теряется ни в одном порядке."""
    assert encode(-1.0, RegisterFormat.FLOAT_ABCD) == [0xBF80, 0x0000]
    assert encode(-1.0, RegisterFormat.FLOAT_DCBA) == [0x0000, 0x80BF]


def test_encode_rejects_out_of_range() -> None:
    with pytest.raises(ValueError):
        encode(-1, RegisterFormat.UINT16)
    with pytest.raises(ValueError):
        encode(0x10000, RegisterFormat.UINT16)
    with pytest.raises(ValueError):
        encode(-1, RegisterFormat.UINT32_ABCD)
    with pytest.raises(ValueError):
        encode(0x100000000, RegisterFormat.UINT32_ABCD)


def test_encode_rejects_wrong_types_for_uint() -> None:
    with pytest.raises(TypeError):
        encode(1.5, RegisterFormat.UINT16)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        encode(1.5, RegisterFormat.UINT32_CDAB)  # type: ignore[arg-type]


def test_decode_rejects_wrong_register_shape() -> None:
    with pytest.raises(ValueError):
        decode([0x1234], RegisterFormat.UINT32_ABCD)
    with pytest.raises(ValueError):
        decode([0x1234, 0x5678], RegisterFormat.UINT16)
    with pytest.raises(ValueError):
        decode([0x1234, 0x10000], RegisterFormat.UINT32_ABCD)


def test_float_nan_inf_round_trip_bitwise() -> None:
    for value in (math.nan, math.inf, -math.inf):
        regs = encode(value, RegisterFormat.FLOAT_ABCD)
        result = decode(regs, RegisterFormat.FLOAT_ABCD)
        assert math.isnan(result) if math.isnan(value) else result == value

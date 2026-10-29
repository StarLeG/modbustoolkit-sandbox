"""Кодеки значений: Python-значения ↔ регистры Modbus (см. docs/HRIR_SPEC.md).

Внутреннее представление — unpacked значения (`int` для uint16/uint32, `float`
для FLOAT). Регистр — 16 бит, байты внутри регистра big-endian (A — старший байт).
Порядок слов задаётся форматом: перестановка байт значения (A B C D) в байты
на проводе (ABCD/CDAB/BADC/DCBA).
"""
from __future__ import annotations

import math
import struct
from collections.abc import Sequence

from app.modbus.types import RegisterFormat

# _WIRE_PERM[fmt][i] — индекс байта значения (A=0, B=1, C=2, D=3),
# который попадает в i-й байт на проводе. Порядок слов одинаков
# для uint32 и float.
_ABCD: tuple[int, int, int, int] = (0, 1, 2, 3)
_CDAB: tuple[int, int, int, int] = (2, 3, 0, 1)
_BADC: tuple[int, int, int, int] = (1, 0, 3, 2)
_DCBA: tuple[int, int, int, int] = (3, 2, 1, 0)

_WIRE_PERM: dict[RegisterFormat, tuple[int, int, int, int]] = {
    RegisterFormat.UINT32_ABCD: _ABCD,
    RegisterFormat.UINT32_CDAB: _CDAB,
    RegisterFormat.UINT32_BADC: _BADC,
    RegisterFormat.UINT32_DCBA: _DCBA,
    RegisterFormat.FLOAT_ABCD: _ABCD,
    RegisterFormat.FLOAT_CDAB: _CDAB,
    RegisterFormat.FLOAT_BADC: _BADC,
    RegisterFormat.FLOAT_DCBA: _DCBA,
}


def _invert(perm: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    inv = [0, 0, 0, 0]
    for wire_idx, value_idx in enumerate(perm):
        inv[value_idx] = wire_idx
    return (inv[0], inv[1], inv[2], inv[3])


_INVERSE_PERM: dict[RegisterFormat, tuple[int, int, int, int]] = {
    fmt: _invert(perm) for fmt, perm in _WIRE_PERM.items()
}


def _check_regs(regs: Sequence[int], expected: int) -> None:
    if len(regs) != expected:
        raise ValueError(f"ожидалось регистров: {expected}, получено: {len(regs)}")
    for reg in regs:
        if not isinstance(reg, int) or not 0 <= reg <= 0xFFFF:
            raise ValueError(f"регистр вне диапазона 0..65535: {reg!r}")


def _value_to_bytes(value: float, fmt: RegisterFormat) -> bytes:
    if fmt is RegisterFormat.UINT16:
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError(f"для {fmt.value} ожидается int, получено: {type(value).__name__}")
        if not 0 <= value <= 0xFFFF:
            raise ValueError(f"uint16 вне диапазона 0..65535: {value}")
        return value.to_bytes(2, "big")
    if fmt.is_float:
        return struct.pack(">f", float(value))
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"для {fmt.value} ожидается int, получено: {type(value).__name__}")
    if not 0 <= value <= 0xFFFFFFFF:
        raise ValueError(f"uint32 вне диапазона 0..4294967295: {value}")
    return value.to_bytes(4, "big")


def encode(value: float, fmt: RegisterFormat) -> list[int]:
    """Закодировать значение в регистры (1 для UINT16, 2 для 32-битных форматов)."""
    raw = _value_to_bytes(value, fmt)
    if fmt is RegisterFormat.UINT16:
        return [int.from_bytes(raw, "big")]
    perm = _WIRE_PERM[fmt]
    wire = bytes(raw[perm[i]] for i in range(4))
    return [(wire[0] << 8) | wire[1], (wire[2] << 8) | wire[3]]


def decode(regs: Sequence[int], fmt: RegisterFormat) -> int | float:
    """Раскодировать регистры в значение по формату (см. таблицу в HRIR_SPEC.md)."""
    expected = fmt.registers_per_value
    _check_regs(regs, expected)
    wire = b"".join(reg.to_bytes(2, "big") for reg in regs)
    if fmt is RegisterFormat.UINT16:
        return int.from_bytes(wire, "big")
    inv = _INVERSE_PERM[fmt]
    raw = bytes(wire[inv[j]] for j in range(4))
    if fmt.is_float:
        return float(struct.unpack(">f", raw)[0])
    return int.from_bytes(raw, "big")


def clamp_value(value: float, fmt: RegisterFormat) -> int | float:
    """Привести значение к формату регистра (ADR-003, TASK-241).

    uint16: clamp 0..65535; uint32: clamp 0..2^32-1 (дробная часть отбрасывается);
    float: любое значение, кроме NaN/Inf — они заменяются на 0.
    """
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return 0.0 if fmt.is_float else 0
    if fmt.is_float:
        return float(value)
    int_value = int(value)
    hi = 0xFFFF if fmt is RegisterFormat.UINT16 else 0xFFFFFFFF
    return max(0, min(hi, int_value))

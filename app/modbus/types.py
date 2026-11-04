"""Типы карты регистров: области, форматы данных, спецификации слейвов.

Источник истины — docs/HRIR_SPEC.md. Адреса 1-based, как в спецификации Modbus.
Для 32-битных форматов адрес значения — адрес его старшего регистра (N читается
из регистров N и N+1), поэтому диапазон значений идёт с шагом 2.

Тип значения (TASK-717) задаётся поведением (`Behavior.dtype`) и управляет
геометрией: 16-битный тип занимает регистр N, 32-битный — пару N, N+1.
Порядок слов ABCD/CDAB/BADC/DCBA всегда берётся из формата слейва; для пар
slave 1 (формат слейва 16-битный) — ABCD, старшее слово в регистре N.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

Area = Literal["cl", "di", "hr", "ir"]

AREAS: tuple[Area, ...] = ("cl", "di", "hr", "ir")

#: Тип значения поведения (числовые области hr/ir, TASK-717).
ValueDType = Literal["uint16", "int16", "uint32", "int32", "float32"]

VALUE_DTYPES: tuple[ValueDType, ...] = ("uint16", "int16", "uint32", "int32", "float32")

#: Ширина типа в регистрах: 16-битные — один регистр, 32-битные — пара N, N+1.
DTYPE_WIDTH: dict[ValueDType, int] = {
    "uint16": 1,
    "int16": 1,
    "uint32": 2,
    "int32": 2,
    "float32": 2,
}


class RegisterFormat(Enum):
    """Формат значения регистра: тип данных + порядок байтов/слов (A — старший байт)."""

    UINT16 = "uint16"
    INT16 = "int16"
    UINT32_ABCD = "uint32_abcd"
    UINT32_CDAB = "uint32_cdab"
    UINT32_BADC = "uint32_badc"
    UINT32_DCBA = "uint32_dcba"
    INT32_ABCD = "int32_abcd"
    INT32_CDAB = "int32_cdab"
    INT32_BADC = "int32_badc"
    INT32_DCBA = "int32_dcba"
    FLOAT_ABCD = "float_abcd"
    FLOAT_CDAB = "float_cdab"
    FLOAT_BADC = "float_badc"
    FLOAT_DCBA = "float_dcba"

    @property
    def dtype(self) -> ValueDType:
        """Тип значения формата без учёта порядка слов (float_abcd → float32)."""
        prefix, _, _ = self.value.partition("_")
        return ("float32" if prefix == "float" else prefix)  # type: ignore[return-value]

    @property
    def word_order(self) -> str | None:
        """Порядок слов формата ("abcd"/"cdab"/"badc"/"dcba"); 16-битные — None."""
        _, _, order = self.value.partition("_")
        return order or None

    @property
    def is_float(self) -> bool:
        return self.dtype == "float32"

    @property
    def is_signed(self) -> bool:
        return self.dtype in ("int16", "int32")

    @property
    def is_32bit(self) -> bool:
        return DTYPE_WIDTH[self.dtype] == 2

    @property
    def bytes_per_value(self) -> int:
        return 4 if self.is_32bit else 2

    @property
    def registers_per_value(self) -> int:
        return 2 if self.is_32bit else 1


def native_dtype(fmt: RegisterFormat) -> ValueDType:
    """Нативный тип значений слейва с форматом `fmt` (uint16/uint32/float32)."""
    return fmt.dtype


def allowed_dtypes(fmt: RegisterFormat) -> tuple[ValueDType, ...]:
    """Типы значений, допустимые в поведениях слейва (TASK-717).

    16-битная сетка (slave 1) принимает все типы: 32-битный тип занимает
    пару регистров N, N+1. 32-битная сетка (слейвы 2..9) — только 32-битные
    типы: слот уже занимает пару.
    """
    if fmt.registers_per_value == 1:
        return VALUE_DTYPES
    return tuple(dtype for dtype in VALUE_DTYPES if DTYPE_WIDTH[dtype] == 2)


_WIRE_BY_DTYPE_ORDER: dict[tuple[ValueDType, str], RegisterFormat] = {
    ("uint32", "abcd"): RegisterFormat.UINT32_ABCD,
    ("uint32", "cdab"): RegisterFormat.UINT32_CDAB,
    ("uint32", "badc"): RegisterFormat.UINT32_BADC,
    ("uint32", "dcba"): RegisterFormat.UINT32_DCBA,
    ("int32", "abcd"): RegisterFormat.INT32_ABCD,
    ("int32", "cdab"): RegisterFormat.INT32_CDAB,
    ("int32", "badc"): RegisterFormat.INT32_BADC,
    ("int32", "dcba"): RegisterFormat.INT32_DCBA,
    ("float32", "abcd"): RegisterFormat.FLOAT_ABCD,
    ("float32", "cdab"): RegisterFormat.FLOAT_CDAB,
    ("float32", "badc"): RegisterFormat.FLOAT_BADC,
    ("float32", "dcba"): RegisterFormat.FLOAT_DCBA,
}


def wire_format(dtype: ValueDType, slave_fmt: RegisterFormat) -> RegisterFormat:
    """Формат на проводе: тип значения × порядок слов слейва (TASK-717).

    16-битные типы порядка слов не имеют. Для 32-битного типа на slave 1
    (формат слейва 16-битный) порядок слов ABCD: старшее слово в регистре N.
    """
    if DTYPE_WIDTH[dtype] == 1:
        return RegisterFormat.UINT16 if dtype == "uint16" else RegisterFormat.INT16
    return _WIRE_BY_DTYPE_ORDER[(dtype, slave_fmt.word_order or "abcd")]


@dataclass(frozen=True)
class AddressRange:
    """Диапазон адресов [start, stop] с шагом (оба конца включены)."""

    start: int
    stop: int
    step: int = 1

    def __contains__(self, address: int) -> bool:
        return self.start <= address <= self.stop and (address - self.start) % self.step == 0


@dataclass(frozen=True)
class SlaveSpec:
    """Спецификация слейва: формат HR/IR и допустимые адреса областей.

    Пустой кортеж диапазонов = область на слейве отсутствует (чтение/запись → 0x02).
    """

    slave_id: int
    fmt: RegisterFormat
    hr: tuple[AddressRange, ...]
    ir: tuple[AddressRange, ...]
    cl: tuple[AddressRange, ...]
    di: tuple[AddressRange, ...]
    #: Кэш register_ranges (вычисляется один раз: спека неизменяема).
    _reg_ranges: dict[Area, tuple[AddressRange, ...]] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        span = self.fmt.registers_per_value - 1
        computed: dict[Area, tuple[AddressRange, ...]] = {}
        for area in AREAS:
            ranges = self.area_ranges(area)
            computed[area] = (
                ranges
                if area in ("cl", "di")
                else tuple(AddressRange(r.start, r.stop + span) for r in ranges)
            )
        object.__setattr__(self, "_reg_ranges", computed)

    def area_ranges(self, area: Area) -> tuple[AddressRange, ...]:
        mapping: dict[Area, tuple[AddressRange, ...]] = {
            "cl": self.cl,
            "di": self.di,
            "hr": self.hr,
            "ir": self.ir,
        }
        return mapping[area]

    def has_address(self, area: Area, address: int) -> bool:
        return any(address in rng for rng in self.area_ranges(area))

    def register_ranges(self, area: Area) -> tuple[AddressRange, ...]:
        """Сырые адреса регистров/бит, которые видят Modbus-запросы (FC 1..6, 16).

        Сетка адресов значений (area_ranges) для 32-битных форматов идёт с шагом 2,
        но каждое значение занимает 2 регистра, поэтому сырая область — сплошная и
        на registers_per_value - 1 шире. Для cl/di (биты) и slave 10 (UINT16,
        диапазоны заданы сразу в регистрах) совпадает с сеткой значений.
        """
        return self._reg_ranges[area]


_REG16 = AddressRange(1, 1024)  # slave 1: HR/IR 1..1024
_REG32 = AddressRange(1, 1023, step=2)  # slaves 2..9: адреса значений 1,3,...,1023
_BITS = AddressRange(1, 64)  # CL/DI 1..64 (слейвы 1..9)

SLAVE_SPECS: dict[int, SlaveSpec] = {
    1: SlaveSpec(1, RegisterFormat.UINT16, hr=(_REG16,), ir=(_REG16,), cl=(_BITS,), di=(_BITS,)),
    2: SlaveSpec(
        2, RegisterFormat.UINT32_ABCD, hr=(_REG32,), ir=(_REG32,), cl=(_BITS,), di=(_BITS,)
    ),
    3: SlaveSpec(
        3, RegisterFormat.UINT32_CDAB, hr=(_REG32,), ir=(_REG32,), cl=(_BITS,), di=(_BITS,)
    ),
    4: SlaveSpec(
        4, RegisterFormat.UINT32_BADC, hr=(_REG32,), ir=(_REG32,), cl=(_BITS,), di=(_BITS,)
    ),
    5: SlaveSpec(
        5, RegisterFormat.UINT32_DCBA, hr=(_REG32,), ir=(_REG32,), cl=(_BITS,), di=(_BITS,)
    ),
    6: SlaveSpec(
        6, RegisterFormat.FLOAT_ABCD, hr=(_REG32,), ir=(_REG32,), cl=(_BITS,), di=(_BITS,)
    ),
    7: SlaveSpec(
        7, RegisterFormat.FLOAT_CDAB, hr=(_REG32,), ir=(_REG32,), cl=(_BITS,), di=(_BITS,)
    ),
    8: SlaveSpec(
        8, RegisterFormat.FLOAT_BADC, hr=(_REG32,), ir=(_REG32,), cl=(_BITS,), di=(_BITS,)
    ),
    9: SlaveSpec(
        9, RegisterFormat.FLOAT_DCBA, hr=(_REG32,), ir=(_REG32,), cl=(_BITS,), di=(_BITS,)
    ),
    # Slave 10 — только IR: 1..268 — метрики 27 часовых поясов (UINT16),
    # 300..301 — Unix timestamp в формате UINT32_ABCD (см. HRIR_SPEC.md).
    10: SlaveSpec(
        10,
        RegisterFormat.UINT16,
        hr=(),
        ir=(AddressRange(1, 268), AddressRange(300, 301)),
        cl=(),
        di=(),
    ),
}

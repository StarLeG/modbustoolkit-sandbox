"""Типы карты регистров: области, форматы данных, спецификации слейвов.

Источник истины — docs/HRIR_SPEC.md. Адреса 1-based, как в спецификации Modbus.
Для 32-битных форматов адрес значения — адрес его старшего регистра (N читается
из регистров N и N+1), поэтому диапазон значений идёт с шагом 2.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

Area = Literal["cl", "di", "hr", "ir"]

AREAS: tuple[Area, ...] = ("cl", "di", "hr", "ir")


class RegisterFormat(Enum):
    """Формат значения регистра: тип данных + порядок байтов/слов (A — старший байт)."""

    UINT16 = "uint16"
    UINT32_ABCD = "uint32_abcd"
    UINT32_CDAB = "uint32_cdab"
    UINT32_BADC = "uint32_badc"
    UINT32_DCBA = "uint32_dcba"
    FLOAT_ABCD = "float_abcd"
    FLOAT_CDAB = "float_cdab"
    FLOAT_BADC = "float_badc"
    FLOAT_DCBA = "float_dcba"

    @property
    def is_float(self) -> bool:
        return self.value.startswith("float")

    @property
    def is_32bit(self) -> bool:
        return self is not RegisterFormat.UINT16

    @property
    def bytes_per_value(self) -> int:
        return 4 if self.is_32bit else 2

    @property
    def registers_per_value(self) -> int:
        return 2 if self.is_32bit else 1


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

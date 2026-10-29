"""Модель карты адресов одного слейва (TASK-102).

Хранилище значений слейва + валидация адресных окон по `SLAVE_SPECS`.
Адресация — raw-регистров/бит, 1-based, как в `docs/HRIR_SPEC.md`: запрос Modbus
несёт адрес регистра из спецификации без сдвига. Для 32-битных форматов
(слейвы 2..9) каждое значение занимает 2 регистра N и N+1, поэтому сырые
регистры образуют сплошную область 1..1024 (см. `SlaveSpec.register_ranges`).

Хранилище sparse: неинициализированные регистры читаются как 0, биты — как False.
Запись в ir/di разрешена на уровне карты (движок поведений генерирует их
автоматически); запрет записи по Modbus обеспечивает слой FC-адаптера.

Разделение ошибок:
- `ModbusMapError` — переводится в Modbus-exception (код в `exception_code`);
- `ValueError` / `TypeError` — баг вызывающего кода, не протокольная ошибка.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence

from app.modbus.types import SLAVE_SPECS, Area, SlaveSpec

#: Illegal Data Address (0x02) — адрес вне карты слейва.
EXC_ILLEGAL_ADDRESS = 0x02
#: Illegal Data Value (0x03) — некорректная длина окна / значение.
EXC_ILLEGAL_VALUE = 0x03
#: Server Device Failure (0x04) — например, запись в locked-поведение (ADR-003).
EXC_DEVICE_FAILURE = 0x04

_REG_AREAS: tuple[Area, ...] = ("hr", "ir")
_BIT_AREAS: tuple[Area, ...] = ("cl", "di")


class ModbusMapError(Exception):
    """Ошибка карты адресов → Modbus-exception клиенту."""

    def __init__(self, message: str, exception_code: int = EXC_ILLEGAL_ADDRESS) -> None:
        super().__init__(message)
        self.exception_code = exception_code


def check_window(spec: SlaveSpec, area: Area, start: int, count: int) -> None:
    """Проверить, что окно [start, start+count-1] целиком лежит в карте области.

    Окно не должно пересекать «дырки» карты (например, 269..299 на slave 10).
    """
    if count <= 0:
        raise ModbusMapError(f"некорректная длина окна: count={count}", EXC_ILLEGAL_VALUE)
    ranges = spec.register_ranges(area)
    for address in range(start, start + count):
        if not any(address in rng for rng in ranges):
            raise ModbusMapError(f"slave {spec.slave_id}: {area} адрес {address} вне карты")


class SlaveMap:
    """Карта одного слейва: области cl/di (биты) и hr/ir (регистры)."""

    def __init__(
        self,
        slave_id: int,
        ir_reader: Callable[[int, int], list[int]] | None = None,
    ) -> None:
        if slave_id not in SLAVE_SPECS:
            raise ValueError(f"неизвестный slave_id: {slave_id}")
        self.spec: SlaveSpec = SLAVE_SPECS[slave_id]
        self._regs: dict[Area, dict[int, int]] = {"hr": {}, "ir": {}}
        self._bits: dict[Area, dict[int, bool]] = {"cl": {}, "di": {}}
        if ir_reader is None and slave_id == 10:
            # Slave 10: IR вычисляется (часовые пояса, timestamp), не хранится.
            from app.modbus.slave10 import read_registers

            ir_reader = read_registers
        self._ir_reader = ir_reader

    def _check_window(self, area: Area, start: int, count: int) -> None:
        check_window(self.spec, area, start, count)

    def get_registers(self, area: Area, start: int, count: int) -> list[int]:
        """Прочитать `count` регистров области hr/ir, начиная с `start`."""
        if area not in _REG_AREAS:
            raise ValueError(f"{area!r}: не регистровая область (ожидается hr/ir)")
        self._check_window(area, start, count)
        if area == "ir" and self._ir_reader is not None:
            return self._ir_reader(start, count)
        store = self._regs[area]
        return [store.get(address, 0) for address in range(start, start + count)]

    def set_registers(self, area: Area, start: int, values: Sequence[int]) -> None:
        """Записать регистры области hr/ir, начиная с `start`."""
        if area not in _REG_AREAS:
            raise ValueError(f"{area!r}: не регистровая область (ожидается hr/ir)")
        if area == "ir" and self._ir_reader is not None:
            raise ValueError("ir: область только для чтения (значения вычисляются)")
        self._check_window(area, start, len(values))
        store = self._regs[area]
        for offset, value in enumerate(values):
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"регистр должен быть int, получено: {type(value).__name__}")
            if not 0 <= value <= 0xFFFF:
                raise ValueError(f"регистр вне диапазона 0..65535: {value}")
            store[start + offset] = value

    def get_bits(self, area: Area, start: int, count: int) -> list[bool]:
        """Прочитать `count` битов области cl/di, начиная с `start`."""
        if area not in _BIT_AREAS:
            raise ValueError(f"{area!r}: не битовая область (ожидается cl/di)")
        self._check_window(area, start, count)
        store = self._bits[area]
        return [store.get(address, False) for address in range(start, start + count)]

    def set_bits(self, area: Area, start: int, values: Sequence[bool]) -> None:
        """Записать биты области cl/di, начиная с `start`."""
        if area not in _BIT_AREAS:
            raise ValueError(f"{area!r}: не битовая область (ожидается cl/di)")
        self._check_window(area, start, len(values))
        store = self._bits[area]
        for offset, value in enumerate(values):
            if not isinstance(value, bool):
                raise TypeError(f"бит должен быть bool, получено: {type(value).__name__}")
            store[start + offset] = value

"""Slave 10 — вычисляемые IR: метрики времени по 27 часовым поясам (TASK-103).

Раскладка (см. `docs/HRIR_SPEC.md`): для зоны Z (0..26) блок из 10 слотов
`1 + 10*Z .. 10 + 10*Z`, из них 8 метрик и 2 резервных (→ 0); блок карты —
1..268 (резервы 269..270 последней зоны за её пределами). IR 300..301 —
Unix timestamp (UINT32_ABCD, точность ±0.5 с).

Смещение UTC: Z = 0..14 → UTC+00..UTC+14, Z = 15..26 → UTC−01..UTC−12
(offset = Z для Z ≤ 14, иначе 14 − Z). Метрики считаются в местном времени зоны.

Функция `read_registers` — вычисляемый источник IR для `SlaveMap(10)`:
значения не хранятся, при каждой читке считаются от текущего момента.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.modbus.codec import encode
from app.modbus.slave_map import check_window
from app.modbus.types import SLAVE_SPECS, RegisterFormat

#: Число зон (Z = 0..ZONE_COUNT-1).
ZONE_COUNT = 27
#: Слотов в блоке зоны (8 метрик + 2 резерва).
ZONE_SLOTS = 10
#: Начало блока Unix timestamp (UINT32_ABCD): регистры 300..301.
TIMESTAMP_ADDR = 300

# Смещения метрик внутри блока зоны (k = (address - 1) % ZONE_SLOTS).
_K_YEAR = 0
_K_MONTH = 1
_K_DAY = 2
_K_HOUR = 3
_K_MINUTE = 4
_K_SECOND = 5
_K_WEEKDAY = 6  # 1=Пн .. 7=Вс
_K_OFFSET = 7  # смещение UTC в часах, int16 на проводе
# k = 8, 9 — резерв → 0


def zone_offset_hours(zone: int) -> int:
    """Смещение зоны от UTC в часах: Z=0..14 → 0..+14, Z=15..26 → −1..−12."""
    return zone if zone <= 14 else 14 - zone


def _local_datetime(now: datetime, zone: int) -> datetime:
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(UTC) + timedelta(hours=zone_offset_hours(zone))


def _zone_register(address: int, now: datetime) -> int:
    zone, k = divmod(address - 1, ZONE_SLOTS)
    if k in (_K_YEAR, _K_MONTH, _K_DAY, _K_HOUR, _K_MINUTE, _K_SECOND, _K_WEEKDAY, _K_OFFSET):
        local = _local_datetime(now, zone)
        if k == _K_YEAR:
            return local.year
        if k == _K_MONTH:
            return local.month
        if k == _K_DAY:
            return local.day
        if k == _K_HOUR:
            return local.hour
        if k == _K_MINUTE:
            return local.minute
        if k == _K_SECOND:
            return local.second
        if k == _K_WEEKDAY:
            return local.weekday() + 1  # Python: Пн=0 → 1=Пн .. 7=Вс
        return zone_offset_hours(zone) & 0xFFFF  # int16 на проводе (two's complement)
    return 0  # резервные слоты 9 и 10


def _timestamp_registers(now: datetime) -> list[int]:
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    seconds = round(now.timestamp())
    return encode(seconds, RegisterFormat.UINT32_ABCD)


def read_registers(start: int, count: int, *, now: datetime | None = None) -> list[int]:
    """Прочитать `count` регистров IR slave 10, начиная с `start`.

    Окно должно целиком лежать в карте (1..268 или 300..301), иначе —
    `ModbusMapError` (0x02); окно через «дырку» 269..299 недопустимо.
    """
    check_window(SLAVE_SPECS[10], "ir", start, count)
    if now is None:
        now = datetime.now(UTC)
    ts_regs = _timestamp_registers(now) if start + count > TIMESTAMP_ADDR else []
    result: list[int] = []
    for address in range(start, start + count):
        if address >= TIMESTAMP_ADDR:
            result.append(ts_regs[address - TIMESTAMP_ADDR])
        else:
            result.append(_zone_register(address, now))
    return result


__all__ = [
    "TIMESTAMP_ADDR",
    "ZONE_COUNT",
    "ZONE_SLOTS",
    "read_registers",
    "zone_offset_hours",
]

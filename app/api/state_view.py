"""Представление значений слейвов: сетка адресов (value-grid) и decode (TASK-305).

Общее для REST (`GET /api/modbus/state`, снимки) и WS (`/ws/state`, ADR-005 п. 1).
"""
from __future__ import annotations

from typing import Any

from app.modbus.codec import decode
from app.modbus.slave10 import TIMESTAMP_ADDR
from app.modbus.slave_map import SlaveMap
from app.modbus.types import Area, RegisterFormat


def slave10_ir_values(smap: SlaveMap) -> dict[str, int | float | bool]:
    """IR slave 10: 1..268 — uint16-метрики, 300 — Unix timestamp (UINT32_ABCD)."""
    values: dict[str, int | float | bool] = {}
    for address in range(1, 269):
        values[str(address)] = smap.get_registers("ir", address, 1)[0]
    values[str(TIMESTAMP_ADDR)] = decode(
        smap.get_registers("ir", TIMESTAMP_ADDR, 2), RegisterFormat.UINT32_ABCD
    )
    return values


def area_values(smap: SlaveMap, area: Area) -> dict[str, int | float | bool]:
    """Значения области по сетке адресов (TASK-305).

    hr/ir декодируются по формату слейва (для 32-битных — значения 1,3,…,1023),
    cl/di — bool. Неинициализированные регистры читаются как 0 (sparse-хранилище).
    """
    if smap.spec.slave_id == 10 and area == "ir":
        return slave10_ir_values(smap)
    fmt = smap.spec.fmt
    values: dict[str, int | float | bool] = {}
    bit_area = area in ("cl", "di")
    for rng in smap.spec.area_ranges(area):
        for address in range(rng.start, rng.stop + 1, rng.step):
            if bit_area:
                values[str(address)] = smap.get_bits(area, address, 1)[0]
            else:
                raw = smap.get_registers(area, address, fmt.registers_per_value)
                values[str(address)] = decode(raw, fmt)
    return values


def state_payload(
    maps: dict[int, SlaveMap],
    slave: int,
    areas: tuple[Area, ...],
    addresses: tuple[int, ...] | None = None,
) -> dict[str, Any]:
    """Кадр состояния `{"slave", "fmt", "areas"}` (GET /state и /ws/state, ADR-005 п. 2).

    `addresses` — фильтр сетки значений; `None` — вся сетка.
    """
    smap = maps[slave]
    wanted = set(addresses) if addresses is not None else None
    out: dict[str, dict[str, int | float | bool]] = {}
    for area in areas:
        values = area_values(smap, area)
        if wanted is not None:
            values = {k: v for k, v in values.items() if int(k) in wanted}
        out[area] = values
    return {"slave": slave, "fmt": smap.spec.fmt.value, "areas": out}

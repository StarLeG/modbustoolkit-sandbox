"""Представление значений слейвов: сетка адресов (value-grid) и decode (TASK-305).

Общее для REST (`GET /api/modbus/state`, снимки) и WS (`/ws/state`, ADR-005 п. 1).
Декод hr/ir идёт по типу значения привязки (TASK-717): младший регистр пары
N, N+1 в кадр не попадает — значением управляет привязка N.
"""
from __future__ import annotations

from typing import Any

from app.modbus.behavior import BehaviorRegistry, pair_owner, wire_format_for
from app.modbus.codec import decode
from app.modbus.slave10 import TIMESTAMP_ADDR
from app.modbus.slave_map import SlaveMap
from app.modbus.tags import TagStore
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


def area_values(
    smap: SlaveMap,
    area: Area,
    registry: BehaviorRegistry | None = None,
) -> dict[str, int | float | bool]:
    """Значения области по сетке адресов (TASK-305, TASK-717).

    hr/ir декодируются по типу значения привязки (нативный тип слейва, если
    привязка не переопределена); адреса младших регистров пар N, N+1 скрыты.
    cl/di — bool. Неинициализированные регистры читаются как 0 (sparse-хранилище).
    """
    if smap.spec.slave_id == 10 and area == "ir":
        return slave10_ir_values(smap)
    values: dict[str, int | float | bool] = {}
    bit_area = area in ("cl", "di")
    for rng in smap.spec.area_ranges(area):
        for address in range(rng.start, rng.stop + 1, rng.step):
            if bit_area:
                values[str(address)] = smap.get_bits(area, address, 1)[0]
                continue
            if registry is not None and pair_owner(
                registry, smap.spec.slave_id, area, address
            ) is not None:
                continue  # младший регистр пары N, N+1: значение живёт на адресе N
            config = registry.get(smap.spec.slave_id, area, address) if registry else None
            fmt = wire_format_for(config, smap.spec.slave_id) if config else smap.spec.fmt
            raw = smap.get_registers(area, address, fmt.registers_per_value)
            values[str(address)] = decode(raw, fmt)
    return values


def state_payload(
    maps: dict[int, SlaveMap],
    slave: int,
    areas: tuple[Area, ...],
    addresses: tuple[int, ...] | None = None,
    registry: BehaviorRegistry | None = None,
    tags: TagStore | None = None,
) -> dict[str, Any]:
    """Кадр состояния `{"slave", "fmt", "areas", "tags"}` (GET /state и /ws/state, ADR-005 п. 2).

    `addresses` — фильтр сетки значений; `None` — вся сетка. `tags` — только
    пользовательские теги областей кадра `{область: {адрес: тег}}` (TASK-718);
    без тегов поле пустое.
    """
    smap = maps[slave]
    wanted = set(addresses) if addresses is not None else None
    out: dict[str, dict[str, int | float | bool]] = {}
    for area in areas:
        values = area_values(smap, area, registry)
        if wanted is not None:
            values = {k: v for k, v in values.items() if int(k) in wanted}
        out[area] = values
    user_tags = tags.for_areas(slave, areas) if tags is not None else {}
    return {"slave": slave, "fmt": smap.spec.fmt.value, "areas": out, "tags": user_tags}

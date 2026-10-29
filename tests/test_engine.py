"""TASK-202: тик-цикл движка поведений (применение, change-detection, start/stop)."""
from __future__ import annotations

import asyncio

import pytest

from app.modbus.behavior import Behavior, BehaviorRegistry
from app.modbus.codec import encode
from app.modbus.engine import BehaviorEngine
from app.modbus.slave_map import SlaveMap
from app.modbus.types import SLAVE_SPECS, RegisterFormat

MapSet = dict[int, SlaveMap]


def _make_engine(tick_ms: int = 100) -> tuple[BehaviorEngine, BehaviorRegistry, MapSet]:
    registry = BehaviorRegistry()
    maps: MapSet = {slave_id: SlaveMap(slave_id) for slave_id in SLAVE_SPECS}
    return BehaviorEngine(registry, maps, tick_ms=tick_ms), registry, maps


# --- применение значений --------------------------------------------------------------


def test_constant_applies_to_holding() -> None:
    engine, registry, maps = _make_engine()
    registry.set(1, "hr", 5, Behavior("constant", {"value": 42}))
    engine.tick(now=0.0)
    assert maps[1].get_registers("hr", 5, 1) == [42]


def test_constant_encodes_32bit_value() -> None:
    engine, registry, maps = _make_engine()
    registry.set(3, "ir", 1, Behavior("constant", {"value": 0x12345678}))
    engine.tick(now=0.0)
    assert maps[3].get_registers("ir", 1, 2) == encode(0x12345678, RegisterFormat.UINT32_CDAB)


def test_constant_applies_to_coil() -> None:
    engine, registry, maps = _make_engine()
    registry.set(1, "cl", 7, Behavior("constant", {"value": 1}))
    engine.tick(now=0.0)
    assert maps[1].get_bits("cl", 7, 1) == [True]


def test_constant_without_value_defaults_to_zero() -> None:
    engine, registry, maps = _make_engine()
    registry.set(1, "hr", 1, Behavior("constant"))
    registry.set(1, "cl", 1, Behavior("constant"))
    engine.tick(now=0.0)
    assert maps[1].get_registers("hr", 1, 1) == [0]
    assert maps[1].get_bits("cl", 1, 1) == [False]


# --- change-detection (ADR-003 п. 1) ---------------------------------------------------


def test_user_write_survives_ticks_for_constant() -> None:
    """constant применяется один раз: запись пользователя не перетирается."""
    engine, registry, maps = _make_engine()
    registry.set(1, "hr", 5, Behavior("constant", {"value": 0}))
    engine.tick(now=0.0)
    maps[1].set_registers("hr", 5, [0xBEEF])  # запись «пользователя» в обход движка
    engine.tick(now=1.0)
    engine.tick(now=2.0)
    assert maps[1].get_registers("hr", 5, 1) == [0xBEEF]


def test_config_change_reapplies() -> None:
    engine, registry, maps = _make_engine()
    registry.set(1, "hr", 5, Behavior("constant", {"value": 1}))
    engine.tick(now=0.0)
    registry.set(1, "hr", 5, Behavior("constant", {"value": 2}))  # новый объект конфига
    engine.tick(now=1.0)
    assert maps[1].get_registers("hr", 5, 1) == [2]


def test_removed_binding_is_forgotten() -> None:
    engine, registry, maps = _make_engine()
    registry.set(1, "hr", 5, Behavior("constant", {"value": 1}))
    engine.tick(now=0.0)
    registry.remove(1, "hr", 5)
    maps[1].set_registers("hr", 5, [7])
    engine.tick(now=1.0)
    assert maps[1].get_registers("hr", 5, 1) == [7]


def test_tick_without_behaviors_is_noop() -> None:
    engine, _registry, maps = _make_engine()
    engine.tick(now=0.0)
    assert maps[1].get_registers("hr", 1, 1) == [0]
    assert engine.ticks == 1


# --- start / stop ---------------------------------------------------------------------


async def test_start_stop_graceful() -> None:
    engine, registry, _maps = _make_engine(tick_ms=20)
    registry.set(1, "hr", 1, Behavior("constant", {"value": 9}))
    engine.start()
    await asyncio.sleep(0.07)
    await engine.stop()
    assert engine.ticks >= 2
    frozen = engine.ticks
    await asyncio.sleep(0.05)
    assert engine.ticks == frozen  # цикл остановлен
    await engine.stop()  # повторный stop безопасен


async def test_start_twice_rejected() -> None:
    engine, _registry, _maps = _make_engine()
    engine.start()
    with pytest.raises(RuntimeError, match="уже запущен"):
        engine.start()
    await engine.stop()


async def test_async_context_manager() -> None:
    engine, registry, maps = _make_engine(tick_ms=20)
    registry.set(1, "hr", 3, Behavior("constant", {"value": 5}))
    async with engine:
        await asyncio.sleep(0.05)
    assert maps[1].get_registers("hr", 3, 1) == [5]
    assert engine.ticks >= 1

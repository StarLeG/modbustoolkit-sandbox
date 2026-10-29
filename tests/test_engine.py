"""TASK-202: тик-цикл движка поведений (применение, change-detection, start/stop)."""
from __future__ import annotations

import asyncio
import time

import pytest

from app.modbus.behavior import Behavior, BehaviorRegistry
from app.modbus.codec import encode
from app.modbus.engine import BehaviorEngine
from app.modbus.journal import Journal
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


def test_remove_restores_default() -> None:
    """remove() сбрасывает к дефолту: constant 0 применяется один раз, дальше не трогает."""
    engine, registry, maps = _make_engine()
    registry.set(1, "hr", 5, Behavior("constant", {"value": 1}))
    engine.tick(now=0.0)
    assert maps[1].get_registers("hr", 5, 1) == [1]
    assert registry.remove(1, "hr", 5) is not None
    engine.tick(now=1.0)
    assert maps[1].get_registers("hr", 5, 1) == [0]  # дефолт constant 0 применился
    maps[1].set_registers("hr", 5, [7])  # запись «пользователя» в обход движка
    engine.tick(now=2.0)
    assert maps[1].get_registers("hr", 5, 1) == [7]  # дефолт статичен, не перетирает


def test_tick_applies_defaults_without_overrides() -> None:
    engine, _registry, maps = _make_engine()
    engine.tick(now=0.0)
    assert maps[1].get_registers("hr", 1, 1) == [0]  # дефолт HR: constant 0
    assert engine.ticks == 1


def test_default_ir_counter_ticks_once_per_interval() -> None:
    """Дефолтный IR-счётчик: +1 раз в interval_s (инвариант «IR — счётчик 1 Гц»)."""
    engine, _registry, maps = _make_engine()
    engine.tick(now=0.0)
    assert maps[1].get_registers("ir", 1, 1) == [0]
    engine.tick(now=1.0)
    assert maps[1].get_registers("ir", 1, 1) == [1]
    engine.tick(now=1.5)
    assert maps[1].get_registers("ir", 1, 1) == [1]  # внутри интервала — без изменений
    engine.tick(now=2.0)
    assert maps[1].get_registers("ir", 1, 1) == [2]


def test_default_di_counter_bits_pattern() -> None:
    """Дефолтный DI: бит address−1 счётчика секунд."""
    engine, _registry, maps = _make_engine()
    engine.tick(now=0.0)
    assert maps[1].get_bits("di", 1, 2) == [False, False]
    engine.tick(now=1.0)  # 0b01
    assert maps[1].get_bits("di", 1, 2) == [True, False]
    engine.tick(now=2.0)  # 0b10
    assert maps[1].get_bits("di", 1, 2) == [False, True]


# --- start / stop ---------------------------------------------------------------------


async def test_start_stop_graceful() -> None:
    engine, registry, _maps = _make_engine(tick_ms=20)
    registry.set(1, "hr", 1, Behavior("constant", {"value": 9}))
    engine.start()
    # первый тик дорогой (создание генераторов ~11 тыс. дефолтных привязок) — ждём условие
    deadline = time.monotonic() + 5.0
    while engine.ticks < 2 and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
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
        deadline = time.monotonic() + 5.0
        while maps[1].get_registers("hr", 3, 1) != [5] and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
    assert maps[1].get_registers("hr", 3, 1) == [5]
    assert engine.ticks >= 1


# --- TASK-204: ошибки поведений → constant 0 (инвариант 7) -----------------------------


class _BoomGenerator:
    """Генератор, падающий на каждом update() — имитация ошибки поведения."""

    is_static = False

    def update(self, now: float) -> int:
        raise RuntimeError("boom")


def test_behavior_error_falls_back_to_constant_zero() -> None:
    engine, registry, maps = _make_engine()
    registry.set(1, "hr", 5, Behavior("constant", {"value": 5}, locked=True))
    registry.set(1, "hr", 6, Behavior("constant", {"value": 7}))
    engine.tick(now=0.0)
    engine._slots[(1, "hr", 5)].generator = _BoomGenerator()  # имитация падения
    engine.tick(now=1.0)
    assert maps[1].get_registers("hr", 5, 1) == [0]  # constant 0 записан
    fallback = registry.get(1, "hr", 5)
    assert fallback is not None
    assert fallback.kind == "constant"
    assert fallback.params == {"value": 0}
    assert fallback.locked is True  # locked сохраняется
    assert maps[1].get_registers("hr", 6, 1) == [7]  # соседнее поведение живо


def test_behavior_error_on_bit_falls_back_to_false() -> None:
    journal = Journal()
    registry = BehaviorRegistry()
    maps: MapSet = {slave_id: SlaveMap(slave_id) for slave_id in SLAVE_SPECS}
    engine = BehaviorEngine(registry, maps, journal=journal)
    registry.set(1, "cl", 2, Behavior("constant", {"value": 1}))
    engine.tick(now=0.0)
    engine._slots[(1, "cl", 2)].generator = _BoomGenerator()
    engine.tick(now=1.0)
    assert maps[1].get_bits("cl", 2, 1) == [False]
    entry = journal.entries()[0]
    assert entry.action == "behavior_failed"
    assert (entry.slave, entry.area, entry.address) == (1, "cl", 2)
    assert entry.source == "engine"


def test_tick_barrier_survives_write_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Исключение в пути записи не роняет тик-цикл (барьер в tick())."""
    engine, registry, maps = _make_engine()
    registry.set(1, "hr", 5, Behavior("constant", {"value": 5}))
    real_set = maps[1].set_registers

    def broken(area: str, address: int, values: list[int]) -> None:
        if address == 5:  # падает только целевая привязка, не все ~11 тыс. дефолтов
            raise RuntimeError("write failed")
        real_set(area, address, values)

    monkeypatch.setattr(maps[1], "set_registers", broken)
    engine.tick(now=0.0)
    engine.tick(now=1.0)
    assert engine.ticks == 2


def test_generator_creation_failure_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ошибка конструктора генератора → constant 0 (инвариант 7)."""
    engine, registry, maps = _make_engine()
    registry.set(1, "hr", 5, Behavior("constant", {"value": 5}, locked=True))
    from app.modbus import engine as engine_module

    real_create = engine_module.create_generator

    def broken(*args: object, **kwargs: object) -> object:
        if args[1] == (1, "hr", 5):  # только целевая привязка, дефолты создаются штатно
            raise ValueError("плохие параметры")
        return real_create(*args, **kwargs)

    monkeypatch.setattr(engine_module, "create_generator", broken)
    engine.tick(now=0.0)
    assert maps[1].get_registers("hr", 5, 1) == [0]
    fallback = registry.get(1, "hr", 5)
    assert fallback is not None
    assert fallback.kind == "constant"
    assert fallback.params == {"value": 0}
    assert fallback.locked is True

"""TASK-201: модель Behavior и реестр привязок (slave, area, address).

TASK-205: дефолтные поведения слейвов 1..9 материализованы в реестре.
TASK-241: нормализация value-параметров по формату регистра.
"""
from __future__ import annotations

import math

import pytest

from app.modbus.behavior import (
    BEHAVIOR_KINDS,
    Behavior,
    BehaviorRegistry,
    check_binding,
    check_kind_for_area,
    default_behavior,
)
from app.modbus.types import AREAS, SLAVE_SPECS

# --- модель Behavior -------------------------------------------------------------------


def test_behavior_defaults() -> None:
    behavior = Behavior(kind="constant")
    assert behavior.kind == "constant"
    assert behavior.params == {}
    assert behavior.locked is False


def test_behavior_all_kinds_constructible() -> None:
    for kind in BEHAVIOR_KINDS:
        behavior = Behavior(kind=kind, params={"value": 1}, locked=True)  # type: ignore[arg-type]
        assert behavior.kind == kind


def test_behavior_unknown_kind_rejected() -> None:
    with pytest.raises(ValueError, match="неизвестный kind"):
        Behavior(kind="saw_wave")  # type: ignore[arg-type]


def test_behavior_bad_params_type_rejected() -> None:
    with pytest.raises(TypeError, match="params должен быть dict"):
        Behavior(kind="constant", params=[1, 2])  # type: ignore[arg-type]


def test_behavior_bad_locked_type_rejected() -> None:
    with pytest.raises(TypeError, match="locked должен быть bool"):
        Behavior(kind="constant", locked=1)  # type: ignore[arg-type]


# --- валидация привязки ---------------------------------------------------------------


def test_check_binding_valid() -> None:
    check_binding(1, "hr", 1024)  # slave 1: UINT16, адрес 1024 допустим
    check_binding(2, "hr", 1023)  # slave 2: value-grid, нечётный адрес
    check_binding(9, "di", 64)
    check_binding(1, "ir", 1)


@pytest.mark.parametrize(
    ("slave", "area", "address", "match"),
    [
        (0, "hr", 1, "неизвестный slave"),
        (11, "hr", 1, "неизвестный slave"),
        (10, "ir", 1, "slave 10"),
        (1, "zz", 1, "неизвестная область"),
        (1, "hr", 0, "вне карты"),
        (1, "hr", 1025, "вне карты"),
        (1, "di", 65, "вне карты"),
        (2, "hr", 2, "вне карты"),  # чётный адрес: 32-битный формат, value-grid с шагом 2
        (3, "ir", 1024, "вне карты"),  # последний raw-регистр ≠ value-grid адрес
    ],
)
def test_check_binding_rejected(slave: int, area: str, address: int, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        check_binding(slave, area, address)  # type: ignore[arg-type]


def test_check_binding_type_errors() -> None:
    with pytest.raises(TypeError, match="slave должен быть int"):
        check_binding("1", "hr", 1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="address должен быть int"):
        check_binding(1, "hr", 1.5)  # type: ignore[arg-type]


def test_check_kind_for_area() -> None:
    check_kind_for_area("counter", "ir")
    check_kind_for_area("toggle", "cl")
    check_kind_for_area("constant", "hr")
    check_kind_for_area("constant", "di")
    with pytest.raises(ValueError, match="не применим"):
        check_kind_for_area("counter", "cl")
    with pytest.raises(ValueError, match="не применим"):
        check_kind_for_area("toggle", "hr")


# --- реестр ---------------------------------------------------------------------------


def test_registry_set_get_remove() -> None:
    registry = BehaviorRegistry()
    behavior = Behavior(kind="counter", params={"step": 1})
    registry.set(1, "hr", 5, behavior)
    assert registry.get(1, "hr", 5) is behavior
    assert registry.get(1, "hr", 6) is default_behavior(1, "hr", 6)  # не переопределено
    assert registry.remove(1, "hr", 5) is behavior  # сброс к дефолту
    assert registry.get(1, "hr", 5) is default_behavior(1, "hr", 5)
    assert registry.remove(1, "hr", 5) is None  # уже дефолт
    assert registry.get(10, "ir", 1) is None  # slave 10 вне реестра
    assert registry.get(2, "hr", 2) is None  # чётный адрес вне value-grid


def test_registry_set_validates_binding() -> None:
    registry = BehaviorRegistry()
    with pytest.raises(ValueError, match="slave 10"):
        registry.set(10, "ir", 1, Behavior(kind="counter"))
    with pytest.raises(ValueError, match="вне карты"):
        registry.set(2, "hr", 2, Behavior(kind="counter"))
    with pytest.raises(TypeError, match="ожидается Behavior"):
        registry.set(1, "hr", 1, "counter")  # type: ignore[arg-type]


def test_registry_set_validates_kind_area() -> None:
    registry = BehaviorRegistry()
    with pytest.raises(ValueError, match="не применим"):
        registry.set(1, "cl", 1, Behavior(kind="counter"))
    with pytest.raises(ValueError, match="не применим"):
        registry.set(1, "ir", 1, Behavior(kind="toggle"))


def test_registry_reset_all_and_per_slave() -> None:
    registry = BehaviorRegistry()
    registry.set(1, "hr", 1, Behavior(kind="constant"))
    registry.set(1, "cl", 1, Behavior(kind="constant"))
    registry.set(3, "ir", 1, Behavior(kind="counter"))
    assert registry.reset(1) == 2  # только переопределения слейва 1
    assert registry.get(1, "hr", 1) is default_behavior(1, "hr", 1)
    assert registry.get(3, "ir", 1) is not default_behavior(3, "ir", 1)
    assert registry.reset() == 1
    assert registry.reset() == 0  # сбрасывать больше нечего
    assert registry.get(3, "ir", 1) is default_behavior(3, "ir", 1)


def test_registry_is_locked() -> None:
    registry = BehaviorRegistry()
    assert registry.is_locked(1, "hr", 1) is False  # дефолт HR: constant 0, unlocked
    assert registry.is_locked(1, "ir", 1) is True  # дефолт IR: counter, locked
    registry.set(1, "hr", 1, Behavior(kind="counter", locked=True))
    assert registry.is_locked(1, "hr", 1) is True
    registry.set(1, "ir", 1, Behavior(kind="counter", locked=False))
    assert registry.is_locked(1, "ir", 1) is False
    assert registry.is_locked(10, "ir", 1) is False  # slave 10 вне реестра


def test_registry_items_are_identity_stable() -> None:
    """Движок сравнивает конфигурации по идентичности: объект не меняется сам."""
    registry = BehaviorRegistry()
    behavior = Behavior(kind="counter")
    registry.set(2, "ir", 3, behavior)
    assert registry.get(2, "ir", 3) is behavior
    assert registry.get(2, "ir", 3) is behavior  # повторный get — тот же объект
    assert [b for b, _ in registry.items() if b == (2, "ir", 3)] == [(2, "ir", 3)]
    # дефолтный экземпляр разделяется соседними привязками
    assert registry.get(2, "ir", 101) is registry.get(2, "ir", 103)


# --- дефолтные поведения (TASK-205/810, ADR-003 п. 5, ADR-009) --------------------------


def _expected_binding_count() -> int:
    total = 0
    for slave, spec in SLAVE_SPECS.items():
        if slave == 10:
            continue
        for area in AREAS:
            for rng in spec.area_ranges(area):
                total += len(range(rng.start, rng.stop + 1, rng.step))
    return total


def test_defaults_materialized_for_slaves_1_to_9() -> None:
    registry = BehaviorRegistry()
    assert len(registry) == _expected_binding_count()
    for slave in range(1, 10):
        spec = SLAVE_SPECS[slave]
        for area in AREAS:
            for rng in spec.area_ranges(area):
                for address in range(rng.start, rng.stop + 1, rng.step):
                    assert registry.get(slave, area, address) is not None
    assert registry.get(10, "ir", 1) is None  # slave 10 исключён


def test_default_hr_cl_constant_zero_unlocked() -> None:
    registry = BehaviorRegistry()
    # вне демо-слотов HR (ADR-009): пишется без ограничений, как и весь CL
    for slave, area, address in [(1, "hr", 1), (1, "cl", 64), (5, "cl", 1), (9, "hr", 1007)]:
        behavior = registry.get(slave, area, address)
        assert behavior is not None
        assert behavior.kind == "constant"
        assert behavior.params == {"value": 0}
        assert behavior.locked is False


def test_default_ir_counter_locked() -> None:
    registry = BehaviorRegistry()
    for slave in range(1, 10):
        behavior = registry.get(slave, "ir", 1)
        assert behavior is not None
        assert behavior.kind == "counter"
        assert behavior.locked is True
        assert behavior.params["step"] == 1
        assert behavior.params["interval_s"] == 1
        # max = предел формата: для UINT16 иначе clamp_value «залипает» на 65535
        assert behavior.params["max"] == (0xFFFF if slave == 1 else 0xFFFF_FFFF)


def test_default_di_random_bit_then_counter_bits_locked() -> None:
    """DI 1..8 — случайный бит (TASK-810), DI 9..64 — биты счётчика секунд."""
    registry = BehaviorRegistry()
    for slave in range(1, 10):
        for address in range(1, 9):
            behavior = registry.get(slave, "di", address)
            assert behavior == Behavior("random_bit", {"p": 0.5}, locked=True)
        for address in range(9, 65):
            behavior = registry.get(slave, "di", address)
            assert behavior is not None
            assert behavior.kind == "counter_bits"
            assert behavior.locked is True


def test_default_ir_showcase_kinds_locked() -> None:
    """Слоты 2..12 IR — витрина всех числовых поведений (TASK-810, ADR-009)."""
    registry = BehaviorRegistry()
    kinds = [
        "sawtooth",
        "triangle",
        "meander",
        "sine",
        "random_uniform",
        "random_walk",
        "step",
        "countdown",
        "ramp_oneshot",
        "follow",
        "constant",
    ]
    for slave in range(1, 10):
        for offset, kind in enumerate(kinds):
            address = offset + 2 if slave == 1 else 2 * (offset + 2) - 1
            behavior = registry.get(slave, "ir", address)
            assert behavior is not None
            assert behavior.kind == kind, (slave, address)
            assert behavior.locked is True
        tail = 13 if slave == 1 else 2 * 13 - 1  # слот 13 — снова счётчик
        assert registry.get(slave, "ir", tail).kind == "counter"


def test_default_hr_demo_locked_at_top_slots() -> None:
    """Последние 8 слотов HR каждого слейва — демо-генераторы, запись блокируется."""
    registry = BehaviorRegistry()
    demo_kinds = [
        "counter",
        "sawtooth",
        "triangle",
        "meander",
        "sine",
        "random_uniform",
        "random_walk",
        "step",
    ]
    top = {1: range(1017, 1025), **{slave: range(1009, 1024, 2) for slave in range(2, 10)}}
    for slave, addresses in top.items():
        assert len(list(addresses)) == 8
        for address, kind in zip(addresses, demo_kinds, strict=True):
            behavior = registry.get(slave, "hr", address)
            assert behavior is not None
            assert behavior.kind == kind, (slave, address)
            assert behavior.locked is True


def test_default_instances_shared() -> None:
    """Один экземпляр Behavior-дефолта разделяется многими привязками (ADR-003 п. 5)."""
    registry = BehaviorRegistry()
    assert registry.get(1, "ir", 1) is registry.get(1, "ir", 1023)
    assert registry.get(2, "ir", 1) is registry.get(9, "ir", 1023)  # 32-битные делят один
    assert registry.get(1, "ir", 1) is not registry.get(2, "ir", 1)  # 16/32 бит — разные
    assert registry.get(1, "cl", 1) is registry.get(1, "hr", 1)
    assert registry.get(1, "di", 1) is registry.get(9, "di", 8)  # random_bit общий
    assert registry.get(1, "di", 9) is registry.get(9, "di", 64)  # counter_bits общий
    assert registry.get(1, "ir", 2) is registry.get(5, "ir", 3)  # слот 2 витрины общий
    assert registry.get(1, "hr", 1017) is registry.get(2, "hr", 1009)  # демо-HR общее


def test_default_behavior_validates_binding() -> None:
    with pytest.raises(ValueError, match="slave 10"):
        default_behavior(10, "ir", 1)
    with pytest.raises(ValueError, match="вне карты"):
        default_behavior(2, "hr", 2)


def test_remove_and_reset_touch_only_overrides() -> None:
    registry = BehaviorRegistry()
    assert registry.remove(1, "hr", 1) is None  # не переопределено
    assert registry.reset() == 0  # чистый реестр — сбрасывать нечего
    registry.set(4, "di", 2, Behavior("constant", {"value": 1}))
    assert registry.reset(4) == 1
    assert registry.get(4, "di", 2) is default_behavior(4, "di", 2)


# --- нормализация параметров (TASK-241) -------------------------------------------------


def _params(registry: BehaviorRegistry, slave: int, area: str, address: int) -> dict:
    behavior = registry.get(slave, area, address)  # type: ignore[arg-type]
    assert behavior is not None
    return behavior.params


def test_normalize_clamps_uint16_params() -> None:
    registry = BehaviorRegistry()  # slave 1: UINT16
    registry.set(1, "hr", 5, Behavior("constant", {"value": 70000}))
    assert _params(registry, 1, "hr", 5) == {"value": 65535}
    registry.set(1, "hr", 6, Behavior("constant", {"value": -5}))
    assert _params(registry, 1, "hr", 6) == {"value": 0}
    registry.set(1, "ir", 1, Behavior("sawtooth", {"min": -10, "max": 70000}))
    assert _params(registry, 1, "ir", 1) == {"min": 0, "max": 65535}


def test_normalize_clamps_uint32_params() -> None:
    registry = BehaviorRegistry()  # slave 2: UINT32_ABCD
    registry.set(2, "hr", 1, Behavior("constant", {"value": 2**32}))
    assert _params(registry, 2, "hr", 1) == {"value": 0xFFFF_FFFF}
    registry.set(2, "ir", 1, Behavior("countdown", {"start": -1, "min": -7}))
    assert _params(registry, 2, "ir", 1) == {"start": 0, "min": 0}


def test_normalize_float_nan_inf_to_zero() -> None:
    registry = BehaviorRegistry()  # slave 6: FLOAT_ABCD
    registry.set(6, "hr", 1, Behavior("constant", {"value": math.nan}))
    assert _params(registry, 6, "hr", 1) == {"value": 0.0}
    registry.set(6, "hr", 3, Behavior("constant", {"value": math.inf}))
    assert _params(registry, 6, "hr", 3) == {"value": 0.0}
    registry.set(6, "hr", 5, Behavior("constant", {"value": 3.5}))  # обычное float — как есть
    assert _params(registry, 6, "hr", 5) == {"value": 3.5}


def test_normalize_truncates_fractional_for_int_formats() -> None:
    registry = BehaviorRegistry()  # uint16: дробная часть отбрасывается (как в clamp_value)
    registry.set(1, "hr", 5, Behavior("constant", {"value": 3.7}))
    assert _params(registry, 1, "hr", 5) == {"value": 3}


def test_normalize_step_values_list() -> None:
    registry = BehaviorRegistry()
    registry.set(1, "hr", 5, Behavior("step", {"values": [0, 70000, -1]}))
    assert _params(registry, 1, "hr", 5) == {"values": [0, 65535, 0]}


def test_normalize_ramp_oneshot_from_to_then() -> None:
    registry = BehaviorRegistry()
    registry.set(1, "hr", 5, Behavior("ramp_oneshot", {"from": -1, "to": 100000, "then": "hold"}))
    assert _params(registry, 1, "hr", 5) == {"from": 0, "to": 65535, "then": "hold"}
    registry.set(1, "hr", 6, Behavior("ramp_oneshot", {"then": 70000}))
    assert _params(registry, 1, "hr", 6) == {"then": 65535}


def test_normalize_keeps_identity_when_unchanged() -> None:
    """Нормализованный объект не создаётся зря: движок сравнивает по идентичности."""
    registry = BehaviorRegistry()
    behavior = Behavior("counter", {"step": 1})  # step — не значение регистра
    registry.set(1, "hr", 5, behavior)
    assert registry.get(1, "hr", 5) is behavior
    empty = Behavior("constant")
    registry.set(1, "hr", 6, empty)
    assert registry.get(1, "hr", 6) is empty


def test_normalize_keeps_non_value_params() -> None:
    registry = BehaviorRegistry()
    behavior = Behavior("counter", {"start": 5, "step": 2.5, "interval_s": 2})
    registry.set(1, "ir", 1, behavior)  # start в диапазоне, step/interval_s не трогаются
    assert registry.get(1, "ir", 1) is behavior


def test_normalize_skips_discrete_areas() -> None:
    registry = BehaviorRegistry()
    behavior = Behavior("constant", {"value": 5})  # битовая область: формата регистра нет
    registry.set(1, "cl", 1, behavior)
    assert registry.get(1, "cl", 1) is behavior


def test_normalize_preserves_locked() -> None:
    registry = BehaviorRegistry()
    registry.set(1, "hr", 5, Behavior("constant", {"value": 70000}, locked=True))
    behavior = registry.get(1, "hr", 5)
    assert behavior is not None
    assert behavior.params == {"value": 65535}
    assert behavior.locked is True

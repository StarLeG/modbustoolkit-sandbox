"""TASK-201: модель Behavior и реестр привязок (slave, area, address).

TASK-205: дефолтные поведения слейвов 1..9 материализованы в реестре.
"""
from __future__ import annotations

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
    assert registry.get(2, "ir", 5) is registry.get(2, "ir", 7)


# --- дефолтные поведения (TASK-205, ADR-003 п. 5) ---------------------------------------


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
    for slave, area, address in [(1, "hr", 1), (1, "cl", 64), (5, "cl", 1), (9, "hr", 1023)]:
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


def test_default_di_counter_bits_locked() -> None:
    registry = BehaviorRegistry()
    for slave in range(1, 10):
        behavior = registry.get(slave, "di", 1)
        assert behavior is not None
        assert behavior.kind == "counter_bits"
        assert behavior.locked is True


def test_default_instances_shared() -> None:
    """Один экземпляр Behavior-дефолта разделяется многими привязками (ADR-003 п. 5)."""
    registry = BehaviorRegistry()
    assert registry.get(1, "ir", 1) is registry.get(1, "ir", 1023)
    assert registry.get(2, "ir", 1) is registry.get(9, "ir", 1023)  # 32-битные делят один
    assert registry.get(1, "ir", 1) is not registry.get(2, "ir", 1)  # 16/32 бит — разные
    assert registry.get(1, "cl", 1) is registry.get(1, "hr", 1)
    assert registry.get(1, "di", 1) is registry.get(9, "di", 64)


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

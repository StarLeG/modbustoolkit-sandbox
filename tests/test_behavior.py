"""TASK-201: модель Behavior и реестр привязок (slave, area, address)."""
from __future__ import annotations

import pytest

from app.modbus.behavior import (
    BEHAVIOR_KINDS,
    Behavior,
    BehaviorRegistry,
    check_binding,
    check_kind_for_area,
)

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
    assert registry.get(1, "hr", 6) is None
    assert registry.remove(1, "hr", 5) is behavior
    assert registry.get(1, "hr", 5) is None
    assert registry.remove(1, "hr", 5) is None


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
    assert registry.reset(1) == 2
    assert registry.get(1, "hr", 1) is None
    assert registry.get(3, "ir", 1) is not None
    assert registry.reset() == 1
    assert len(registry) == 0
    assert registry.reset() == 0


def test_registry_is_locked() -> None:
    registry = BehaviorRegistry()
    assert registry.is_locked(1, "hr", 1) is False  # привязки нет
    registry.set(1, "hr", 1, Behavior(kind="constant", locked=False))
    assert registry.is_locked(1, "hr", 1) is False
    registry.set(1, "hr", 1, Behavior(kind="counter", locked=True))
    assert registry.is_locked(1, "hr", 1) is True


def test_registry_items_are_identity_stable() -> None:
    """Движок сравнивает конфигурации по идентичности: объект не меняется сам."""
    registry = BehaviorRegistry()
    behavior = Behavior(kind="counter")
    registry.set(2, "ir", 3, behavior)
    assert registry.get(2, "ir", 3) is behavior
    assert list(registry.items()) == [((2, "ir", 3), behavior)]

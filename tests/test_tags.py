"""TASK-718: пользовательские теги регистров (TagStore, validate_tag).

Теги общие (shared last-write-wins), живут в памяти (инвариант 9), валидация
привязки — по карте SLAVE_SPECS.
"""
from __future__ import annotations

import pytest

from app.modbus.tags import MAX_TAG_LENGTH, TagStore, validate_tag

# --- validate_tag ------------------------------------------------------------------------


def test_validate_strips_whitespace() -> None:
    assert validate_tag(1, "hr", 5, "  Насос 1  ") == "Насос 1"


def test_validate_rejects_empty() -> None:
    with pytest.raises(ValueError, match="не может быть пустым"):
        validate_tag(1, "hr", 5, "   ")


def test_validate_rejects_too_long() -> None:
    with pytest.raises(ValueError, match="длиннее"):
        validate_tag(1, "hr", 5, "х" * (MAX_TAG_LENGTH + 1))
    assert validate_tag(1, "hr", 5, "х" * MAX_TAG_LENGTH)  # ровно по потолку — ок


def test_validate_rejects_unknown_slave() -> None:
    with pytest.raises(ValueError, match="неизвестный slave"):
        validate_tag(11, "hr", 1, "тег")
    with pytest.raises(ValueError, match="неизвестный slave"):
        validate_tag(0, "hr", 1, "тег")


def test_validate_rejects_address_outside_map() -> None:
    with pytest.raises(ValueError, match="вне карты регистров"):
        validate_tag(2, "hr", 2, "тег")  # чётный адрес — не сетка значений slave 2
    with pytest.raises(ValueError, match="вне карты регистров"):
        validate_tag(1, "hr", 1025, "тег")  # за пределами 1..1024
    with pytest.raises(ValueError, match="вне карты регистров"):
        validate_tag(10, "hr", 1, "тег")  # slave 10 — только ir
    with pytest.raises(ValueError, match="вне карты регистров"):
        validate_tag(10, "ir", 302, "тег")  # «дырка» за пределами карты slave 10


# --- TagStore ----------------------------------------------------------------------------


def test_set_get_clear_round_trip() -> None:
    store = TagStore()
    assert store.get(1, "hr", 5) is None
    assert store.set(1, "hr", 5, "Насос 1") == "Насос 1"
    assert store.get(1, "hr", 5) == "Насос 1"
    assert len(store) == 1
    assert store.clear(1, "hr", 5) is True
    assert store.get(1, "hr", 5) is None
    assert len(store) == 0


def test_clear_missing_returns_false() -> None:
    store = TagStore()
    assert store.clear(1, "hr", 5) is False
    assert store.clear(1, "hr", 5) is False  # идемпотентно


def test_set_overwrites_last_write_wins() -> None:
    store = TagStore()
    store.set(1, "hr", 5, "Первый")
    store.set(1, "hr", 5, "Второй")
    assert store.get(1, "hr", 5) == "Второй"
    assert len(store) == 1


def test_bindings_are_independent() -> None:
    store = TagStore()
    store.set(1, "hr", 5, "HR-5")
    store.set(1, "ir", 5, "IR-5")
    store.set(2, "hr", 5, "Slave2-5")
    assert store.get(1, "hr", 5) == "HR-5"
    assert store.get(1, "ir", 5) == "IR-5"
    assert store.get(2, "hr", 5) == "Slave2-5"
    assert len(store) == 3


def test_for_area_returns_address_map() -> None:
    store = TagStore()
    store.set(1, "hr", 5, "HR-5")
    store.set(1, "di", 2, "DI-2")
    store.set(2, "hr", 5, "Slave2-5")
    assert store.for_area(1, "hr") == {"5": "HR-5"}
    assert store.for_area(1, "di") == {"2": "DI-2"}
    assert store.for_area(2, "hr") == {"5": "Slave2-5"}
    assert store.for_area(1, "cl") == {}
    assert store.for_area(3, "hr") == {}


def test_for_areas_keeps_areas_independent() -> None:
    """Теги одного адреса в разных областях не сталкиваются (cl:5 и hr:5)."""
    store = TagStore()
    store.set(1, "hr", 5, "HR-5")
    store.set(1, "cl", 5, "CL-5")
    store.set(1, "di", 2, "DI-2")
    assert store.for_areas(1, ("cl", "di", "hr", "ir")) == {
        "cl": {"5": "CL-5"},
        "di": {"2": "DI-2"},
        "hr": {"5": "HR-5"},
    }


def test_snapshot_groups_by_slave_and_area() -> None:
    store = TagStore()
    store.set(1, "hr", 5, "HR-5")
    store.set(1, "hr", 7, "HR-7")
    store.set(10, "ir", 1, "Время")
    assert store.snapshot() == {
        "1": {"hr": {"5": "HR-5", "7": "HR-7"}},
        "10": {"ir": {"1": "Время"}},
    }


def test_set_validates_binding() -> None:
    store = TagStore()
    with pytest.raises(ValueError, match="вне карты регистров"):
        store.set(2, "hr", 2, "тег")
    assert len(store) == 0  # ничего не сохранено

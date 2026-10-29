"""Генераторы значений поведений (TASK-211…236).

Контракт (ADR-003 п. 7): `update(now)` — функция времени (секунды от создания
генератора) и внутреннего состояния; возвращает текущее значение поведения
(int | float для hr/ir, bool для cl/di). `is_static=True` — значение фиксировано
(движок пишет его в карту один раз и не перетирает пользовательские записи).
"""
from __future__ import annotations

import random
from typing import Protocol

from app.modbus.behavior import Behavior, Binding


class GeneratorContext(Protocol):
    """Доступ генератора к текущим значениям карт (поведение `follow`)."""

    def read_value(self, binding: Binding) -> int | float: ...

    def read_bit(self, binding: Binding) -> bool: ...


class ValueGenerator(Protocol):
    """Генератор значения одного поведения."""

    is_static: bool

    def update(self, now: float) -> int | float | bool: ...


class ConstantGenerator:
    """`constant`: фиксированное значение (0 по умолчанию)."""

    def __init__(self, value: float | bool) -> None:
        self.is_static = True
        self._value = value

    def update(self, now: float) -> int | float | bool:
        _ = now
        return self._value


def create_generator(
    behavior: Behavior,
    binding: Binding,
    ctx: GeneratorContext,
    rng: random.Random | None = None,
) -> ValueGenerator:
    """Создать генератор по конфигурации поведения (фабрика kind'ов)."""
    _ = ctx, rng
    area = binding[1]
    if behavior.kind == "constant":
        raw = behavior.params.get("value", 0)
        if area in ("cl", "di"):
            return ConstantGenerator(bool(raw))
        return ConstantGenerator(raw if isinstance(raw, (int, float)) else 0)
    raise ValueError(f"поведение {behavior.kind!r} ещё не реализовано")

"""Генераторы значений поведений (TASK-211…236).

Контракт (ADR-003 п. 7): `update(now)` — функция времени (секунды от создания
генератора) и внутреннего состояния; возвращает текущее значение поведения
(int | float для hr/ir, bool для cl/di). `is_static=True` — значение фиксировано
(движок пишет его в карту один раз и не перетирает пользовательские записи).
"""
from __future__ import annotations

import math
import random
from typing import Any, Protocol

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


def _num(params: dict[str, Any], name: str, default: float) -> int | float:
    """Числовой параметр; bool и не-числа отклоняются (см. TASK-241)."""
    raw = params.get(name, default)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise TypeError(f"параметр {name!r} должен быть числом, получено: {raw!r}")
    return raw


def _positive(params: dict[str, Any], name: str, default: float) -> int | float:
    value = _num(params, name, default)
    if value <= 0:
        raise ValueError(f"параметр {name!r} должен быть > 0, получено: {value!r}")
    return value


def _flag(params: dict[str, Any], name: str, default: bool) -> bool:
    raw = params.get(name, default)
    if not isinstance(raw, bool):
        raise TypeError(f"параметр {name!r} должен быть bool, получено: {raw!r}")
    return raw


class CounterGenerator:
    """`counter`: start + step * floor(now / interval_s), wrap/clamp в [min, max].

    `wrap=True` — цикл после max (при step < 0 — после min возвращается к max);
    `wrap=False` — насыщение на границе. `interval_s` — период шага (ADR-003 п. 2,
    по умолчанию 1.0 — «IR — счётчик 1 Гц»).
    """

    is_static = False

    def __init__(
        self,
        params: dict[str, Any],
        binding: Binding,
        ctx: GeneratorContext,
        rng: random.Random,
    ) -> None:
        _ = binding, ctx, rng
        self._start = _num(params, "start", 0)
        self._step = _num(params, "step", 1)
        self._min = _num(params, "min", 0)
        self._max = _num(params, "max", 2**32 - 1)
        self._wrap = _flag(params, "wrap", True)
        self._interval = _positive(params, "interval_s", 1.0)
        if self._max < self._min:
            raise ValueError(f"counter: max {self._max!r} < min {self._min!r}")

    def update(self, now: float) -> int | float:
        n = math.floor(now / self._interval)
        raw = self._start + self._step * n
        if not self._wrap:
            return min(max(raw, self._min), self._max)
        span = self._max - self._min + abs(self._step)
        if span == 0:
            return self._min
        value = self._min + ((raw - self._min) % span)
        return min(max(value, self._min), self._max)


def create_generator(
    behavior: Behavior,
    binding: Binding,
    ctx: GeneratorContext,
    rng: random.Random | None = None,
) -> ValueGenerator:
    """Создать генератор по конфигурации поведения (фабрика kind'ов)."""
    if rng is None:
        rng = random.Random()
    params = behavior.params
    area = binding[1]
    if behavior.kind == "constant":
        raw = params.get("value", 0)
        if area in ("cl", "di"):
            return ConstantGenerator(bool(raw))
        return ConstantGenerator(raw if isinstance(raw, (int, float)) else 0)
    if behavior.kind == "counter":
        return CounterGenerator(params, binding, ctx, rng)
    raise ValueError(f"поведение {behavior.kind!r} ещё не реализовано")

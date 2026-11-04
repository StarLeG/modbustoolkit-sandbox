"""Генераторы значений поведений (TASK-211…236).

Контракт (ADR-003 п. 7): `update(now)` — функция времени (секунды от создания
генератора) и внутреннего состояния; возвращает текущее значение поведения
(int | float для hr/ir, bool для cl/di). `is_static=True` — значение фиксировано
(движок пишет его в карту один раз и не перетирает пользовательские записи).
"""
from __future__ import annotations

import math
import random
from typing import Any, Literal, Protocol, cast

from app.modbus.behavior import Behavior, Binding, check_binding, effective_dtype
from app.modbus.types import Area, ValueDType


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


class SawtoothGenerator:
    """`sawtooth`: линейная пила min→max за `period_s`, повтор; `phase` — доля периода."""

    is_static = False

    def __init__(
        self,
        params: dict[str, Any],
        binding: Binding,
        ctx: GeneratorContext,
        rng: random.Random,
    ) -> None:
        _ = binding, ctx, rng
        self._min = _num(params, "min", 0)
        self._max = _num(params, "max", 100)
        self._period = _positive(params, "period_s", 10.0)
        self._phase = _num(params, "phase", 0.0)
        if self._max < self._min:
            raise ValueError(f"sawtooth: max {self._max!r} < min {self._min!r}")

    def update(self, now: float) -> int | float:
        frac = (now / self._period + self._phase) % 1.0
        return self._min + (self._max - self._min) * frac


class TriangleGenerator:
    """`triangle`: min→max→min за `period_s`; `phase` — доля периода."""

    is_static = False

    def __init__(
        self,
        params: dict[str, Any],
        binding: Binding,
        ctx: GeneratorContext,
        rng: random.Random,
    ) -> None:
        _ = binding, ctx, rng
        self._min = _num(params, "min", 0)
        self._max = _num(params, "max", 100)
        self._period = _positive(params, "period_s", 10.0)
        self._phase = _num(params, "phase", 0.0)
        if self._max < self._min:
            raise ValueError(f"triangle: max {self._max!r} < min {self._min!r}")

    def update(self, now: float) -> int | float:
        frac = (now / self._period + self._phase) % 1.0
        span = self._max - self._min
        if frac < 0.5:
            return self._min + span * (frac * 2)
        return self._max - span * ((frac - 0.5) * 2)


class MeanderGenerator:
    """`meander`: max в первые `duty` доли периода, иначе min (меандр/прямоугольник)."""

    is_static = False

    def __init__(
        self,
        params: dict[str, Any],
        binding: Binding,
        ctx: GeneratorContext,
        rng: random.Random,
    ) -> None:
        _ = binding, ctx, rng
        self._min = _num(params, "min", 0)
        self._max = _num(params, "max", 100)
        self._period = _positive(params, "period_s", 10.0)
        self._duty = _num(params, "duty", 0.5)
        self._phase = _num(params, "phase", 0.0)
        if self._max < self._min:
            raise ValueError(f"meander: max {self._max!r} < min {self._min!r}")
        if not 0 <= self._duty <= 1:
            raise ValueError(f"meander: duty должен быть в 0..1, получено: {self._duty!r}")

    def update(self, now: float) -> int | float:
        frac = (now / self._period + self._phase) % 1.0
        return self._max if frac < self._duty else self._min


class SineGenerator:
    """`sine`: синусоида `(min+max)/2 + (max-min)/2 * sin(2π(now/period_s + phase))`."""

    is_static = False

    def __init__(
        self,
        params: dict[str, Any],
        binding: Binding,
        ctx: GeneratorContext,
        rng: random.Random,
    ) -> None:
        _ = binding, ctx, rng
        self._min = _num(params, "min", 0)
        self._max = _num(params, "max", 100)
        self._period = _positive(params, "period_s", 10.0)
        self._phase = _num(params, "phase", 0.0)
        if self._max < self._min:
            raise ValueError(f"sine: max {self._max!r} < min {self._min!r}")
        self._mid = (self._min + self._max) / 2
        self._amp = (self._max - self._min) / 2

    def update(self, now: float) -> int | float:
        return self._mid + self._amp * math.sin(2 * math.pi * (now / self._period + self._phase))


class RandomUniformGenerator:
    """`random_uniform`: равномерное случайное значение в [min, max] на каждом update.

    Математика по типу значения (TASK-717): для целых типов — `rng.randint`
    (включительно), для float32 — `rng.uniform` (дробные значения).
    """

    is_static = False

    def __init__(
        self,
        params: dict[str, Any],
        binding: Binding,
        ctx: GeneratorContext,
        rng: random.Random,
        *,
        dtype: ValueDType | None = None,
    ) -> None:
        _ = binding, ctx
        self._rng = rng
        self._min = _num(params, "min", 0)
        self._max = _num(params, "max", 100)
        if self._max < self._min:
            raise ValueError(f"random_uniform: max {self._max!r} < min {self._min!r}")
        self._integers = dtype != "float32" and isinstance(self._min, int) and isinstance(
            self._max, int
        )

    def update(self, now: float) -> int | float:
        _ = now
        if self._integers:
            return self._rng.randint(int(self._min), int(self._max))
        return self._rng.uniform(self._min, self._max)


class RandomWalkGenerator:
    """`random_walk`: блуждание `pos += U(-step, step) + mean_reversion * (center - pos)`.

    Стартовая точка — центр `[min, max]`; после каждого шага позиция клампится
    в `[min, max]`.
    """

    is_static = False

    def __init__(
        self,
        params: dict[str, Any],
        binding: Binding,
        ctx: GeneratorContext,
        rng: random.Random,
    ) -> None:
        _ = binding, ctx
        self._rng = rng
        self._min = _num(params, "min", 0)
        self._max = _num(params, "max", 100)
        self._step = _num(params, "step", 1)
        self._mean_rev = _num(params, "mean_reversion", 0.0)
        if self._max < self._min:
            raise ValueError(f"random_walk: max {self._max!r} < min {self._min!r}")
        if self._step < 0:
            raise ValueError(f"random_walk: step должен быть >= 0, получено: {self._step!r}")
        if not 0 <= self._mean_rev <= 1:
            raise ValueError(
                f"random_walk: mean_reversion должен быть в 0..1, получено: {self._mean_rev!r}"
            )
        self._center = (self._min + self._max) / 2
        self._pos = self._center

    def update(self, now: float) -> int | float:
        _ = now
        self._pos += self._rng.uniform(-self._step, self._step)
        self._pos += self._mean_rev * (self._center - self._pos)
        self._pos = min(max(self._pos, self._min), self._max)
        return self._pos


class RampOneshotGenerator:
    """`ramp_oneshot`: линейный переход от `from` к `to` за `duration_s`, затем `then`.

    `then`: `"hold"` — держать `to`; `"loop"` — повторять ramp по кругу;
    число — перейти на эту константу.
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
        self._start = _num(params, "from", 0)
        self._to = _num(params, "to", 100)
        self._duration = _positive(params, "duration_s", 10.0)
        then = params.get("then", "hold")
        self._mode: Literal["hold", "loop", "value"]
        self._then_value: int | float = 0
        if isinstance(then, str):
            if then == "hold":
                self._mode = "hold"
            elif then == "loop":
                self._mode = "loop"
            else:
                raise ValueError(
                    f"ramp_oneshot: then должен быть 'hold', 'loop' или числом, получено: {then!r}"
                )
        elif isinstance(then, bool) or not isinstance(then, (int, float)):
            raise TypeError(
                f"ramp_oneshot: then должен быть 'hold', 'loop' или числом, получено: {then!r}"
            )
        else:
            self._mode = "value"
            self._then_value = then

    def update(self, now: float) -> int | float:
        if now >= self._duration:
            if self._mode == "loop":
                now = now % self._duration
            elif self._mode == "hold":
                return self._to
            else:
                return self._then_value
        return self._start + (self._to - self._start) * (now / self._duration)


class CountdownGenerator:
    """`countdown`: обратный отсчёт `max(min, start - step * floor(now / interval_s))`.

    Шаг раз в `interval_s` (как у `counter`, ADR-003 п. 2); насыщение на `min`.
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
        self._start = _num(params, "start", 100)
        self._step = _num(params, "step", 1)
        self._min = _num(params, "min", 0)
        self._interval = _positive(params, "interval_s", 1.0)
        if self._step <= 0:
            raise ValueError(f"countdown: step должен быть > 0, получено: {self._step!r}")
        if self._start < self._min:
            raise ValueError(f"countdown: start {self._start!r} < min {self._min!r}")

    def update(self, now: float) -> int | float:
        n = math.floor(now / self._interval)
        return max(self._min, self._start - self._step * n)


class StepGenerator:
    """`step`: циклический перебор списка `values`, смена раз в `interval_s`."""

    is_static = False

    def __init__(
        self,
        params: dict[str, Any],
        binding: Binding,
        ctx: GeneratorContext,
        rng: random.Random,
    ) -> None:
        _ = binding, ctx, rng
        raw = params.get("values", (0, 1))
        if not isinstance(raw, (list, tuple)):
            raise TypeError(f"step: values должен быть списком, получено: {raw!r}")
        values: list[int | float] = []
        for item in raw:
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise TypeError(f"step: элемент values должен быть числом, получено: {item!r}")
            values.append(item)
        if not values:
            raise ValueError("step: values не должен быть пустым")
        self._values = values
        self._interval = _positive(params, "interval_s", 1.0)

    def update(self, now: float) -> int | float:
        n = math.floor(now / self._interval)
        return self._values[n % len(self._values)]


def _parse_source(raw: str, slave: int) -> Binding:
    """Разобрать `source: "hr:15"` в привязку `(slave, area, address)`."""
    area_str, sep, addr_str = raw.partition(":")
    if not sep or not addr_str:
        raise ValueError(f"source должен быть вида 'area:address', получено: {raw!r}")
    if area_str not in ("cl", "di", "hr", "ir"):
        raise ValueError(f"source: неизвестная область {area_str!r}")
    area = cast("Area", area_str)
    try:
        address = int(addr_str)
    except ValueError:
        raise ValueError(f"source: адрес должен быть целым, получено: {addr_str!r}") from None
    check_binding(slave, area, address)
    return (slave, area, address)


class FollowGenerator:
    """`follow`: значение берётся из другого регистра/бита того же слейва.

    `source` — строка вида `"hr:15"` (область и value-grid адрес источника);
    источник cl/di читается как 0/1.
    """

    is_static = False

    def __init__(
        self,
        params: dict[str, Any],
        binding: Binding,
        ctx: GeneratorContext,
        rng: random.Random,
    ) -> None:
        _ = rng
        self._ctx = ctx
        raw = params.get("source")
        if not isinstance(raw, str):
            raise TypeError(
                f"follow: source должен быть строкой 'area:address', получено: {raw!r}"
            )
        self._source = _parse_source(raw, binding[0])

    def update(self, now: float) -> int | float:
        _ = now
        return self._ctx.read_value(self._source)


class ToggleGenerator:
    """`toggle`: меандр 50% — True первые `period_s / 2`, затем False (полный период = `period_s`)."""

    is_static = False

    def __init__(
        self,
        params: dict[str, Any],
        binding: Binding,
        ctx: GeneratorContext,
        rng: random.Random,
    ) -> None:
        _ = binding, ctx, rng
        self._period = _positive(params, "period_s", 1.0)

    def update(self, now: float) -> bool:
        return (now / self._period) % 1.0 < 0.5


class PwmGenerator:
    """`pwm`: True в первые `duty` доли периода, иначе False (скважность бита)."""

    is_static = False

    def __init__(
        self,
        params: dict[str, Any],
        binding: Binding,
        ctx: GeneratorContext,
        rng: random.Random,
    ) -> None:
        _ = binding, ctx, rng
        self._period = _positive(params, "period_s", 1.0)
        self._duty = _num(params, "duty", 0.5)
        if not 0 <= self._duty <= 1:
            raise ValueError(f"pwm: duty должен быть в 0..1, получено: {self._duty!r}")

    def update(self, now: float) -> bool:
        return (now / self._period) % 1.0 < self._duty


class RandomBitGenerator:
    """`random_bit`: True с вероятностью `p` на каждом update."""

    is_static = False

    def __init__(
        self,
        params: dict[str, Any],
        binding: Binding,
        ctx: GeneratorContext,
        rng: random.Random,
    ) -> None:
        _ = binding, ctx
        self._rng = rng
        self._p = _num(params, "p", 0.5)
        if not 0 <= self._p <= 1:
            raise ValueError(f"random_bit: p должен быть в 0..1, получено: {self._p!r}")

    def update(self, now: float) -> bool:
        _ = now
        return self._rng.random() < self._p


class CounterBitsGenerator:
    """`counter_bits`: бит `bit` целочисленного счётчика секунд `(int(now) >> bit) & 1`.

    По умолчанию `bit = address - 1`: бит N меняется раз в 2^N секунд.
    """

    is_static = False

    def __init__(
        self,
        params: dict[str, Any],
        binding: Binding,
        ctx: GeneratorContext,
        rng: random.Random,
    ) -> None:
        _ = ctx, rng
        raw = params.get("bit", binding[2] - 1)
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise TypeError(f"counter_bits: bit должен быть целым, получено: {raw!r}")
        if raw < 0:
            raise ValueError(f"counter_bits: bit должен быть >= 0, получено: {raw!r}")
        self._bit = raw

    def update(self, now: float) -> bool:
        return bool((int(now) >> self._bit) & 1)


class FollowBitGenerator:
    """`follow_bit`: бит `bit` значения из `source` (например `"hr:15"`).

    Источник hr/ir — целое значение, берётся его бит `bit` (по умолчанию 0);
    источник cl/di — сам бит, параметр `bit` игнорируется.
    """

    is_static = False

    def __init__(
        self,
        params: dict[str, Any],
        binding: Binding,
        ctx: GeneratorContext,
        rng: random.Random,
    ) -> None:
        _ = rng
        self._ctx = ctx
        raw = params.get("source")
        if not isinstance(raw, str):
            raise TypeError(
                f"follow_bit: source должен быть строкой 'area:address', получено: {raw!r}"
            )
        self._source = _parse_source(raw, binding[0])
        bit = params.get("bit", 0)
        if isinstance(bit, bool) or not isinstance(bit, int):
            raise TypeError(f"follow_bit: bit должен быть целым, получено: {bit!r}")
        if bit < 0:
            raise ValueError(f"follow_bit: bit должен быть >= 0, получено: {bit!r}")
        self._bit = bit

    def update(self, now: float) -> bool:
        _ = now
        if self._source[1] in ("cl", "di"):
            return self._ctx.read_bit(self._source)
        return bool((int(self._ctx.read_value(self._source)) >> self._bit) & 1)


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
    if behavior.kind == "sawtooth":
        return SawtoothGenerator(params, binding, ctx, rng)
    if behavior.kind == "triangle":
        return TriangleGenerator(params, binding, ctx, rng)
    if behavior.kind == "meander":
        return MeanderGenerator(params, binding, ctx, rng)
    if behavior.kind == "sine":
        return SineGenerator(params, binding, ctx, rng)
    if behavior.kind == "random_uniform":
        dtype = effective_dtype(behavior, binding[0]) if area in ("hr", "ir") else None
        return RandomUniformGenerator(params, binding, ctx, rng, dtype=dtype)
    if behavior.kind == "random_walk":
        return RandomWalkGenerator(params, binding, ctx, rng)
    if behavior.kind == "ramp_oneshot":
        return RampOneshotGenerator(params, binding, ctx, rng)
    if behavior.kind == "countdown":
        return CountdownGenerator(params, binding, ctx, rng)
    if behavior.kind == "step":
        return StepGenerator(params, binding, ctx, rng)
    if behavior.kind == "follow":
        return FollowGenerator(params, binding, ctx, rng)
    if behavior.kind == "toggle":
        return ToggleGenerator(params, binding, ctx, rng)
    if behavior.kind == "pwm":
        return PwmGenerator(params, binding, ctx, rng)
    if behavior.kind == "random_bit":
        return RandomBitGenerator(params, binding, ctx, rng)
    if behavior.kind == "counter_bits":
        return CounterBitsGenerator(params, binding, ctx, rng)
    if behavior.kind == "follow_bit":
        return FollowBitGenerator(params, binding, ctx, rng)
    raise ValueError(f"поведение {behavior.kind!r} ещё не реализовано")

"""Поведения: unit-тесты генераторов (TASK-211…236).

Детерминизм: `update(now)` — функция фиктивного времени; случайные
поведения — с seeded `random.Random`. Проверяются границы и циклы.
"""
from __future__ import annotations

import random

import pytest

from app.modbus.behavior import Behavior, Binding
from app.modbus.generators import ConstantGenerator, create_generator


class FakeCtx:
    """Фиктивный контекст чтения источников для follow-поведений."""

    def __init__(self) -> None:
        self.values: dict[Binding, int | float] = {}
        self.bits: dict[Binding, bool] = {}

    def read_value(self, binding: Binding) -> int | float:
        return self.values.get(binding, 0)

    def read_bit(self, binding: Binding) -> bool:
        return self.bits.get(binding, False)


def _gen(
    kind: str,
    params: dict[str, object] | None = None,
    *,
    slave: int = 1,
    area: str = "hr",
    address: int = 1,
    ctx: FakeCtx | None = None,
    seed: int = 42,
):
    behavior = Behavior(kind, params or {})  # type: ignore[arg-type]
    binding: Binding = (slave, area, address)  # type: ignore[assignment]
    return create_generator(behavior, binding, ctx or FakeCtx(), random.Random(seed))


# --- TASK-211: constant ----------------------------------------------------------------


def test_constant_numeric() -> None:
    gen = _gen("constant", {"value": 42})
    assert isinstance(gen, ConstantGenerator)
    assert gen.is_static is True
    assert gen.update(0.0) == 42
    assert gen.update(99.0) == 42  # значение не зависит от времени


def test_constant_bit() -> None:
    assert _gen("constant", {"value": 1}, area="cl").update(0.0) is True
    assert _gen("constant", {"value": 0}, area="di").update(0.0) is False


def test_constant_default_zero() -> None:
    assert _gen("constant").update(0.0) == 0
    assert _gen("constant", area="cl").update(0.0) is False


# --- TASK-212: counter -----------------------------------------------------------------


def test_counter_default_steps_once_per_second() -> None:
    gen = _gen("counter")
    assert gen.update(0.0) == 0
    assert gen.update(0.99) == 0
    assert gen.update(1.0) == 1
    assert gen.update(2.5) == 2


def test_counter_step_start_interval() -> None:
    gen = _gen("counter", {"start": 10, "step": 5, "interval_s": 0.1})
    assert gen.update(0.0) == 10
    assert gen.update(0.1) == 15
    assert gen.update(0.25) == 20


def test_counter_saturates_without_wrap() -> None:
    gen = _gen("counter", {"start": 4, "step": 1, "min": 0, "max": 5, "wrap": False})
    assert gen.update(1.0) == 5
    assert gen.update(9.0) == 5


def test_counter_wraps_after_max() -> None:
    gen = _gen("counter", {"start": 5, "step": 1, "min": 0, "max": 5, "wrap": True})
    assert gen.update(0.0) == 5
    assert gen.update(1.0) == 0
    assert gen.update(2.0) == 1


def test_counter_negative_step_wraps_to_max() -> None:
    gen = _gen("counter", {"start": 1, "step": -1, "min": 0, "max": 5, "wrap": True})
    assert gen.update(0.0) == 1
    assert gen.update(1.0) == 0
    assert gen.update(2.0) == 5


def test_counter_negative_step_saturates_at_min() -> None:
    gen = _gen("counter", {"start": 1, "step": -1, "min": 0, "max": 5, "wrap": False})
    assert gen.update(2.0) == 0
    assert gen.update(9.0) == 0


def test_counter_float_params() -> None:
    gen = _gen(
        "counter",
        {"start": 0.0, "step": 0.5, "min": 0.0, "max": 1.5, "wrap": True, "interval_s": 1},
    )
    assert gen.update(0.0) == 0.0
    assert gen.update(1.0) == 0.5
    assert gen.update(3.0) == 1.5
    assert gen.update(4.0) == 0.0  # цикл: span = max - min + step = 2.0


def test_counter_interval_must_be_positive() -> None:
    with pytest.raises(ValueError, match="interval_s"):
        _gen("counter", {"interval_s": 0})
    with pytest.raises(ValueError, match="interval_s"):
        _gen("counter", {"interval_s": -1})


def test_counter_min_max_validation() -> None:
    with pytest.raises(ValueError, match="max"):
        _gen("counter", {"min": 5, "max": 1})


# --- TASK-213: sawtooth ----------------------------------------------------------------


def test_sawtooth_ramp_and_repeat() -> None:
    gen = _gen("sawtooth", {"min": 0, "max": 10, "period_s": 4, "phase": 0})
    assert gen.update(0.0) == 0
    assert gen.update(1.0) == 2.5
    assert gen.update(2.0) == 5
    assert gen.update(3.9) == pytest.approx(9.75)
    assert gen.update(4.0) == 0  # цикл повторяется


def test_sawtooth_phase() -> None:
    gen = _gen("sawtooth", {"min": 0, "max": 10, "period_s": 4, "phase": 0.25})
    assert gen.update(0.0) == 2.5


def test_sawtooth_flat_limits() -> None:
    gen = _gen("sawtooth", {"min": 3, "max": 3, "period_s": 2})
    assert gen.update(0.5) == 3


def test_sawtooth_validation() -> None:
    with pytest.raises(ValueError, match="period_s"):
        _gen("sawtooth", {"period_s": 0})
    with pytest.raises(ValueError, match="max"):
        _gen("sawtooth", {"min": 5, "max": 1})


# --- TASK-214: triangle ----------------------------------------------------------------


def test_triangle_wave() -> None:
    gen = _gen("triangle", {"min": 0, "max": 10, "period_s": 4, "phase": 0})
    assert gen.update(0.0) == 0
    assert gen.update(1.0) == 5
    assert gen.update(2.0) == 10
    assert gen.update(3.0) == 5
    assert gen.update(4.0) == 0


def test_triangle_phase() -> None:
    gen = _gen("triangle", {"min": 0, "max": 10, "period_s": 4, "phase": 0.25})
    assert gen.update(0.0) == 5
    assert gen.update(1.0) == 10


def test_triangle_validation() -> None:
    with pytest.raises(ValueError, match="period_s"):
        _gen("triangle", {"period_s": -1})
    with pytest.raises(ValueError, match="max"):
        _gen("triangle", {"min": 5, "max": 1})


# --- TASK-215: meander -----------------------------------------------------------------


def test_meander_duty_half() -> None:
    gen = _gen("meander", {"min": 0, "max": 10, "period_s": 4, "duty": 0.5})
    assert gen.update(0.0) == 10
    assert gen.update(1.9) == 10
    assert gen.update(2.0) == 0
    assert gen.update(3.9) == 0
    assert gen.update(4.0) == 10


def test_meander_duty_quarter() -> None:
    gen = _gen("meander", {"min": 0, "max": 10, "period_s": 4, "duty": 0.25, "phase": 0})
    assert gen.update(0.9) == 10
    assert gen.update(1.1) == 0


def test_meander_duty_extremes() -> None:
    assert _gen("meander", {"min": 0, "max": 10, "duty": 0}).update(0.5) == 0
    assert _gen("meander", {"min": 0, "max": 10, "duty": 1}).update(0.5) == 10


def test_meander_validation() -> None:
    with pytest.raises(ValueError, match="duty"):
        _gen("meander", {"duty": 1.5})
    with pytest.raises(ValueError, match="duty"):
        _gen("meander", {"duty": -0.1})
    with pytest.raises(ValueError, match="period_s"):
        _gen("meander", {"period_s": 0})


# --- TASK-216: sine --------------------------------------------------------------------


def test_sine_wave() -> None:
    gen = _gen("sine", {"min": -10, "max": 10, "period_s": 4, "phase": 0})
    assert gen.update(0.0) == pytest.approx(0)  # sin(0) = 0
    assert gen.update(1.0) == pytest.approx(10)  # sin(π/2) = 1 → max
    assert gen.update(2.0) == pytest.approx(0)  # sin(π) = 0
    assert gen.update(3.0) == pytest.approx(-10)  # sin(3π/2) = -1 → min
    assert gen.update(4.0) == pytest.approx(0)  # цикл


def test_sine_offset_and_amplitude() -> None:
    """min=0, max=100 → ось 50, амплитуда 50."""
    gen = _gen("sine", {"min": 0, "max": 100, "period_s": 4, "phase": 0})
    assert gen.update(0.0) == pytest.approx(50)
    assert gen.update(1.0) == pytest.approx(100)
    assert gen.update(3.0) == pytest.approx(0)


def test_sine_phase() -> None:
    gen = _gen("sine", {"min": -10, "max": 10, "period_s": 4, "phase": 0.25})
    assert gen.update(0.0) == pytest.approx(10)


def test_sine_validation() -> None:
    with pytest.raises(ValueError, match="period_s"):
        _gen("sine", {"period_s": 0})
    with pytest.raises(ValueError, match="max"):
        _gen("sine", {"min": 5, "max": 1})

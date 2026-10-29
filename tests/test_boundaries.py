"""TASK-242: граничные тесты поведений (все 17 kind'ов).

Дополняют `tests/test_generators.py`: вырожденные параметры (min==max, step=0),
точки переключения ровно на границе, экстремальные биты и адреса карты.
Детерминизм — фиктивное время `update(now)` + seeded `random.Random`.
"""
from __future__ import annotations

import random

import pytest

from app.modbus.behavior import Behavior, Binding
from app.modbus.generators import create_generator


class FakeCtx:
    """Фиктивный контекст чтения источников для follow-поведений."""

    def __init__(self) -> None:
        self.values: dict[Binding, int | float] = {}
        self.bits: dict[Binding, bool] = {}

    def read_value(self, binding: Binding) -> int | float:
        if binding[1] in ("cl", "di"):
            return 1 if self.bits.get(binding, False) else 0
        return self.values.get(binding, 0)

    def read_bit(self, binding: Binding) -> bool:
        return self.bits.get(binding, False)


class FakeRng:
    """ГСЧ, выдающий заданные значения random()."""

    def __init__(self, *values: float) -> None:
        self._values = list(values)

    def random(self) -> float:
        return self._values.pop(0) if self._values else 0.5


def _gen(
    kind: str,
    params: dict[str, object] | None = None,
    *,
    slave: int = 1,
    area: str = "hr",
    address: int = 1,
    ctx: FakeCtx | None = None,
    seed: int = 42,
    rng: random.Random | None = None,
):
    behavior = Behavior(kind, params or {})  # type: ignore[arg-type]
    binding: Binding = (slave, area, address)  # type: ignore[assignment]
    return create_generator(
        behavior, binding, ctx or FakeCtx(), rng or random.Random(seed)
    )


# --- constant --------------------------------------------------------------------------


def test_constant_non_numeric_value_is_zero() -> None:
    assert _gen("constant", {"value": "42"}).update(0.0) == 0
    assert _gen("constant", {"value": None}).update(0.0) == 0


def test_constant_bit_truthiness() -> None:
    assert _gen("constant", {"value": 2}, area="cl").update(0.0) is True
    assert _gen("constant", {"value": -1}, area="di").update(0.0) is True
    assert _gen("constant", {"value": 0.0}, area="cl").update(0.0) is False


# --- counter ---------------------------------------------------------------------------


def test_counter_min_equals_max_is_constant() -> None:
    flat_zero = _gen("counter", {"min": 5, "max": 5, "step": 0})
    assert flat_zero.update(0.0) == 5
    assert flat_zero.update(7.0) == 5  # span == 0 → всегда min
    flat = _gen("counter", {"min": 5, "max": 5, "step": 3})
    assert flat.update(0.0) == 5
    assert flat.update(1.0) == 5


def test_counter_start_out_of_range() -> None:
    clamped = _gen("counter", {"start": 10, "min": 0, "max": 5, "wrap": False})
    assert clamped.update(0.0) == 5  # сразу насыщается на max
    wrapped = _gen("counter", {"start": 10, "min": 0, "max": 5, "step": 1, "wrap": True})
    assert wrapped.update(0.0) == 4  # 10 % span(6)


# --- sawtooth --------------------------------------------------------------------------


def test_sawtooth_phase_is_modulo_one() -> None:
    base = _gen("sawtooth", {"min": 0, "max": 10, "period_s": 4, "phase": 0})
    shifted = _gen("sawtooth", {"min": 0, "max": 10, "period_s": 4, "phase": 1.0})
    assert shifted.update(1.0) == base.update(1.0)
    negative = _gen("sawtooth", {"min": 0, "max": 10, "period_s": 4, "phase": -0.25})
    assert negative.update(0.0) == 7.5  # phase -0.25 ≡ 0.75


# --- triangle --------------------------------------------------------------------------


def test_triangle_min_equals_max_is_flat() -> None:
    gen = _gen("triangle", {"min": 3, "max": 3, "period_s": 2})
    assert gen.update(0.0) == 3
    assert gen.update(0.7) == 3
    assert gen.update(1.9) == 3


# --- meander ---------------------------------------------------------------------------


def test_meander_phase_shift_inverts() -> None:
    gen = _gen("meander", {"min": 0, "max": 10, "period_s": 4, "duty": 0.5, "phase": 0.5})
    assert gen.update(0.0) == 0  # frac=0.5 → уже min
    assert gen.update(2.0) == 10  # frac=0.0 → новый период, max


def test_meander_min_equals_max() -> None:
    assert _gen("meander", {"min": 7, "max": 7}).update(0.1) == 7
    assert _gen("meander", {"min": 7, "max": 7}).update(9.9) == 7


# --- sine ------------------------------------------------------------------------------


def test_sine_min_equals_max_is_constant() -> None:
    gen = _gen("sine", {"min": 4, "max": 4, "period_s": 3})
    assert gen.update(0.0) == pytest.approx(4)
    assert gen.update(0.75) == pytest.approx(4)


def test_sine_phase_is_modulo_one() -> None:
    base = _gen("sine", {"min": -1, "max": 1, "period_s": 2, "phase": 0.25})
    shifted = _gen("sine", {"min": -1, "max": 1, "period_s": 2, "phase": 1.25})
    assert shifted.update(0.3) == pytest.approx(base.update(0.3))


# --- random_uniform --------------------------------------------------------------------


def test_random_uniform_min_equals_max() -> None:
    gen = _gen("random_uniform", {"min": 7, "max": 7}, seed=1)
    assert all(gen.update(0.0) == 7 for _ in range(10))
    gen_float = _gen("random_uniform", {"min": 2.5, "max": 2.5}, seed=1)
    assert all(gen_float.update(0.0) == 2.5 for _ in range(10))


# --- random_walk -----------------------------------------------------------------------


def test_random_walk_step_zero_never_moves() -> None:
    gen = _gen("random_walk", {"min": 0, "max": 100, "step": 0, "mean_reversion": 0}, seed=1)
    assert all(gen.update(0.0) == pytest.approx(50) for _ in range(5))


def test_random_walk_mean_reversion_one_returns_center() -> None:
    gen = _gen("random_walk", {"min": 0, "max": 10, "step": 4, "mean_reversion": 1}, seed=1)
    for _ in range(10):
        assert gen.update(0.0) == pytest.approx(5)


def test_random_walk_mean_reversion_bounds_accepted() -> None:
    _gen("random_walk", {"mean_reversion": 0})  # границы 0 и 1 допустимы
    _gen("random_walk", {"mean_reversion": 1})


# --- ramp_oneshot ----------------------------------------------------------------------


def test_ramp_oneshot_just_below_duration_is_linear() -> None:
    gen = _gen("ramp_oneshot", {"from": 0, "to": 100, "duration_s": 10, "then": "hold"})
    assert gen.update(9.99) == pytest.approx(99.9)  # ещё линейный участок
    assert gen.update(10.0) == 100  # граница — уже then


def test_ramp_oneshot_from_equals_to() -> None:
    gen = _gen("ramp_oneshot", {"from": 5, "to": 5, "duration_s": 1})
    assert gen.update(0.0) == 5
    assert gen.update(0.5) == 5
    assert gen.update(2.0) == 5  # after duration: hold


# --- countdown -------------------------------------------------------------------------


def test_countdown_start_equals_min_is_constant() -> None:
    gen = _gen("countdown", {"start": 3, "step": 1, "min": 3})
    assert gen.update(0.0) == 3
    assert gen.update(10.0) == 3


# --- step ------------------------------------------------------------------------------


def test_step_single_value_is_constant() -> None:
    gen = _gen("step", {"values": [42], "interval_s": 0.5})
    assert gen.update(0.0) == 42
    assert gen.update(7.5) == 42


def test_step_switches_on_exact_interval() -> None:
    gen = _gen("step", {"values": [1, 2], "interval_s": 2})
    assert gen.update(1.99) == 1
    assert gen.update(2.0) == 2


# --- follow ----------------------------------------------------------------------------


def test_follow_source_at_map_edges() -> None:
    ctx = FakeCtx()
    ctx.values[(2, "hr", 1023)] = 77  # последний value-grid адрес слейва 2
    assert _gen("follow", {"source": "hr:1023"}, slave=2, ctx=ctx).update(0.0) == 77
    ctx.bits[(1, "di", 64)] = True  # последний бит
    assert _gen("follow", {"source": "di:64"}, ctx=ctx).update(0.0) == 1


def test_follow_source_past_map_rejected() -> None:
    with pytest.raises(ValueError, match="вне карты"):
        _gen("follow", {"source": "hr:1024"}, slave=2)
    with pytest.raises(ValueError, match="вне карты"):
        _gen("follow", {"source": "di:65"})


# --- toggle ----------------------------------------------------------------------------


def test_toggle_fractional_period_exact_half() -> None:
    gen = _gen("toggle", {"period_s": 0.5}, area="cl")
    assert gen.update(0.24) is True
    assert gen.update(0.25) is False  # ровно половина периода
    assert gen.update(0.5) is True  # новый период


# --- pwm -------------------------------------------------------------------------------


def test_pwm_exact_duty_point_is_low() -> None:
    gen = _gen("pwm", {"period_s": 4, "duty": 0.5}, area="cl")
    assert gen.update(1.99) is True
    assert gen.update(2.0) is False  # frac == duty → уже False


# --- random_bit ------------------------------------------------------------------------


def test_random_bit_strict_inequality_at_bounds() -> None:
    """p=0 → False даже при random()==0.0; p=1 → True даже при random()≈1."""
    low = _gen("random_bit", {"p": 0}, area="cl", rng=FakeRng(0.0))  # type: ignore[arg-type]
    assert low.update(0.0) is False
    high = _gen("random_bit", {"p": 1}, area="cl", rng=FakeRng(0.999999))  # type: ignore[arg-type]
    assert high.update(0.0) is True


# --- counter_bits ----------------------------------------------------------------------


def test_counter_bits_exact_second_boundary() -> None:
    gen = _gen("counter_bits", {"bit": 1}, area="di", address=1)
    assert gen.update(1.99) is False  # int(1.99)=1 >> 1 = 0
    assert gen.update(2.0) is True  # 2 >> 1 = 1


def test_counter_bits_high_bit() -> None:
    gen = _gen("counter_bits", {"bit": 31}, area="cl", address=1)
    assert gen.update(2.0**31 - 1) is False
    assert gen.update(2.0**31) is True


# --- follow_bit ------------------------------------------------------------------------


def test_follow_bit_width_edges() -> None:
    ctx = FakeCtx()
    ctx.values[(1, "hr", 15)] = 0x80000000
    high = _gen("follow_bit", {"source": "hr:15", "bit": 31}, area="cl", ctx=ctx)
    assert high.update(0.0) is True
    beyond = _gen("follow_bit", {"source": "hr:15", "bit": 32}, area="cl", ctx=ctx)
    assert beyond.update(0.0) is False  # за шириной слова — 0

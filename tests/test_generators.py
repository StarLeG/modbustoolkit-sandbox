"""Поведения: unit-тесты генераторов (TASK-211…236).

Детерминизм: `update(now)` — функция фиктивного времени; случайные
поведения — с seeded `random.Random`. Проверяются границы и циклы.
"""
from __future__ import annotations

import random

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

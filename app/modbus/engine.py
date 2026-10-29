"""Движок поведений: тик-цикл и запись значений в карты слейвов (TASK-202).

Семантика — `docs/adr/ADR-003-behavior-engine.md`:

- тик раз в `tick_ms` (по умолчанию `ENGINE_TICK_MS`) как asyncio-задача;
  остановка — `stop()` / `async with` (graceful);
- значение пишется в карту только при изменении (сравнение с последним
  записанным *самим движком*): `constant` применяется один раз, а
  пользовательские записи в незаблокированные регистры сохраняются;
- привязки — value-grid адреса; запись кодируется `codec.encode` в
  `registers_per_value` регистров (слейвы 2..9 — 2 регистра).

Движок пишет в `SlaveMap` напрямую, минуя `write_policy`: locked-привязки
блокируют только внешние записи (ADR-003 п. 3).
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Self

from app.modbus.behavior import Behavior, BehaviorRegistry, Binding
from app.modbus.codec import clamp_value, decode, encode
from app.modbus.generators import ValueGenerator, create_generator
from app.modbus.slave_map import SlaveMap
from app.modbus.types import Area

log = logging.getLogger(__name__)

_BIT_AREAS: tuple[Area, ...] = ("cl", "di")


@dataclass
class _Slot:
    """Привязка на время жизни генератора: конфиг + состояние применения."""

    config: Behavior
    generator: ValueGenerator
    created_at: float
    last: int | float | bool | None = None


class BehaviorEngine:
    """Тик-цикл поведений поверх карт слейвов (реестр → генераторы → SlaveMap)."""

    def __init__(
        self,
        registry: BehaviorRegistry,
        maps: dict[int, SlaveMap],
        tick_ms: int = 100,
        *,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        self._registry = registry
        self._maps = maps
        self._tick_ms = tick_ms
        self._clock = clock
        self._rng = rng if rng is not None else random.Random()
        self._slots: dict[Binding, _Slot] = {}
        self._task: asyncio.Task[None] | None = None
        self._t0 = 0.0
        self.ticks = 0

    # --- GeneratorContext: чтение источников для follow ---------------------------

    def read_value(self, binding: Binding) -> int | float:
        slave, area, address = binding
        smap = self._maps[slave]
        if area in _BIT_AREAS:
            return 1 if smap.get_bits(area, address, 1)[0] else 0
        regs = smap.get_registers(area, address, smap.spec.fmt.registers_per_value)
        return decode(regs, smap.spec.fmt)

    def read_bit(self, binding: Binding) -> bool:
        slave, area, address = binding
        smap = self._maps[slave]
        if area not in _BIT_AREAS:
            raise ValueError(f"read_bit: {area!r} не битовая область")
        return smap.get_bits(area, address, 1)[0]

    # --- тик-цикл -----------------------------------------------------------------

    def sync(self, now: float) -> None:
        """(Пере)создать генераторы там, где конфигурация изменилась по идентичности."""
        alive: dict[Binding, _Slot] = {}
        for binding, behavior in self._registry.items():
            slot = self._slots.get(binding)
            if slot is None or slot.config is not behavior:
                generator = create_generator(behavior, binding, self, self._rng)
                slot = _Slot(config=behavior, generator=generator, created_at=now)
            alive[binding] = slot
        self._slots = alive

    def tick(self, now: float | None = None) -> None:
        """Один проход: обновить генераторы, записать изменившиеся значения."""
        if now is None:
            now = self._clock() - self._t0
        self.sync(now)
        for binding, slot in self._slots.items():
            self._apply(binding, slot, now)
        self.ticks += 1

    def _apply(self, binding: Binding, slot: _Slot, now: float) -> None:
        if slot.generator.is_static and slot.last is not None:
            return  # constant уже применён; чужие записи не перетираем
        slave, area, address = binding
        smap = self._maps[slave]
        value = slot.generator.update(now - slot.created_at)
        if area in _BIT_AREAS:
            new_bit = bool(value)
            if slot.last != new_bit:
                smap.set_bits(area, address, [new_bit])
                slot.last = new_bit
            return
        new_value = clamp_value(value, smap.spec.fmt)
        if slot.last != new_value:
            smap.set_registers(area, address, encode(new_value, smap.spec.fmt))
            slot.last = new_value

    def start(self) -> None:
        """Запустить тик-цикл (asyncio-задача)."""
        if self._task is not None:
            raise RuntimeError("движок уже запущен")
        self._t0 = self._clock()
        self._task = asyncio.create_task(self._run(), name="behavior-engine")
        log.info("behavior engine started (tick=%d ms)", self._tick_ms)

    async def _run(self) -> None:
        interval = self._tick_ms / 1000.0
        while True:
            self.tick()
            await asyncio.sleep(interval)

    async def stop(self) -> None:
        """Остановить тик-цикл (graceful); безопасно вызывать повторно."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        log.info("behavior engine stopped")

    async def __aenter__(self) -> Self:
        self.start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.stop()

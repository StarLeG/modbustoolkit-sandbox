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

from app.modbus.behavior import (
    Behavior,
    BehaviorRegistry,
    Binding,
    pair_owner,
    wire_format_for,
)
from app.modbus.codec import clamp_value, decode, encode
from app.modbus.generators import ConstantGenerator, ValueGenerator, create_generator
from app.modbus.journal import Journal, JournalEntry
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
        journal: Journal | None = None,
    ) -> None:
        self._registry = registry
        self._maps = maps
        self._tick_ms = tick_ms
        self._clock = clock
        self._rng = rng if rng is not None else random.Random()
        self._journal = journal
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
        owner = pair_owner(self._registry, slave, area, address)
        if owner is not None:
            address = owner  # младший регистр пары: значение живёт на адресе N
        config = self._registry.get(slave, area, address)
        fmt = wire_format_for(config, slave) if config is not None else smap.spec.fmt
        regs = smap.get_registers(area, address, fmt.registers_per_value)
        return decode(regs, fmt)

    def read_bit(self, binding: Binding) -> bool:
        slave, area, address = binding
        smap = self._maps[slave]
        if area not in _BIT_AREAS:
            raise ValueError(f"read_bit: {area!r} не битовая область")
        return smap.get_bits(area, address, 1)[0]

    # --- тик-цикл -----------------------------------------------------------------

    def sync(self, now: float) -> None:
        """(Пере)создать генераторы там, где конфигурация изменилась по идентичности.

        Реестр не сжимается (TASK-205: `remove()` сбрасывает к дефолту), поэтому
        слоты только создаются и обновляются, но не удаляются.
        """
        for binding, behavior in self._registry.items():
            slot = self._slots.get(binding)
            if slot is None or slot.config is not behavior:
                self._slots[binding] = self._make_slot(binding, behavior, now)

    def _make_slot(self, binding: Binding, behavior: Behavior, now: float) -> _Slot:
        """Создать генератор; ошибка конструктора/параметров → constant 0 (инвариант 7)."""
        slave, area, address = binding
        try:
            generator = create_generator(behavior, binding, self, self._rng)
        except Exception:  # инвариант 7: ошибка поведения не роняет сервер
            log.exception(
                "поведение %r на slave %s %s:%s не создано → constant 0",
                behavior.kind,
                slave,
                area,
                address,
            )
            self._journal_failure(slave, area, address)
            fallback = Behavior(
                "constant", {"value": 0}, locked=behavior.locked, dtype=behavior.dtype
            )
            self._registry.set(slave, area, address, fallback)
            generator = ConstantGenerator(False if area in _BIT_AREAS else 0)
            return _Slot(config=fallback, generator=generator, created_at=now)
        return _Slot(config=behavior, generator=generator, created_at=now)

    def tick(self, now: float | None = None) -> None:
        """Один проход: обновить генераторы, записать изменившиеся значения."""
        if now is None:
            now = self._clock() - self._t0
        self.sync(now)
        for binding, slot in self._slots.items():
            try:
                self._apply(binding, slot, now)
            except Exception:  # барьер: тик не должен падать из-за одной привязки
                log.exception("не удалось применить поведение на %s", binding)
        self.ticks += 1

    def _apply(self, binding: Binding, slot: _Slot, now: float) -> None:
        slave, area, address = binding
        if pair_owner(self._registry, slave, area, address) is not None:
            return  # младший регистр пары N, N+1: значением управляет привязка N
        if slot.generator.is_static and slot.last is not None:
            return  # constant уже применён; чужие записи не перетираем
        try:
            value = slot.generator.update(now - slot.created_at)
        except Exception:  # noqa: BLE001 — инвариант 7: ошибка поведения не роняет сервер
            value = self._fallback(binding, slot)
        self._write(binding, slot, value)

    def _journal_failure(self, slave: int, area: Area, address: int) -> None:
        if self._journal is not None:
            self._journal.add(
                JournalEntry(
                    action="behavior_failed",
                    slave=slave,
                    area=area,
                    address=address,
                    source="engine",
                )
            )

    def _fallback(self, binding: Binding, slot: _Slot) -> int | float | bool:
        """Ошибка поведения: constant 0 + лог + журнал (инвариант 7, ADR-003 п. 4)."""
        slave, area, address = binding
        log.exception(
            "поведение %r на slave %s %s:%s упало → constant 0",
            slot.config.kind,
            slave,
            area,
            address,
        )
        self._journal_failure(slave, area, address)
        fallback = Behavior(
            "constant", {"value": 0}, locked=slot.config.locked, dtype=slot.config.dtype
        )
        self._registry.set(slave, area, address, fallback)
        slot.config = fallback
        slot.generator = ConstantGenerator(False if area in _BIT_AREAS else 0)
        return slot.generator.update(0.0)

    def _write(self, binding: Binding, slot: _Slot, value: float | bool) -> None:
        slave, area, address = binding
        smap = self._maps[slave]
        if area in _BIT_AREAS:
            new_bit = bool(value)
            if slot.last != new_bit:
                smap.set_bits(area, address, [new_bit])
                slot.last = new_bit
            return
        fmt = wire_format_for(slot.config, slave)  # тип значения × порядок слов (TASK-717)
        new_value = clamp_value(value, fmt)
        if slot.last != new_value:
            smap.set_registers(area, address, encode(new_value, fmt))
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

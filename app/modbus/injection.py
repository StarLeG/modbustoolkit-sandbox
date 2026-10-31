"""Реестр инъекций ошибок (TASK-309, ADR-004 п. 6) и сопоставление с запросами.

Инъекции (exception / delay / timeout) регистрируются через REST и живут
в памяти с ленивым TTL: запись считается истёкшей, когда `now >= expires_at`
(проверяется при обращении к реестру, фоновых таймеров нет — инвариант 6).
Enforcement — `GuardedRequestHandler.handle_request` (ADR-007): перед
обработкой запроса ищется подходящая инъекция (`find_injection`) и по её типу
формируется ответ (exception), задержка (delay) или молчание (timeout).
"""
from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from app.modbus.behavior import touched_bindings
from app.modbus.types import SLAVE_SPECS, Area, SlaveSpec

InjectionKind = Literal["exception", "delay", "timeout"]

#: Коды Modbus-исключений, допустимые для инъекции (TASK-601).
EXCEPTION_CODES: tuple[int, ...] = (0x01, 0x02, 0x03, 0x04, 0x06)

#: Максимальная задержка инъекции delay (TASK-602), мс.
MAX_DELAY_MS = 5000

#: Потолок одновременных активных инъекций (TASK-1004, ADR-012): флуд
#: `POST /inject` не должен раздувать реестр. Снятие и истечение TTL
#: освобождают слоты.
MAX_ACTIVE_INJECTIONS = 32

_KINDS: tuple[str, ...] = ("exception", "delay", "timeout")


@dataclass(frozen=True)
class Injection:
    """Одна инъекция ошибки на слейв или диапазон адресов.

    Цель: без `area`/`address` — весь слейв; диапазон задаётся по сетке
    значений (1,3,…,1023 для 32-битных форматов, `docs/HRIR_SPEC.md`).
    `ttl_s` — фактическое время жизни (≤ `INJECTION_MAX_TTL_S`); для
    `kind="timeout"` это же значение — длительность молчания (TASK-603).
    """

    id: int
    kind: InjectionKind
    slave: int
    ttl_s: float
    created_at: float
    expires_at: float
    area: Area | None = None
    address: int | None = None
    count: int | None = None
    code: int | None = None
    delay_ms: int | None = None


def _check_params(
    kind: str,
    slave: int,
    area: Area | None,
    address: int | None,
    count: int | None,
    code: int | None,
    delay_ms: int | None,
) -> None:
    """Валидация параметров инъекции (ValueError/TypeError → 400 в REST)."""
    if kind not in _KINDS:
        raise ValueError(f"неизвестный тип инъекции: {kind}")
    if slave not in SLAVE_SPECS:
        raise ValueError(f"неизвестный slave: {slave}")
    if kind == "exception":
        if code is None:
            raise ValueError("для exception требуется code")
        if code not in EXCEPTION_CODES:
            raise ValueError(f"недопустимый код исключения: {code}")
        if delay_ms is not None:
            raise ValueError("delay_ms не используется для exception")
    elif kind == "delay":
        if delay_ms is None:
            raise ValueError("для delay требуется delay_ms")
        if not 0 <= delay_ms <= MAX_DELAY_MS:
            raise ValueError(f"delay_ms вне диапазона 0..{MAX_DELAY_MS}: {delay_ms}")
        if code is not None:
            raise ValueError("code не используется для delay")
    else:  # timeout: молчание ttl_s секунд, параметров ответа нет
        if code is not None:
            raise ValueError("code не используется для timeout")
        if delay_ms is not None:
            raise ValueError("delay_ms не используется для timeout")
    if (address is not None) != (area is not None):
        raise ValueError("address и area задаются вместе")
    if count is not None and address is None:
        raise ValueError("count требует address")
    if area is None or address is None:
        return
    spec = SLAVE_SPECS[slave]
    rng = next((r for r in spec.area_ranges(area) if address in r), None)
    if rng is None:
        raise ValueError(f"slave {slave}: {area} адрес {address} вне карты значений")
    if count is not None:
        last = address + (count - 1) * rng.step
        if last not in rng:
            raise ValueError(
                f"slave {slave}: диапазон {area} {address}+{count} выходит за карту значений"
            )


class InjectionRegistry:
    """Реестр инъекций: добавление, выборка, снятие, ленивый TTL, потолок числа."""

    def __init__(
        self,
        max_ttl_s: float = 300.0,
        *,
        max_active: int = MAX_ACTIVE_INJECTIONS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_ttl_s <= 0:
            raise ValueError(f"max_ttl_s должен быть > 0, получено: {max_ttl_s}")
        if max_active <= 0:
            raise ValueError(f"max_active должен быть > 0, получено: {max_active}")
        self._max_ttl_s = float(max_ttl_s)
        self._max_active = int(max_active)
        self._clock = clock
        self._injections: dict[int, Injection] = {}
        self._next_id = 1

    @property
    def max_ttl_s(self) -> float:
        """Максимальное время жизни инъекции (INJECTION_MAX_TTL_S)."""
        return self._max_ttl_s

    @property
    def max_active(self) -> int:
        """Потолок одновременных активных инъекций (MAX_ACTIVE_INJECTIONS)."""
        return self._max_active

    def add(
        self,
        kind: InjectionKind,
        slave: int,
        *,
        area: Area | None = None,
        address: int | None = None,
        count: int | None = None,
        code: int | None = None,
        delay_ms: int | None = None,
        ttl_s: float | None = None,
    ) -> Injection:
        """Создать инъекцию; `ttl_s` ограничивается `max_ttl_s` (ADR-004 п. 6).

        Без `ttl_s` — максимальное время жизни. Для `kind="timeout"` `ttl_s` —
        это и есть длительность молчания. Не больше `max_active` одновременных
        инъекций (TASK-1004): истёкшие слоты не занимают, при переполнении —
        ValueError (400 в REST).
        """
        _check_params(kind, slave, area, address, count, code, delay_ms)
        if ttl_s is not None and ttl_s <= 0:
            raise ValueError(f"ttl_s должен быть > 0, получено: {ttl_s}")
        self._purge()  # истёкшие не занимают слоты потолка
        if len(self._injections) >= self._max_active:
            raise ValueError(
                f"слишком много активных инъекций: максимум {self._max_active}"
            )
        ttl = self._max_ttl_s if ttl_s is None else min(float(ttl_s), self._max_ttl_s)
        now = self._clock()
        injection = Injection(
            id=self._next_id,
            kind=kind,
            slave=slave,
            ttl_s=ttl,
            created_at=now,
            expires_at=now + ttl,
            area=area,
            address=address,
            count=count,
            code=code,
            delay_ms=delay_ms,
        )
        self._next_id += 1
        self._injections[injection.id] = injection
        return injection

    def _purge(self) -> None:
        """Ленивый TTL: выбросить истёкшие инъекции (при обращении)."""
        now = self._clock()
        for injection_id in [
            i for i, inj in self._injections.items() if now >= inj.expires_at
        ]:
            del self._injections[injection_id]

    def items(self) -> list[Injection]:
        """Активные (не истёкшие) инъекции в порядке добавления."""
        self._purge()
        return list(self._injections.values())

    def get(self, injection_id: int) -> Injection | None:
        """Активная инъекция по id либо None (нет / истекла)."""
        self._purge()
        return self._injections.get(injection_id)

    def clear(self, *, injection_id: int | None = None, slave: int | None = None) -> int:
        """Снять инъекции: по id, по слейву или все. Возвращает число снятых."""
        self._purge()
        doomed = [
            i
            for i, inj in self._injections.items()
            if (injection_id is None or inj.id == injection_id)
            and (slave is None or inj.slave == slave)
        ]
        for injection_id_ in doomed:
            del self._injections[injection_id_]
        return len(doomed)

    def __len__(self) -> int:
        self._purge()
        return len(self._injections)


def _grid_step(spec: SlaveSpec, area: Area, address: int) -> int:
    """Шаг сетки значений области: 2 для 32-битных форматов, иначе 1."""
    rng = next((r for r in spec.area_ranges(area) if address in r), None)
    return rng.step if rng is not None else 1


def find_injection(
    injections: Sequence[Injection],
    slave: int,
    area: Area,
    address: int,
    count: int,
) -> Injection | None:
    """Первая инъекция (в порядке добавления), задевающая окно запроса (ADR-007 п. 2).

    `address`/`count` — сырое окно PDU (регистры/биты); сопоставление идёт по
    сетке значений через `touched_bindings` (для 32-битных форматов raw N+1
    задевает значение N). `injections` — активные инъекции (например,
    `InjectionRegistry.items()`, уже без истёкших). Цель «весь слейв»
    (`area=None`) совпадает с любой областью слейва.
    """
    spec = SLAVE_SPECS.get(slave)
    if spec is None:
        return None
    touched = {binding[2] for binding in touched_bindings(slave, area, address, count)}
    if not touched:
        return None
    for injection in injections:
        if injection.slave != slave:
            continue
        if injection.area is None:  # весь слейв
            return injection
        if injection.area != area or injection.address is None:
            continue
        step = _grid_step(spec, injection.area, injection.address)
        span = range(
            injection.address,
            injection.address + (injection.count or 1) * step,
            step,
        )
        if touched.intersection(span):
            return injection
    return None

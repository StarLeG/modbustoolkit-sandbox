"""TASK-309: реестр инъекций — app/modbus/injection.py (ADR-004 п. 6).

Ленивый TTL, ограничение TTL, снятие по id/слейву/все, валидация параметров.
"""
from __future__ import annotations

import pytest

from app.modbus.injection import EXCEPTION_CODES, MAX_DELAY_MS, InjectionRegistry


def _registry(max_ttl_s: float = 300.0) -> tuple[InjectionRegistry, list[float]]:
    now = [100.0]
    return InjectionRegistry(max_ttl_s, clock=lambda: now[0]), now


def test_add_assigns_sequential_ids() -> None:
    registry, _ = _registry()
    first = registry.add("exception", 1, code=0x02)
    second = registry.add("delay", 2, delay_ms=100)
    assert (first.id, second.id) == (1, 2)
    assert [inj.id for inj in registry.items()] == [1, 2]
    assert len(registry) == 2


def test_ttl_defaults_to_max_and_clamps() -> None:
    registry, _ = _registry(max_ttl_s=300.0)
    default = registry.add("timeout", 1)
    clamped = registry.add("timeout", 2, ttl_s=1000)
    exact = registry.add("timeout", 3, ttl_s=60)
    assert default.ttl_s == 300.0  # без ttl_s — максимум
    assert clamped.ttl_s == 300.0  # больше максимума — ограничивается
    assert exact.ttl_s == 60.0
    assert exact.expires_at == exact.created_at + 60.0


def test_lazy_ttl_expires() -> None:
    registry, now = _registry()
    injection = registry.add("timeout", 1, ttl_s=10)
    now[0] = injection.expires_at - 0.1
    assert registry.get(injection.id) is not None
    now[0] = injection.expires_at  # граница — уже истекла
    assert registry.get(injection.id) is None
    assert registry.items() == []


def test_clear_filters() -> None:
    registry, _ = _registry()
    a = registry.add("exception", 1, code=0x01)
    b = registry.add("delay", 1, delay_ms=10)
    registry.add("exception", 2, code=0x04)
    assert registry.clear(slave=1) == 2
    assert [inj.id for inj in registry.items()] == [3]
    assert registry.clear() == 1
    assert registry.clear() == 0
    assert registry.get(a.id) is None and registry.get(b.id) is None


def test_clear_by_id() -> None:
    registry, _ = _registry()
    first = registry.add("exception", 1, code=0x06)
    registry.add("exception", 1, code=0x06)
    assert registry.clear(injection_id=first.id) == 1
    assert [inj.id for inj in registry.items()] == [2]


def test_validation_errors() -> None:
    registry, _ = _registry()
    cases: list[tuple[tuple[str, int], dict[str, object], str]] = [
        (("zzz", 1), {}, "неизвестный тип"),
        (("exception", 11), {"code": 1}, "неизвестный slave"),
        (("exception", 1), {}, "требуется code"),
        (("exception", 1), {"code": 0x05}, "недопустимый код"),
        (("exception", 1), {"code": 1, "delay_ms": 100}, "не используется"),
        (("delay", 1), {}, "требуется delay_ms"),
        (("delay", 1), {"delay_ms": MAX_DELAY_MS + 1}, "вне диапазона"),
        (("delay", 1), {"delay_ms": 100, "code": 1}, "не используется"),
        (("timeout", 1), {"code": 1}, "не используется"),
        (("timeout", 1), {"delay_ms": 100}, "не используется"),
        (("exception", 1), {"code": 1, "count": 2}, "count требует address"),
        (("exception", 1), {"code": 1, "address": 1}, "задаются вместе"),
        (("exception", 2), {"code": 1, "area": "hr", "address": 2}, "вне карты значений"),
        (("exception", 10), {"code": 1, "area": "cl", "address": 1}, "вне карты значений"),
        (
            ("exception", 1),
            {"code": 1, "area": "hr", "address": 1024, "count": 2},
            "выходит за карту значений",
        ),
        (
            ("exception", 10),
            {"code": 1, "area": "ir", "address": 265, "count": 40},
            "выходит за карту значений",  # диапазон перекрывает «дырку» 269..299
        ),
        (("timeout", 1), {"ttl_s": 0}, "ttl_s должен быть > 0"),
    ]
    for (kind, slave), kwargs, match in cases:
        with pytest.raises((ValueError, TypeError), match=match):
            registry.add(kind, slave, **kwargs)  # type: ignore[arg-type]
    assert len(registry) == 0  # ничего не создано


def test_valid_targets() -> None:
    registry, _ = _registry()
    whole = registry.add("exception", 5, code=0x02)  # весь слейв
    ranged = registry.add("delay", 2, delay_ms=10, area="hr", address=1, count=2)
    edge = registry.add("timeout", 10, area="ir", address=300, count=2)  # 300..301
    assert (whole.area, whole.address, whole.count) == (None, None, None)
    assert (ranged.area, ranged.address, ranged.count) == ("hr", 1, 2)
    assert (edge.area, edge.address, edge.count) == ("ir", 300, 2)
    assert EXCEPTION_CODES == (0x01, 0x02, 0x03, 0x04, 0x06)

"""Композиция сервисов приложения (TASK-301, ADR-004 п. 1).

Контейнер `AppServices` — единая точка сборки in-process сервисов ядра
(реестр поведений, карты слейвов, движок, журнал, политика записи).
Web-слой (REST/WS) получает сервисы через него, не создавая их сам.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.modbus.behavior import BehaviorRegistry, WritePolicy, make_write_policy
from app.modbus.config import Config
from app.modbus.engine import BehaviorEngine
from app.modbus.journal import Journal
from app.modbus.slave_map import SlaveMap
from app.modbus.types import SLAVE_SPECS


@dataclass
class AppServices:
    """Сервисы приложения: ядро + журнал + политика внешних записей."""

    config: Config
    registry: BehaviorRegistry
    maps: dict[int, SlaveMap]
    engine: BehaviorEngine
    journal: Journal
    write_policy: WritePolicy


def build_services(config: Config | None = None) -> AppServices:
    """Собрать сервисы: реестр, карты 1..10, журнал, движок, политика записи.

    Значения регистров живут только в памяти (инвариант 9): перезапуск = сброс.
    """
    cfg = config if config is not None else Config.from_env()
    registry = BehaviorRegistry()
    maps: dict[int, SlaveMap] = {slave_id: SlaveMap(slave_id) for slave_id in SLAVE_SPECS}
    journal = Journal(size=cfg.JOURNAL_SIZE)
    engine = BehaviorEngine(registry, maps, tick_ms=cfg.ENGINE_TICK_MS, journal=journal)
    write_policy = make_write_policy(registry, journal)
    return AppServices(
        config=cfg,
        registry=registry,
        maps=maps,
        engine=engine,
        journal=journal,
        write_policy=write_policy,
    )

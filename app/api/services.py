"""Композиция сервисов приложения (TASK-301, ADR-004 п. 1).

Контейнер `AppServices` — единая точка сборки in-process сервисов ядра
(реестр поведений, карты слейвов, движок, журнал, политика записи).
Web-слой (REST/WS) получает сервисы через него, не создавая их сам.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.api.rate_limit import WriteRateLimiter
from app.api.state_bus import WebStateBus
from app.modbus.behavior import BehaviorRegistry, WritePolicy, make_write_policy
from app.modbus.config import Config
from app.modbus.engine import BehaviorEngine
from app.modbus.injection import InjectionRegistry
from app.modbus.journal import Journal
from app.modbus.metrics import ServerMetrics
from app.modbus.slave_map import SlaveMap
from app.modbus.tags import TagStore
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
    write_limiter: WriteRateLimiter
    injections: InjectionRegistry
    metrics: ServerMetrics
    ws_bus: WebStateBus
    tags: TagStore


def build_services(config: Config | None = None) -> AppServices:
    """Собрать сервисы: реестры, карты 1..10, журнал, движок, политика записи.

    Значения регистров живут только в памяти (инвариант 9): перезапуск = сброс.
    """
    cfg = config if config is not None else Config.from_env()
    registry = BehaviorRegistry()
    maps: dict[int, SlaveMap] = {slave_id: SlaveMap(slave_id) for slave_id in SLAVE_SPECS}
    journal = Journal(size=cfg.JOURNAL_SIZE)
    engine = BehaviorEngine(registry, maps, tick_ms=cfg.ENGINE_TICK_MS, journal=journal)
    write_policy = make_write_policy(registry, journal)
    write_limiter = WriteRateLimiter(cfg.WEB_MAX_WRITE_PER_MIN_PER_IP)
    injections = InjectionRegistry(max_ttl_s=cfg.INJECTION_MAX_TTL_S)
    metrics = ServerMetrics()
    tags = TagStore()
    ws_bus = WebStateBus(maps, registry=registry, state_hz=cfg.WS_STATE_HZ, tags=tags)
    journal.subscribe(ws_bus.publish_entry)  # события журнала → /ws/events (TASK-404)
    return AppServices(
        config=cfg,
        registry=registry,
        maps=maps,
        engine=engine,
        journal=journal,
        write_policy=write_policy,
        write_limiter=write_limiter,
        injections=injections,
        metrics=metrics,
        ws_bus=ws_bus,
        tags=tags,
    )

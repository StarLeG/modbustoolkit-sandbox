"""Журнал событий — ring buffer (TASK-203; полная схема полей — TASK-505).

Фиксируются отклонённые записи в locked-поведения (0x04), далее — смена
поведений, инъекции, UI-записи. Хранение — только в памяти (инвариант 9).
Подписка на новые записи (`subscribe`) — зеркало для `/ws/events` (TASK-404).
"""
from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.modbus.types import Area

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class JournalEntry:
    """Одна запись журнала.

    `source` — источник события ("modbus" | "web" | "engine" | …);
    `ip` — адрес инициатора, когда он известен: для modbus — IP пира соединения
    (TASK-505), для web — IP запроса.
    `area`/`address` — заполняются, когда событие привязано к адресу; для
    событий уровня слейва (инъекции и т.п.) — None.
    """

    action: str
    slave: int
    area: Area | None = None
    address: int | None = None
    value: Any = None
    source: str = "modbus"
    ip: str | None = None
    timestamp: float = field(default_factory=time.time)


class Journal:
    """Кольцевой буфер фиксированного размера (JOURNAL_SIZE)."""

    def __init__(self, size: int = 1000) -> None:
        if size <= 0:
            raise ValueError(f"размер журнала должен быть > 0, получено: {size}")
        self._entries: deque[JournalEntry] = deque(maxlen=size)
        self._listeners: list[Callable[[JournalEntry], None]] = []

    def subscribe(self, listener: Callable[[JournalEntry], None]) -> Callable[[], None]:
        """Подписаться на новые записи; возвращает функцию отписки (TASK-404).

        Ошибка слушателя не прерывает `add` (инвариант 7) — только лог.
        """
        self._listeners.append(listener)

        def _unsubscribe() -> None:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

        return _unsubscribe

    def add(self, entry: JournalEntry) -> None:
        """Добавить запись и уведомить слушателей; при переполнении старая вытесняется."""
        self._entries.append(entry)
        for listener in tuple(self._listeners):
            try:
                listener(entry)
            except Exception:
                logger.exception("journal: слушатель завершился ошибкой")

    def entries(self, limit: int | None = None) -> list[JournalEntry]:
        """Записи от старых к новым; `limit` — максимум последних записей."""
        if limit is None:
            return list(self._entries)
        if limit < 0:
            raise ValueError(f"limit должен быть >= 0, получено: {limit}")
        if limit == 0:
            return []
        return list(self._entries)[-limit:]

    def __len__(self) -> int:
        return len(self._entries)

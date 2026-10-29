"""Журнал событий — ring buffer (TASK-203; полная схема полей — TASK-505).

Фиксируются отклонённые записи в locked-поведения (0x04), далее — смена
поведений, инъекции, UI-записи. Хранение — только в памяти (инвариант 9).
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from app.modbus.types import Area


@dataclass(frozen=True)
class JournalEntry:
    """Одна запись журнала.

    `source` — источник события ("modbus" | "web" | "engine" | …);
    `ip` — адрес инициатора, когда он известен (заполняется с TASK-505).
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

    def add(self, entry: JournalEntry) -> None:
        """Добавить запись; при переполнении самая старая вытесняется."""
        self._entries.append(entry)

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

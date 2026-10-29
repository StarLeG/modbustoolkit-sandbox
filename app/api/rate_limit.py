"""Rate-limit UI-записей: скользящее окно в памяти по IP (TASK-306, ADR-004 п. 4).

Лимит — `WEB_MAX_WRITE_PER_MIN_PER_IP` запросов за 60 с на IP. Хранение —
только в памяти (инвариант 9): перезапуск сбрасывает счётчики.
"""
from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable


class WriteRateLimiter:
    """Скользящее окно: не более `limit` запросов за `window_s` на каждый IP."""

    def __init__(
        self,
        limit: int,
        window_s: float = 60.0,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit <= 0:
            raise ValueError(f"лимит должен быть > 0, получено: {limit}")
        if window_s <= 0:
            raise ValueError(f"окно должно быть > 0, получено: {window_s}")
        self._limit = limit
        self._window_s = window_s
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}

    def allow(self, ip: str) -> bool:
        """True, если запрос разрешён (и учтён); иначе лимит исчерпан."""
        now = self._clock()
        hits = self._hits.setdefault(ip, deque())
        while hits and hits[0] <= now - self._window_s:
            hits.popleft()
        if len(hits) >= self._limit:
            return False
        hits.append(now)
        return True

    def __len__(self) -> int:
        """Число отслеживаемых IP (для диагностики утечек памяти)."""
        return len(self._hits)

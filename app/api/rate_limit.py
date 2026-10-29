"""Rate-limit UI-записей: скользящее окно по IP (TASK-306, ADR-004 п. 4).

Лимит — `WEB_MAX_WRITE_PER_MIN_PER_IP` запросов за 60 с на IP; окно —
`RateWindow` (`app.modbus.limits`, общее с rate-limit Modbus-записей).
Хранение — только в памяти (инвариант 9): перезапуск сбрасывает счётчики.
"""
from __future__ import annotations

import time
from collections.abc import Callable

from app.modbus.limits import RateWindow


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
        self._windows: dict[str, RateWindow] = {}

    def allow(self, ip: str) -> bool:
        """True, если запрос разрешён (и учтён); иначе лимит исчерпан."""
        window = self._windows.get(ip)
        if window is None:
            window = RateWindow(self._limit, self._window_s, clock=self._clock)
            self._windows[ip] = window
        return window.allow()

    def __len__(self) -> int:
        """Число отслеживаемых IP (для диагностики утечек памяти)."""
        return len(self._windows)

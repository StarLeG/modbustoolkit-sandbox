"""Лимиты публичного порта (TASK-501/502, ADR-006): in-memory счётчики.

Состояние — только в памяти (инвариант 9): перезапуск сбрасывает счётчики.
"""
from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable


class RateWindow:
    """Скользящее окно: не более `limit` событий за `window_s` (TASK-502)."""

    def __init__(
        self,
        limit: int,
        window_s: float = 1.0,
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
        self._hits: deque[float] = deque()

    def allow(self) -> bool:
        """True, если событие разрешено (и учтено); иначе лимит исчерпан."""
        now = self._clock()
        while self._hits and self._hits[0] <= now - self._window_s:
            self._hits.popleft()
        if len(self._hits) >= self._limit:
            return False
        self._hits.append(now)
        return True


class ConnectionLimiter:
    """Одновременные соединения по IP: не более `max_per_ip` (TASK-501)."""

    def __init__(self, max_per_ip: int) -> None:
        if max_per_ip <= 0:
            raise ValueError(f"max_per_ip должен быть > 0, получено: {max_per_ip}")
        self._max_per_ip = max_per_ip
        self._counts: dict[str, int] = {}

    @property
    def max_per_ip(self) -> int:
        return self._max_per_ip

    def allow(self, ip: str) -> bool:
        """Занять слот: True — разрешено, False — лимит исчерпан."""
        count = self._counts.get(ip, 0)
        if count >= self._max_per_ip:
            return False
        self._counts[ip] = count + 1
        return True

    def release(self, ip: str) -> None:
        """Освободить слот; для неизвестного ip — без действия."""
        count = self._counts.get(ip, 0)
        if count <= 1:
            self._counts.pop(ip, None)
        else:
            self._counts[ip] = count - 1

    def count(self, ip: str) -> int:
        """Активных соединений с `ip`."""
        return self._counts.get(ip, 0)

    def __len__(self) -> int:
        """Число IP с активными соединениями (диагностика утечек)."""
        return len(self._counts)

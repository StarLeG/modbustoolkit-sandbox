"""Счётчики Modbus-сервера (TASK-703).

Один объект `ServerMetrics` живёт в `AppServices` и передаётся в
`create_server`/`serve` — web-слой и Modbus-слушатель считают в одном месте.
События (соединение, запрос) инкрементируют нарастающие счётчики и односекундные
бакеты; скорости req/s и err/s — усреднение за скользящее окно `RATE_WINDOW_S`.

Всё обновляется из event loop, чтение (`snapshot`) может идти из threadpool
(sync-роуты FastAPI) — короткие операции под `threading.Lock`. Фоновых таймеров
нет (инвариант 6): бакеты стареют лениво при записи и чтении.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

#: Окно усреднения скоростей, с.
RATE_WINDOW_S = 10.0

#: Ширина бакета событий, с.
_BUCKET_S = 1.0


@dataclass(frozen=True)
class MetricsSnapshot:
    """Снимок счётчиков для REST/UI (TASK-703)."""

    active_connections: int
    requests_total: int
    errors_total: int
    req_per_s: float
    err_per_s: float
    uptime_s: float


class ServerMetrics:
    """Счётчики сервера: соединения, запросы, ошибки, uptime, скорости."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._started = clock()
        self._active = 0
        self._requests_total = 0
        self._errors_total = 0
        slots = int(RATE_WINDOW_S / _BUCKET_S)
        #: (метка времени бакета, счётчик) — ленивое старение без таймеров.
        self._req_slots: list[tuple[float, int]] = [(0.0, 0)] * slots
        self._err_slots: list[tuple[float, int]] = [(0.0, 0)] * slots

    def _bump(self, slots: list[tuple[float, int]], now: float) -> None:
        bucket_ts = (now // _BUCKET_S) * _BUCKET_S
        bucket = int(bucket_ts // _BUCKET_S) % len(slots)
        ts, count = slots[bucket]
        slots[bucket] = (bucket_ts, 1 if ts != bucket_ts else count + 1)

    def _rate(self, slots: list[tuple[float, int]], now: float) -> float:
        total = sum(count for ts, count in slots if now - RATE_WINDOW_S < ts <= now)
        return total / RATE_WINDOW_S

    def connection_opened(self) -> None:
        """Соединение Modbus-клиента установлено."""
        with self._lock:
            self._active += 1

    def connection_closed(self) -> None:
        """Соединение Modbus-клиента закрыто."""
        with self._lock:
            self._active = max(0, self._active - 1)

    def request(self, *, error: bool = False) -> None:
        """Учесть обработанный запрос; `error=True` — ответ с ошибкой/молчание.

        Ошибкой считаются ExceptionResponse (включая 0x06 rate-limit и
        инъекции exception), поглощённые timeout-инъекцией и сбои обработки.
        """
        now = self._clock()
        with self._lock:
            self._requests_total += 1
            self._bump(self._req_slots, now)
            if error:
                self._errors_total += 1
                self._bump(self._err_slots, now)

    def snapshot(self) -> MetricsSnapshot:
        """Снимок счётчиков: нарастающие итоги, скорости за окно, uptime."""
        now = self._clock()
        with self._lock:
            return MetricsSnapshot(
                active_connections=self._active,
                requests_total=self._requests_total,
                errors_total=self._errors_total,
                req_per_s=self._rate(self._req_slots, now),
                err_per_s=self._rate(self._err_slots, now),
                uptime_s=max(0.0, now - self._started),
            )

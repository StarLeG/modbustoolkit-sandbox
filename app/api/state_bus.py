"""Шина WS-клиентов live-состояния (Эпик 4, ADR-005).

`WebStateBus` держит подключённых WS-клиентов, их ограниченные очереди и задачи
отправки. Продюсеры кадров (тикер состояния, журнал событий) никогда не ждут
клиента: переполнение очереди означает, что клиент не успевает, и он
отключается (TASK-403), остальные продолжают получать кадры. FastAPI-эндпоинты
живут в `app.api.ws`; зависимостей от FastAPI здесь нет.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Literal

from starlette.websockets import WebSocket

from app.api.state_view import state_payload
from app.modbus.slave_map import SlaveMap
from app.modbus.types import AREAS, SLAVE_SPECS, Area

logger = logging.getLogger(__name__)

#: Размер очереди исходящих кадров одного клиента (TASK-403).
WS_CLIENT_QUEUE = 32
#: Таймаут отправки кадра клиенту, с (TASK-403).
WS_SEND_TIMEOUT_S = 5.0
#: Close-код: ошибка подписки (ADR-005 п. 2).
WS_CLOSE_BAD_SUB = 1008
#: Close-код: медленный клиент отключён (TASK-403).
WS_CLOSE_SLOW = 1013


@dataclass(frozen=True)
class StateSubscription:
    """Подписка `/ws/state`: слейв, области, фильтр адресов (ADR-005 п. 2).

    `addresses=None` — вся сетка значений (value-grid: 1,3,…,1023 для
    32-битных форматов).
    """

    slave: int
    areas: tuple[Area, ...]
    addresses: tuple[int, ...] | None = None

    @classmethod
    def parse(cls, raw: Any) -> StateSubscription:
        """Разобрать JSON-сообщение подписки; `ValueError`/`TypeError` → ошибка клиенту."""
        if not isinstance(raw, dict):
            raise TypeError("подписка ожидается как JSON-объект")
        slave = raw.get("slave")
        if not isinstance(slave, int) or isinstance(slave, bool) or not 1 <= slave <= 10:
            raise ValueError(f"slave должен быть 1..10: {slave!r}")
        spec = SLAVE_SPECS[slave]
        available = tuple(a for a in AREAS if spec.area_ranges(a))
        raw_areas = raw.get("areas")
        if raw_areas is None:
            raw_areas = list(available)
        elif not isinstance(raw_areas, list) or not raw_areas:
            raise ValueError("areas ожидается как непустой список")
        areas: list[Area] = []
        for raw_area in raw_areas:
            if raw_area not in AREAS:
                raise ValueError(f"неизвестная область: {raw_area!r}")
            if raw_area not in available:
                raise ValueError(f"slave {slave}: область {raw_area} отсутствует")
            areas.append(raw_area)
        raw_addresses = raw.get("addresses")
        addresses: tuple[int, ...] | None = None
        if raw_addresses is not None:
            if not isinstance(raw_addresses, list) or not raw_addresses:
                raise ValueError("addresses ожидается как непустой список либо null")
            parsed: list[int] = []
            for raw_address in raw_addresses:
                if not isinstance(raw_address, int) or isinstance(raw_address, bool):
                    raise TypeError(f"адрес должен быть int: {raw_address!r}")
                if not any(spec.has_address(area, raw_address) for area in areas):
                    raise ValueError(f"slave {slave}: адрес {raw_address} вне карты значений")
                parsed.append(raw_address)
            addresses = tuple(parsed)
        return cls(slave=slave, areas=tuple(areas), addresses=addresses)


@dataclass
class WsClient:
    """Один WS-клиент: очередь кадров, задача отправки и (для state) подписка."""

    ws: WebSocket
    kind: Literal["state", "events"]
    queue: asyncio.Queue[str]
    sender: asyncio.Task[None] | None = None
    subscription: StateSubscription | None = None
    dropped: bool = False


class WebStateBus:
    """Рассылка кадров состояния и событий WS-клиентам (TASK-401…404)."""

    def __init__(
        self,
        maps: dict[int, SlaveMap],
        *,
        state_hz: float = 2.0,
        queue_size: int = WS_CLIENT_QUEUE,
        send_timeout_s: float = WS_SEND_TIMEOUT_S,
    ) -> None:
        if state_hz <= 0:
            raise ValueError(f"state_hz должен быть > 0, получено: {state_hz}")
        if queue_size <= 0:
            raise ValueError(f"queue_size должен быть > 0, получено: {queue_size}")
        self._maps = maps
        self._state_interval = 1.0 / state_hz
        self._queue_size = queue_size
        self._send_timeout_s = send_timeout_s
        self._clients: list[WsClient] = []
        self._ticker: asyncio.Task[None] | None = None

    @property
    def clients(self) -> tuple[WsClient, ...]:
        """Подключённые клиенты (не отключённые)."""
        return tuple(self._clients)

    # --- клиенты ---

    def add_client(self, ws: WebSocket, kind: Literal["state", "events"]) -> WsClient:
        """Зарегистрировать клиента и запустить его задачу отправки."""
        client = WsClient(ws=ws, kind=kind, queue=asyncio.Queue(maxsize=self._queue_size))
        client.sender = asyncio.create_task(self._sender(client))
        self._clients.append(client)
        if kind == "state" and self._ticker is None:
            self._ticker = asyncio.create_task(self._state_loop())
        return client

    def remove_client(self, client: WsClient) -> None:
        """Убрать клиента и остановить его задачи (идемпотентно)."""
        if client in self._clients:
            self._clients.remove(client)
        sender = client.sender
        client.sender = None
        try:
            current: asyncio.Task[Any] | None = asyncio.current_task()
        except RuntimeError:
            current = None
        if sender is not None and sender is not current:
            sender.cancel()
        if self._ticker is not None and not any(c.kind == "state" for c in self._clients):
            self._ticker.cancel()
            self._ticker = None

    def set_subscription(self, client: WsClient, subscription: StateSubscription) -> None:
        """Подписка/переподписка; первый кадр уходит сразу (ADR-005 п. 2)."""
        client.subscription = subscription
        self._enqueue_state(client)

    # --- продюсеры ---

    def _enqueue_state(self, client: WsClient) -> None:
        if client.subscription is None or client.dropped:
            return
        sub = client.subscription
        payload = state_payload(self._maps, sub.slave, sub.areas, sub.addresses)
        self._offer(client, {"type": "state", **payload})

    def _offer(self, client: WsClient, frame: dict[str, Any]) -> None:
        if client.dropped:
            return
        try:
            client.queue.put_nowait(json.dumps(frame))
        except asyncio.QueueFull:
            self._drop(client)  # медленный клиент — отключаем (TASK-403)

    def _drop(self, client: WsClient) -> None:
        if client.dropped:
            return
        client.dropped = True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # вне event loop — только убрать из списка
            self.remove_client(client)
            return
        loop.create_task(self._close_client(client))

    async def _close_client(self, client: WsClient) -> None:
        self.remove_client(client)
        try:
            await client.ws.close(code=WS_CLOSE_SLOW)
        except Exception:
            logger.debug("ws: клиент уже закрыт", exc_info=True)

    async def _sender(self, client: WsClient) -> None:
        try:
            while True:
                frame = await client.queue.get()
                await asyncio.wait_for(client.ws.send_text(frame), timeout=self._send_timeout_s)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("ws: ошибка отправки, клиент отключается", exc_info=True)
            await self._close_client(client)

    async def _state_loop(self) -> None:
        """Тикер периодической рассылки состояния (TASK-402)."""
        while True:
            await asyncio.sleep(self._state_interval)
            for client in tuple(self._clients):
                if client.kind == "state":
                    self._enqueue_state(client)

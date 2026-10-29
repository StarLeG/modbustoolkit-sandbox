"""WebSocket-эндпоинты live-состояния (Эпик 4, ADR-005).

Пути `/ws/state` (и далее `/ws/events`) — вне префикса `/api/modbus`, как
`GET /healthz`. Рассылкой занимается `WebStateBus` (`app.api.state_bus`).
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.services import AppServices
from app.api.state_bus import WS_CLOSE_BAD_SUB, StateSubscription, WebStateBus

logger = logging.getLogger(__name__)

ws_router = APIRouter()


def _bus(ws: WebSocket) -> WebStateBus:
    services: AppServices = ws.app.state.services
    return services.ws_bus


@ws_router.websocket("/ws/state")
async def ws_state(ws: WebSocket) -> None:
    """Live-состояние слейва (TASK-401).

    Первый текстовый кадр клиента — JSON-подписка `{slave, areas?, addresses?}`
    (ADR-005 п. 2); ответ — кадры `{"type": "state", "slave", "fmt", "areas"}`
    (формат `GET /api/modbus/state`), первый сразу как подтверждение. Повторное
    сообщение — переподписка. Ошибка подписки → `{"type": "error"}` + close(1008).
    """
    bus = _bus(ws)
    await ws.accept()
    client = bus.add_client(ws, kind="state")
    try:
        while True:
            raw = await ws.receive_text()
            try:
                subscription = StateSubscription.parse(json.loads(raw))
            except (ValueError, TypeError) as exc:
                bus.remove_client(client)  # дальше кадры шлём сами, без sender
                await ws.send_text(json.dumps({"type": "error", "detail": str(exc)}))
                await ws.close(code=WS_CLOSE_BAD_SUB)
                return
            bus.set_subscription(client, subscription)
    except WebSocketDisconnect:
        pass
    finally:
        bus.remove_client(client)


@ws_router.websocket("/ws/events")
async def ws_events(ws: WebSocket) -> None:
    """Канал событий журнала (TASK-404).

    Зеркало `GET /api/modbus/journal`: каждая запись журнала (записи, смена
    поведений, инъекции, отказы locked) уходит кадром
    `{"type": "event", "action", "slave", "area", "address", "value",
    "source", "ip", "timestamp"}` сразу при добавлении. Подписка не нужна,
    входящие сообщения клиента игнорируются.
    """
    bus = _bus(ws)
    await ws.accept()
    client = bus.add_client(ws, kind="events")
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
    finally:
        bus.remove_client(client)

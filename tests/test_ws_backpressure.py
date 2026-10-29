"""TASK-403: backpressure — медленный клиент отключается, остальные работают.

ADR-005 п. 4: ограниченная очередь, продюсеры не ждут клиента; переполнение
или таймаут отправки → close(1013) одного клиента без влияния на остальных.
"""
from __future__ import annotations

import asyncio

import pytest

from app.api.state_bus import WS_CLOSE_SLOW, StateSubscription, WebStateBus
from app.modbus.slave_map import SlaveMap


class FakeWS:
    """Фиктивный WebSocket: записывает кадры, фиксирует close."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed: list[int] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000) -> None:
        self.closed.append(code)


class SlowWS(FakeWS):
    """Клиент, который не читает кадры: отправка блокируется навсегда."""

    async def send_text(self, data: str) -> None:
        await asyncio.Event().wait()


_SUB = StateSubscription(slave=1, areas=("hr",), addresses=(1,))


def _bus(**kwargs: object) -> WebStateBus:
    return WebStateBus({1: SlaveMap(1)}, **kwargs)  # type: ignore[arg-type]


async def test_slow_client_dropped_on_queue_overflow() -> None:
    bus = _bus(queue_size=2, send_timeout_s=60.0)
    slow_ws = SlowWS()
    slow = bus.add_client(slow_ws, kind="state")
    bus.set_subscription(slow, _SUB)
    await asyncio.sleep(0)  # sender забрал кадр и «завис» в send_text
    for _ in range(4):
        bus.set_subscription(slow, _SUB)  # кадры копятся в очереди до переполнения
    await asyncio.sleep(0)  # дать отключению отработать
    assert slow_ws.closed == [WS_CLOSE_SLOW]
    assert bus.clients == ()


async def test_slow_client_does_not_block_others() -> None:
    bus = _bus(queue_size=2, send_timeout_s=60.0)
    slow_ws = SlowWS()
    fast_ws = FakeWS()
    slow = bus.add_client(slow_ws, kind="state")
    fast = bus.add_client(fast_ws, kind="state")
    bus.set_subscription(slow, _SUB)
    bus.set_subscription(fast, _SUB)
    await asyncio.sleep(0)
    assert len(fast_ws.sent) == 1  # быстрый получил кадр, несмотря на зависшего
    for _ in range(4):
        bus.set_subscription(slow, _SUB)
        bus.set_subscription(fast, _SUB)
        await asyncio.sleep(0)  # очередь быстрого успевает опустеть
    assert slow_ws.closed == [WS_CLOSE_SLOW]
    assert fast_ws.closed == []  # быстрый не отключён
    assert len(fast_ws.sent) == 5  # и продолжает получать кадры
    assert bus.clients == (fast,)


async def test_send_timeout_drops_stuck_client() -> None:
    bus = _bus(queue_size=8, send_timeout_s=0.05)
    slow_ws = SlowWS()
    slow = bus.add_client(slow_ws, kind="state")
    bus.set_subscription(slow, _SUB)
    await asyncio.sleep(0.2)  # таймаут отправки истёк
    assert slow_ws.closed == [WS_CLOSE_SLOW]
    assert bus.clients == ()


async def test_drop_is_idempotent() -> None:
    bus = _bus(queue_size=1, send_timeout_s=60.0)
    slow_ws = SlowWS()
    slow = bus.add_client(slow_ws, kind="state")
    bus.set_subscription(slow, _SUB)
    await asyncio.sleep(0)
    for _ in range(5):
        bus.set_subscription(slow, _SUB)  # несколько переполнений подряд
    await asyncio.sleep(0)
    assert slow_ws.closed == [WS_CLOSE_SLOW]  # close ровно один раз
    assert bus.clients == ()


def test_bus_validates_config() -> None:
    with pytest.raises(ValueError, match="state_hz"):
        _bus(state_hz=0)
    with pytest.raises(ValueError, match="queue_size"):
        _bus(queue_size=0)

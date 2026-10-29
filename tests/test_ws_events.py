"""TASK-404: /ws/events — зеркало журнала (ADR-005 п. 5).

Каждая запись журнала уходит кадром `{"type": "event", ...}` events-клиентам;
ошибка слушателя не прерывает `add` (инвариант 7). Подписка не нужна.
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.services import AppServices, build_services
from app.modbus.config import Config
from app.modbus.journal import Journal, JournalEntry


def _client(services: AppServices | None = None) -> TestClient:
    if services is None:
        services = build_services(Config())
    return TestClient(create_app(services=services, run_engine=False))


# --- подписка журнала (unit) ---------------------------------------------------------------


def test_journal_subscribe_notifies() -> None:
    journal = Journal()
    seen: list[JournalEntry] = []
    journal.subscribe(seen.append)
    entry = JournalEntry(action="write", slave=1, area="hr", address=5)
    journal.add(entry)
    assert seen == [entry]


def test_journal_unsubscribe() -> None:
    journal = Journal()
    seen: list[JournalEntry] = []
    unsubscribe = journal.subscribe(seen.append)
    unsubscribe()
    unsubscribe()  # идемпотентно
    journal.add(JournalEntry(action="write", slave=1))
    assert seen == []


def test_journal_listener_error_does_not_break_add() -> None:
    journal = Journal()
    seen: list[JournalEntry] = []

    def bad(_entry: JournalEntry) -> None:
        raise RuntimeError("слушатель сломался")

    journal.subscribe(bad)
    journal.subscribe(seen.append)  # следующий слушатель вызывается несмотря на ошибку
    journal.add(JournalEntry(action="write", slave=1))
    assert len(journal) == 1  # запись добавлена
    assert len(seen) == 1


# --- интеграция через WebSocket ------------------------------------------------------------


def test_ws_events_write() -> None:
    with _client() as client, client.websocket_connect("/ws/events") as ws:
        response = client.post(
            "/api/modbus/write",
            json={"slave": 1, "area": "hr", "address": 5, "value": 42},
        )
        assert response.status_code == 200
        frame = ws.receive_json()
    assert frame["type"] == "event"
    assert frame["action"] == "write"
    assert frame["slave"] == 1
    assert frame["area"] == "hr"
    assert frame["address"] == 5
    assert frame["value"] == 42
    assert frame["source"] == "web"
    assert frame["ip"] is not None
    assert frame["timestamp"] > 0


def test_ws_events_write_rejected_locked() -> None:
    with _client() as client, client.websocket_connect("/ws/events") as ws:
        response = client.put(
            "/api/modbus/behaviors/1/hr/5",
            json={"kind": "counter", "params": {"step": 2}, "locked": True},
        )
        assert response.status_code == 200
        response = client.post(
            "/api/modbus/write",
            json={"slave": 1, "area": "hr", "address": 5, "value": 42},
        )
        assert response.status_code == 409
        frames = [ws.receive_json() for _ in range(2)]
    assert [frame["action"] for frame in frames] == ["behavior_set", "write_rejected_locked"]
    rejected = frames[1]
    assert rejected["slave"] == 1
    assert rejected["area"] == "hr"
    assert rejected["address"] == 5
    assert rejected["source"] == "web"


def test_ws_events_inject() -> None:
    with _client() as client, client.websocket_connect("/ws/events") as ws:
        response = client.post(
            "/api/modbus/inject",
            json={"kind": "exception", "slave": 1, "code": 1},
        )
        assert response.status_code == 200
        frame = ws.receive_json()
    assert frame["type"] == "event"
    assert frame["action"] == "inject"
    assert frame["slave"] == 1
    assert frame["area"] is None  # событие уровня слейва
    assert frame["address"] is None
    assert frame["value"]["kind"] == "exception"


def test_ws_events_ignores_incoming() -> None:
    with _client() as client, client.websocket_connect("/ws/events") as ws:
        ws.send_text("мусор от клиента")
        ws.send_text(json.dumps({"slave": 1}))  # подписка не нужна — игнорируется
        response = client.post(
            "/api/modbus/write",
            json={"slave": 1, "area": "cl", "address": 1, "value": True},
        )
        assert response.status_code == 200
        frame = ws.receive_json()
    assert frame["action"] == "write"
    assert frame["area"] == "cl"
    assert frame["value"] is True

"""TASK-308: GET /api/modbus/journal — журнал событий (ring buffer, TASK-505)."""
from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.services import AppServices, build_services
from app.modbus.config import Config
from app.modbus.journal import JournalEntry


def _client(services: AppServices | None = None) -> TestClient:
    if services is None:
        services = build_services(Config())
    return TestClient(create_app(services=services, run_engine=False))


def test_journal_empty() -> None:
    with _client() as client:
        body = client.get("/api/modbus/journal").json()
    assert body == {"items": [], "total": 0}


def test_journal_lists_events_in_order() -> None:
    services = build_services(Config())
    with _client(services) as client:
        client.post("/api/modbus/write", json={"slave": 1, "area": "hr", "address": 5, "value": 42})
        client.put(
            "/api/modbus/behaviors/1/hr/7",
            json={"kind": "counter", "params": {"step": 1}},
        )
        body = client.get("/api/modbus/journal").json()
    assert body["total"] == 2
    items = body["items"]
    assert [item["action"] for item in items] == ["write", "behavior_set"]  # старые → новые
    first, second = items
    assert first["slave"] == 1
    assert first["area"] == "hr"
    assert first["address"] == 5
    assert first["value"] == 42
    assert first["source"] == "web"
    assert first["ip"] is not None
    assert isinstance(first["timestamp"], float)
    assert second["value"] == {"kind": "counter", "params": {"step": 1}, "locked": False}


def test_journal_limit_returns_last_entries() -> None:
    services = build_services(Config())
    with _client(services) as client:
        for address in (5, 6, 7):
            client.post(
                "/api/modbus/write",
                json={"slave": 1, "area": "hr", "address": address, "value": address},
            )
        body = client.get("/api/modbus/journal", params={"limit": 2}).json()
    assert body["total"] == 3
    assert [item["address"] for item in body["items"]] == [6, 7]


def test_journal_ring_buffer_evicts_old() -> None:
    services = build_services(replace(Config(), JOURNAL_SIZE=2))
    for value in (1, 2, 3):
        services.journal.add(
            JournalEntry(action="write", slave=1, area="hr", address=5, value=value)
        )
    with _client(services) as client:
        body = client.get("/api/modbus/journal").json()
    assert body["total"] == 2
    assert [item["value"] for item in body["items"]] == [2, 3]  # 1 вытеснен


def test_journal_bad_limit_rejected() -> None:
    with _client() as client:
        assert client.get("/api/modbus/journal", params={"limit": 0}).status_code == 422
        assert client.get("/api/modbus/journal", params={"limit": -1}).status_code == 422

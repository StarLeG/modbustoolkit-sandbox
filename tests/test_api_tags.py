"""TASK-718: REST пользовательских тегов.

`GET /api/modbus/tags`, `PUT /api/modbus/tags/{slave}/{area}/{address}`,
`DELETE /api/modbus/tags/{slave}/{area}/{address}`. Теги общие
(shared last-write-wins), действие из UI лимитируется общим бюджетом
`WEB_MAX_WRITE_PER_MIN_PER_IP`; запись в журнал — `tag_set` / `tag_cleared`.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.services import AppServices, build_services
from app.modbus.config import Config
from app.modbus.tags import MAX_TAG_LENGTH


def _client(services: AppServices | None = None) -> TestClient:
    if services is None:
        services = build_services(Config())
    return TestClient(create_app(services=services, run_engine=False))


# --- GET /tags ------------------------------------------------------------------------------


def test_get_tags_empty() -> None:
    with _client() as client:
        body = client.get("/api/modbus/tags").json()
    assert body == {"items": [], "total": 0}


def test_put_tag_sets_and_get_lists() -> None:
    with _client() as client:
        put = client.put("/api/modbus/tags/1/hr/5", json={"tag": "Насос 1"})
        body = client.get("/api/modbus/tags").json()
    assert put.status_code == 200
    assert put.json() == {"slave": 1, "area": "hr", "address": 5, "tag": "Насос 1"}
    assert body["total"] == 1
    assert body["items"] == [{"slave": 1, "area": "hr", "address": 5, "tag": "Насос 1"}]


def test_get_tags_sorted_by_binding() -> None:
    with _client() as client:
        client.put("/api/modbus/tags/2/hr/3", json={"tag": "второй"})
        client.put("/api/modbus/tags/1/di/9", json={"tag": "первый"})
        client.put("/api/modbus/tags/1/hr/7", json={"tag": "средний"})
        items = client.get("/api/modbus/tags").json()["items"]
    assert [(item["slave"], item["area"], item["address"]) for item in items] == [
        (1, "di", 9),
        (1, "hr", 7),
        (2, "hr", 3),
    ]


# --- PUT /tags/{slave}/{area}/{address} ------------------------------------------------------


def test_put_tag_strips_whitespace() -> None:
    with _client() as client:
        response = client.put("/api/modbus/tags/1/hr/5", json={"tag": "  Насос 1  "})
    assert response.json()["tag"] == "Насос 1"


def test_put_tag_rejects_blank() -> None:
    with _client() as client:
        response = client.put("/api/modbus/tags/1/hr/5", json={"tag": "   "})
    assert response.status_code == 400
    assert "не может быть пустым" in response.json()["detail"]


def test_put_tag_rejects_too_long() -> None:
    with _client() as client:
        response = client.put(
            "/api/modbus/tags/1/hr/5", json={"tag": "х" * (MAX_TAG_LENGTH + 1)}
        )
    assert response.status_code == 400
    assert "длиннее" in response.json()["detail"]


def test_put_tag_rejects_bad_binding() -> None:
    cases = [
        (2, "hr", 2, "вне карты"),  # чётный адрес — не сетка значений slave 2
        (10, "hr", 1, "вне карты"),  # slave 10 — только ir
        (1, "hr", 1025, "вне карты"),
    ]
    with _client() as client:
        for slave, area, address, match in cases:
            response = client.put(
                f"/api/modbus/tags/{slave}/{area}/{address}", json={"tag": "тег"}
            )
            assert response.status_code == 400, (slave, area, address)
            assert match in response.json()["detail"]


def test_put_tag_journals() -> None:
    services = build_services(Config())
    with _client(services) as client:
        client.put("/api/modbus/tags/1/hr/5", json={"tag": "Насос 1"})
    entries = [e for e in services.journal.entries() if e.action == "tag_set"]
    assert len(entries) == 1
    entry = entries[0]
    assert (entry.slave, entry.area, entry.address) == (1, "hr", 5)
    assert entry.value == "Насос 1"
    assert entry.source == "web"
    assert entry.ip is not None


# --- DELETE /tags/{slave}/{area}/{address} ---------------------------------------------------


def test_delete_tag_clears_and_journals() -> None:
    services = build_services(Config())
    with _client(services) as client:
        client.put("/api/modbus/tags/1/hr/5", json={"tag": "Насос 1"})
        response = client.delete("/api/modbus/tags/1/hr/5")
        body = client.get("/api/modbus/tags").json()
    assert response.status_code == 200
    assert response.json() == {"slave": 1, "area": "hr", "address": 5, "cleared": True}
    assert body == {"items": [], "total": 0}
    cleared = [e for e in services.journal.entries() if e.action == "tag_cleared"]
    assert len(cleared) == 1
    assert (cleared[0].slave, cleared[0].area, cleared[0].address) == (1, "hr", 5)


def test_delete_tag_missing_is_idempotent() -> None:
    services = build_services(Config())
    with _client(services) as client:
        response = client.delete("/api/modbus/tags/1/hr/5")
    assert response.status_code == 200
    assert response.json()["cleared"] is False
    assert not [e for e in services.journal.entries() if e.action == "tag_cleared"]


def test_delete_tag_rejects_bad_binding() -> None:
    with _client() as client:
        response = client.delete("/api/modbus/tags/10/hr/1")
    assert response.status_code == 400
    assert "вне карты" in response.json()["detail"]


# --- теги в состоянии и совместное использование ----------------------------------------------


def test_state_includes_tags() -> None:
    services = build_services(Config())
    with _client(services) as client:
        client.put("/api/modbus/tags/1/hr/5", json={"tag": "Насос 1"})
        body = client.get("/api/modbus/state", params={"slave": 1, "area": "hr"}).json()
    assert body["tags"] == {"hr": {"5": "Насос 1"}}


def test_state_tags_do_not_mix_areas() -> None:
    """Теги cl:5 и hr:5 независимы: кадр не смешивает одинаковые адреса областей."""
    services = build_services(Config())
    with _client(services) as client:
        client.put("/api/modbus/tags/1/hr/5", json={"tag": "HR-5"})
        client.put("/api/modbus/tags/1/cl/5", json={"tag": "CL-5"})
        full = client.get("/api/modbus/state", params={"slave": 1}).json()
        one = client.get("/api/modbus/state", params={"slave": 1, "area": "cl"}).json()
    assert full["tags"] == {"cl": {"5": "CL-5"}, "hr": {"5": "HR-5"}}
    assert one["tags"] == {"cl": {"5": "CL-5"}}


def test_tags_shared_last_write_wins() -> None:
    """Теги общие: последняя запись побеждает, хранилище не размножается."""
    with _client() as client:
        client.put("/api/modbus/tags/1/hr/5", json={"tag": "Первый"})
        client.put("/api/modbus/tags/1/hr/5", json={"tag": "Второй"})
        items = client.get("/api/modbus/tags").json()["items"]
    assert len(items) == 1
    assert items[0]["tag"] == "Второй"

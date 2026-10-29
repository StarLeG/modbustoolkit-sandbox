"""TASK-301/302: REST поведений (ADR-004).

TASK-301 — GET /api/modbus/behaviors: полный конфиг с фильтрами
slave/area/only_overrides. TASK-302 — PUT одной привязки: нормализация
value-параметров, журнал behavior_set, ошибки 400/422.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.services import AppServices, build_services
from app.modbus.behavior import Behavior
from app.modbus.config import Config
from app.modbus.types import AREAS, SLAVE_SPECS


def _expected_total() -> int:
    total = 0
    for slave, spec in SLAVE_SPECS.items():
        if slave == 10:
            continue
        for area in AREAS:
            for rng in spec.area_ranges(area):
                total += len(range(rng.start, rng.stop + 1, rng.step))
    return total


def _client(services: AppServices | None = None) -> TestClient:
    if services is None:
        services = build_services(Config())
    return TestClient(create_app(services=services, run_engine=False))


def test_get_behaviors_full_config() -> None:
    with _client() as client:
        response = client.get("/api/modbus/behaviors")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == _expected_total() == 11_392
    assert body["overrides"] == 0  # чистый реестр — всё дефолтное
    assert len(body["items"]) == 11_392
    by_key = {(item["slave"], item["area"], item["address"]): item for item in body["items"]}
    hr = by_key[(1, "hr", 1)]
    assert hr["kind"] == "constant"
    assert hr["params"] == {"value": 0}
    assert hr["locked"] is False
    assert hr["is_default"] is True
    ir = by_key[(1, "ir", 1)]
    assert ir["kind"] == "counter"
    assert ir["locked"] is True
    di = by_key[(2, "di", 64)]
    assert di["kind"] == "counter_bits"
    assert di["is_default"] is True


def test_get_behaviors_filters() -> None:
    services = build_services(Config())
    services.registry.set(1, "hr", 5, Behavior("counter", {"step": 2}, locked=True))
    with _client(services) as client:
        overrides = client.get("/api/modbus/behaviors", params={"only_overrides": True}).json()
        assert overrides["overrides"] == 1
        assert [item["address"] for item in overrides["items"]] == [5]

        one_slave = client.get("/api/modbus/behaviors", params={"slave": 2, "area": "ir"}).json()
        assert len(one_slave["items"]) == 512  # слейв 2: value-grid 1,3,...,1023
        assert all(item["slave"] == 2 and item["area"] == "ir" for item in one_slave["items"])

        slave10 = client.get("/api/modbus/behaviors", params={"slave": 10}).json()
        assert slave10["items"] == []  # slave 10 поведений не имеет
        assert slave10["total"] == 11_392


def test_get_behaviors_reflects_override() -> None:
    services = build_services(Config())
    services.registry.set(1, "hr", 5, Behavior("counter", {"step": 2}, locked=True))
    with _client(services) as client:
        items = client.get(
            "/api/modbus/behaviors", params={"slave": 1, "area": "hr"}
        ).json()["items"]
    by_address = {item["address"]: item for item in items}
    assert by_address[5]["kind"] == "counter"
    assert by_address[5]["params"] == {"step": 2}
    assert by_address[5]["locked"] is True
    assert by_address[5]["is_default"] is False
    assert by_address[6]["is_default"] is True  # соседняя привязка не тронута


def test_get_behaviors_bad_params_rejected() -> None:
    with _client() as client:
        assert client.get("/api/modbus/behaviors", params={"slave": 0}).status_code == 422
        assert client.get("/api/modbus/behaviors", params={"slave": 11}).status_code == 422
        assert client.get("/api/modbus/behaviors", params={"area": "zz"}).status_code == 422


# --- TASK-302: PUT /api/modbus/behaviors/{slave}/{area}/{address} -------------------------


def test_put_behavior_sets_override() -> None:
    services = build_services(Config())
    with _client(services) as client:
        response = client.put(
            "/api/modbus/behaviors/1/hr/5",
            json={"kind": "counter", "params": {"step": 2}, "locked": True},
        )
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "slave": 1,
        "area": "hr",
        "address": 5,
        "kind": "counter",
        "params": {"step": 2},
        "locked": True,
        "is_default": False,
    }
    stored = services.registry.get(1, "hr", 5)
    assert stored is not None
    assert stored.kind == "counter"
    assert stored.locked is True


def test_put_behavior_normalizes_params() -> None:
    """value=70000 на UINT16-слейве → 65535 (TASK-241, ADR-004)."""
    with _client() as client:
        response = client.put(
            "/api/modbus/behaviors/1/hr/5", json={"kind": "constant", "params": {"value": 70000}}
        )
    assert response.status_code == 200
    assert response.json()["params"] == {"value": 65535}


def test_put_behavior_journal_entry() -> None:
    services = build_services(Config())
    with _client(services) as client:
        client.put(
            "/api/modbus/behaviors/2/ir/3",
            json={"kind": "counter", "params": {"step": 1}},
        )
    entry = services.journal.entries()[-1]
    assert entry.action == "behavior_set"
    assert (entry.slave, entry.area, entry.address) == (2, "ir", 3)
    assert entry.source == "web"
    assert entry.ip is not None
    assert entry.value == {"kind": "counter", "params": {"step": 1}, "locked": False}


def test_put_behavior_domain_errors() -> None:
    cases = [
        ("/api/modbus/behaviors/1/hr/1", {"kind": "saw_wave"}, "неизвестный kind"),
        ("/api/modbus/behaviors/10/ir/1", {"kind": "counter"}, "slave 10"),
        ("/api/modbus/behaviors/2/hr/2", {"kind": "counter"}, "вне карты"),  # чётный адрес
        ("/api/modbus/behaviors/1/cl/1", {"kind": "counter"}, "не применим"),  # kind для hr/ir
        ("/api/modbus/behaviors/1/hr/5", {"kind": "toggle"}, "не применим"),  # kind для cl/di
    ]
    with _client() as client:
        for path, payload, match in cases:
            response = client.put(path, json=payload)
            assert response.status_code == 400, (path, payload, response.text)
            assert match in response.json()["detail"]


def test_put_behavior_schema_errors() -> None:
    with _client() as client:
        assert client.put("/api/modbus/behaviors/1/hr/5", json={}).status_code == 422  # нет kind
        assert (
            client.put("/api/modbus/behaviors/0/hr/5", json={"kind": "counter"}).status_code == 422
        )
        assert (
            client.put("/api/modbus/behaviors/1/zz/5", json={"kind": "counter"}).status_code == 422
        )


# --- TASK-303: POST /api/modbus/behaviors/batch -------------------------------------------


def test_batch_set_and_remove() -> None:
    services = build_services(Config())
    services.registry.set(1, "hr", 5, Behavior("counter", {"step": 9}))
    with _client(services) as client:
        response = client.post(
            "/api/modbus/behaviors/batch",
            json={
                "items": [
                    {"slave": 1, "area": "hr", "address": 5, "remove": True},
                    {"slave": 2, "area": "ir", "address": 3, "kind": "counter", "params": {"step": 2}},
                    {"slave": 1, "area": "cl", "address": 7, "kind": "toggle", "params": {"period_s": 1}},
                ]
            },
        )
    assert response.status_code == 200
    assert response.json() == {"applied": 2, "removed": 1}
    assert services.registry.get(1, "hr", 5).kind == "constant"  # сброшено к дефолту
    assert services.registry.get(2, "ir", 3).kind == "counter"
    assert services.registry.get(1, "cl", 7).kind == "toggle"


def test_batch_is_atomic() -> None:
    """Ошибка в элементе 1 → 400, элемент 0 не применяется."""
    services = build_services(Config())
    with _client(services) as client:
        response = client.post(
            "/api/modbus/behaviors/batch",
            json={
                "items": [
                    {"slave": 1, "area": "hr", "address": 5, "kind": "counter"},
                    {"slave": 2, "area": "hr", "address": 2, "kind": "counter"},  # чётный адрес
                ]
            },
        )
    assert response.status_code == 400
    assert "элемент 1" in response.json()["detail"]
    assert services.registry.get(1, "hr", 5) is not None
    assert services.registry.get(1, "hr", 5).kind == "constant"  # ничего не применилось


def test_batch_item_validation() -> None:
    with _client() as client:
        remove_with_kind = client.post(
            "/api/modbus/behaviors/batch",
            json={"items": [{"slave": 1, "area": "hr", "address": 5, "remove": True, "kind": "counter"}]},
        )
        assert remove_with_kind.status_code == 400
        assert "не сочетается" in remove_with_kind.json()["detail"]
        no_kind = client.post(
            "/api/modbus/behaviors/batch",
            json={"items": [{"slave": 1, "area": "hr", "address": 5}]},
        )
        assert no_kind.status_code == 400
        assert "kind обязателен" in no_kind.json()["detail"]


def test_batch_journal_per_item() -> None:
    services = build_services(Config())
    services.registry.set(1, "hr", 5, Behavior("counter"))
    with _client(services) as client:
        client.post(
            "/api/modbus/behaviors/batch",
            json={
                "items": [
                    {"slave": 1, "area": "hr", "address": 5, "remove": True},
                    {"slave": 1, "area": "hr", "address": 6, "kind": "constant", "params": {"value": 1}},
                ]
            },
        )
    entries = services.journal.entries()
    assert [entry.action for entry in entries] == ["behavior_reset", "behavior_set"]
    assert all(entry.source == "web" for entry in entries)
    assert entries[0].address == 5
    assert entries[1].value == {"kind": "constant", "params": {"value": 1}, "locked": False}


# --- TASK-304: POST /api/modbus/behaviors/reset -------------------------------------------


def test_reset_all() -> None:
    services = build_services(Config())
    services.registry.set(1, "hr", 5, Behavior("counter"))
    services.registry.set(1, "cl", 2, Behavior("toggle", {"period_s": 1}))
    services.registry.set(4, "ir", 7, Behavior("counter"))
    with _client(services) as client:
        response = client.post("/api/modbus/behaviors/reset", json={})
        again = client.post("/api/modbus/behaviors/reset", json={})
    assert response.status_code == 200
    assert response.json() == {"reset": 3}  # только переопределения
    assert again.json() == {"reset": 0}  # сбрасывать больше нечего
    assert services.registry.get(1, "hr", 5).kind == "constant"
    assert services.registry.get(1, "cl", 2).kind == "constant"  # дефолт CL: constant 0
    assert services.registry.get(4, "ir", 7).kind == "counter"


def test_reset_per_slave() -> None:
    services = build_services(Config())
    services.registry.set(1, "hr", 5, Behavior("counter"))
    services.registry.set(2, "ir", 3, Behavior("sawtooth"))
    with _client(services) as client:
        response = client.post("/api/modbus/behaviors/reset", json={"slave": 1})
    assert response.json() == {"reset": 1}
    assert services.registry.get(1, "hr", 5).kind == "constant"  # сброшено
    assert services.registry.get(2, "ir", 3).kind == "sawtooth"  # чужой слейв цел


def test_reset_rejects_slave_10() -> None:
    with _client() as client:
        response = client.post("/api/modbus/behaviors/reset", json={"slave": 10})
    assert response.status_code == 400
    assert "slave 10" in response.json()["detail"]


def test_reset_bad_slave_rejected() -> None:
    with _client() as client:
        assert client.post("/api/modbus/behaviors/reset", json={"slave": 0}).status_code == 422
        assert client.post("/api/modbus/behaviors/reset", json={"slave": 11}).status_code == 422


def test_reset_journal_per_binding() -> None:
    services = build_services(Config())
    services.registry.set(1, "hr", 5, Behavior("counter"))
    services.registry.set(1, "hr", 6, Behavior("counter"))
    with _client(services) as client:
        client.post("/api/modbus/behaviors/reset", json={"slave": 1})
    entries = services.journal.entries()
    assert [entry.action for entry in entries] == ["behavior_reset", "behavior_reset"]
    assert sorted(entry.address for entry in entries) == [5, 6]

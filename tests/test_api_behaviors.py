"""TASK-301: GET /api/modbus/behaviors — полный конфиг поведений (ADR-004).

Каркас `app/api/` + чтение реестра с фильтрами slave/area/only_overrides.
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

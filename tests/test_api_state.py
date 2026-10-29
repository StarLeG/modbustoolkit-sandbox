"""TASK-305: GET /api/modbus/state — снимок значений слейва (ADR-004 п. 3).

Значения по сетке адресов, декодированные по формату слейва; slave 10 —
только ir, IR 300 — Unix timestamp (UINT32_ABCD, docs/HRIR_SPEC.md).
"""
from __future__ import annotations

import time
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.services import build_services
from app.modbus.codec import encode
from app.modbus.config import Config
from app.modbus.types import RegisterFormat


def _client() -> TestClient:
    return TestClient(create_app(services=build_services(Config()), run_engine=False))


def test_state_uint16_slave() -> None:
    services = build_services(Config())
    services.maps[1].set_registers("hr", 5, [42])
    with TestClient(create_app(services=services, run_engine=False)) as client:
        response = client.get("/api/modbus/state", params={"slave": 1, "area": "hr"})
    assert response.status_code == 200
    body = response.json()
    assert body["slave"] == 1
    assert body["fmt"] == "uint16"
    values = body["areas"]["hr"]
    assert values["5"] == 42
    assert values["1"] == 0  # неинициализированные регистры — 0
    assert len(values) == 1024  # slave 1: HR 1..1024


def test_state_32bit_slave_decodes_values() -> None:
    services = build_services(Config())
    services.maps[2].set_registers("hr", 1, encode(0x12345678, RegisterFormat.UINT32_ABCD))
    with TestClient(create_app(services=services, run_engine=False)) as client:
        body = client.get("/api/modbus/state", params={"slave": 2, "area": "hr"}).json()
    values = body["areas"]["hr"]
    assert body["fmt"] == "uint32_abcd"
    assert values["1"] == 0x12345678  # декодировано из регистров 1..2
    assert len(values) == 512  # value-grid: 1,3,...,1023
    assert "2" not in values  # чётные адреса — не сетка значений


def test_state_bits() -> None:
    services = build_services(Config())
    services.maps[1].set_bits("cl", 3, [True])
    with TestClient(create_app(services=services, run_engine=False)) as client:
        body = client.get("/api/modbus/state", params={"slave": 1, "area": "cl"}).json()
    values = body["areas"]["cl"]
    assert values["3"] is True
    assert values["2"] is False
    assert len(values) == 64


def test_state_all_areas_by_default() -> None:
    with _client() as client:
        body = client.get("/api/modbus/state", params={"slave": 1}).json()
    assert set(body["areas"]) == {"cl", "di", "hr", "ir"}


def test_state_slave10_ir_only() -> None:
    with _client() as client:
        body = client.get("/api/modbus/state", params={"slave": 10}).json()
    assert body["fmt"] == "uint16"
    assert set(body["areas"]) == {"ir"}  # CL/DI/HR на slave 10 отсутствуют
    values = body["areas"]["ir"]
    assert len(values) == 269  # 1..268 + 300
    assert "301" not in values  # 300..301 — одно значение UINT32_ABCD
    assert values["1"] == datetime.now(UTC).year  # метрика года зоны 0 (UTC+00)
    assert abs(values["300"] - time.time()) < 5  # Unix timestamp ±0.5 с по спеке


def test_state_slave10_missing_area_rejected() -> None:
    with _client() as client:
        response = client.get("/api/modbus/state", params={"slave": 10, "area": "hr"})
    assert response.status_code == 400
    assert "отсутствует" in response.json()["detail"]


def test_state_bad_params_rejected() -> None:
    with _client() as client:
        assert client.get("/api/modbus/state").status_code == 422  # slave обязателен
        assert client.get("/api/modbus/state", params={"slave": 0}).status_code == 422
        assert client.get("/api/modbus/state", params={"slave": 11}).status_code == 422
        assert client.get("/api/modbus/state", params={"slave": 1, "area": "zz"}).status_code == 422

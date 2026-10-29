"""TASK-306: POST /api/modbus/write — запись CL/HR из UI (ADR-004 п. 4).

Rate-limit по IP (скользящее окно), locked → 409, ir/di недоступны,
значения приводятся к формату регистра.
"""
from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.rate_limit import WriteRateLimiter
from app.api.services import AppServices, build_services
from app.modbus.behavior import Behavior
from app.modbus.codec import encode
from app.modbus.config import Config
from app.modbus.types import RegisterFormat


def _client(services: AppServices | None = None) -> TestClient:
    if services is None:
        services = build_services(Config())
    return TestClient(create_app(services=services, run_engine=False))


# --- скользящее окно (unit) --------------------------------------------------------------


def test_rate_limiter_window_slides() -> None:
    now = [0.0]
    limiter = WriteRateLimiter(2, window_s=60.0, clock=lambda: now[0])
    assert limiter.allow("1.2.3.4")
    assert limiter.allow("1.2.3.4")
    assert not limiter.allow("1.2.3.4")  # лимит исчерпан
    assert limiter.allow("9.9.9.9")  # чужой IP не ограничен
    now[0] = 61.0
    assert limiter.allow("1.2.3.4")  # окно прошло — снова можно
    assert len(limiter) == 2


# --- запись значений ----------------------------------------------------------------------


def test_write_hr_value() -> None:
    services = build_services(Config())
    with _client(services) as client:
        response = client.post(
            "/api/modbus/write",
            json={"slave": 1, "area": "hr", "address": 5, "value": 42},
        )
    assert response.status_code == 200
    assert response.json() == {"slave": 1, "area": "hr", "address": 5, "value": 42}
    assert services.maps[1].get_registers("hr", 5, 1) == [42]
    entry = services.journal.entries()[-1]
    assert entry.action == "write"
    assert entry.source == "web"
    assert entry.value == 42
    assert entry.ip is not None


def test_write_hr_normalizes_to_format() -> None:
    services = build_services(Config())
    with _client(services) as client:
        response = client.post(
            "/api/modbus/write",
            json={"slave": 1, "area": "hr", "address": 5, "value": 70000},
        )
    assert response.json()["value"] == 65535  # clamp UINT16
    assert services.maps[1].get_registers("hr", 5, 1) == [65535]


def test_write_hr_32bit_encodes_registers() -> None:
    services = build_services(Config())
    with _client(services) as client:
        response = client.post(
            "/api/modbus/write",
            json={"slave": 2, "area": "hr", "address": 1, "value": 0x12345678},
        )
    assert response.json()["value"] == 0x12345678
    assert services.maps[2].get_registers("hr", 1, 2) == encode(
        0x12345678, RegisterFormat.UINT32_ABCD
    )


def test_write_cl_bool() -> None:
    services = build_services(Config())
    with _client(services) as client:
        response = client.post(
            "/api/modbus/write",
            json={"slave": 1, "area": "cl", "address": 3, "value": True},
        )
    assert response.json()["value"] is True
    assert services.maps[1].get_bits("cl", 3, 1) == [True]


def test_write_locked_conflict() -> None:
    services = build_services(Config())
    services.registry.set(1, "hr", 5, Behavior("constant", {"value": 0}, locked=True))
    with _client(services) as client:
        response = client.post(
            "/api/modbus/write",
            json={"slave": 1, "area": "hr", "address": 5, "value": 7},
        )
    assert response.status_code == 409
    assert "заблокирована" in response.json()["detail"]
    assert services.maps[1].get_registers("hr", 5, 1) == [0]  # не записано
    entry = services.journal.entries()[-1]
    assert entry.action == "write_rejected_locked"
    assert entry.source == "web"  # атрибуция UI-записи (TASK-306)


def test_write_rejects_readonly_and_bad_values() -> None:
    with _client() as client:
        cases = [
            ({"slave": 1, "area": "ir", "address": 5, "value": 1}, "только для cl/hr"),
            ({"slave": 1, "area": "di", "address": 5, "value": True}, "только для cl/hr"),
            ({"slave": 1, "area": "cl", "address": 5, "value": 1}, "ожидается bool"),
            ({"slave": 1, "area": "hr", "address": 5, "value": True}, "ожидается число"),
            ({"slave": 2, "area": "hr", "address": 2, "value": 1}, "вне карты"),  # чётный
            ({"slave": 10, "area": "hr", "address": 1, "value": 1}, "вне карты"),
        ]
        for payload, match in cases:
            response = client.post("/api/modbus/write", json=payload)
            assert response.status_code == 400, (payload, response.text)
            assert match in response.json()["detail"]


def test_write_rate_limit() -> None:
    services = build_services(replace(Config(), WEB_MAX_WRITE_PER_MIN_PER_IP=2))
    with _client(services) as client:
        ok = client.post(
            "/api/modbus/write", json={"slave": 1, "area": "hr", "address": 5, "value": 1}
        )
        ok2 = client.post(
            "/api/modbus/write", json={"slave": 1, "area": "hr", "address": 6, "value": 2}
        )
        denied = client.post(
            "/api/modbus/write", json={"slave": 1, "area": "hr", "address": 7, "value": 3}
        )
        read = client.get("/api/modbus/state", params={"slave": 1, "area": "hr"})
    assert ok.status_code == 200
    assert ok2.status_code == 200
    assert denied.status_code == 429
    assert read.status_code == 200  # чтение лимитом не ограничено

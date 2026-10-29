"""TASK-309: POST /api/modbus/inject и /inject/clear (ADR-004 п. 6).

Регистрация и снятие инъекций ошибок; влияние на Modbus-запросы — Эпик 6.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.services import AppServices, build_services
from app.modbus.config import Config


def _client(services: AppServices | None = None) -> TestClient:
    if services is None:
        services = build_services(Config())
    return TestClient(create_app(services=services, run_engine=False))


def test_inject_exception_created() -> None:
    services = build_services(Config())
    with _client(services) as client:
        response = client.post(
            "/api/modbus/inject",
            json={"kind": "exception", "slave": 1, "code": 2, "ttl_s": 60},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 1
    assert body["kind"] == "exception"
    assert body["slave"] == 1
    assert body["code"] == 2
    assert body["ttl_s"] == 60
    assert body["expires_at"] == body["created_at"] + 60
    assert body["area"] is None and body["address"] is None and body["count"] is None
    assert body["delay_ms"] is None
    entry = services.journal.entries()[-1]
    assert entry.action == "inject"
    assert entry.source == "web"
    assert entry.ip is not None
    assert entry.value == {"id": 1, "kind": "exception", "code": 2, "delay_ms": None, "ttl_s": 60.0}


def test_inject_delay_and_timeout() -> None:
    with _client() as client:
        delay = client.post(
            "/api/modbus/inject",
            json={"kind": "delay", "slave": 2, "delay_ms": 250},
        ).json()
        timeout = client.post(
            "/api/modbus/inject",
            json={"kind": "timeout", "slave": 3, "ttl_s": 30},
        ).json()
    assert delay["delay_ms"] == 250
    assert delay["code"] is None
    assert delay["ttl_s"] == 300  # без ttl_s — INJECTION_MAX_TTL_S
    assert timeout["code"] is None and timeout["delay_ms"] is None
    assert timeout["ttl_s"] == 30  # для timeout ttl_s — длительность молчания


def test_inject_ttl_clamped_to_max() -> None:
    with _client() as client:
        body = client.post(
            "/api/modbus/inject",
            json={"kind": "timeout", "slave": 1, "ttl_s": 9999},
        ).json()
    assert body["ttl_s"] == 300  # Config.INJECTION_MAX_TTL_S по умолчанию


def test_inject_target_range() -> None:
    with _client() as client:
        body = client.post(
            "/api/modbus/inject",
            json={
                "kind": "exception",
                "slave": 2,
                "area": "hr",
                "address": 1,
                "count": 2,
                "code": 4,
            },
        ).json()
    assert body["area"] == "hr"
    assert body["address"] == 1
    assert body["count"] == 2


def test_inject_domain_errors() -> None:
    cases = [
        ({"kind": "exception", "slave": 1}, "требуется code"),
        ({"kind": "exception", "slave": 1, "code": 5}, "недопустимый код"),
        ({"kind": "delay", "slave": 1, "delay_ms": 6000}, "вне диапазона"),
        ({"kind": "exception", "slave": 1, "code": 1, "count": 2}, "count требует address"),
        ({"kind": "exception", "slave": 2, "code": 1, "area": "hr", "address": 2}, "вне карты"),
    ]
    with _client() as client:
        for payload, match in cases:
            response = client.post("/api/modbus/inject", json=payload)
            assert response.status_code == 400, (payload, response.text)
            assert match in response.json()["detail"]


def test_inject_schema_errors() -> None:
    cases = [
        {"kind": "zzz", "slave": 1},
        {"kind": "exception", "slave": 0, "code": 1},
        {"kind": "exception", "slave": 11, "code": 1},
        {"kind": "timeout", "slave": 1, "ttl_s": 0},
    ]
    with _client() as client:
        for payload in cases:
            assert client.post("/api/modbus/inject", json=payload).status_code == 422, payload


def test_inject_clear_filters() -> None:
    services = build_services(Config())
    with _client(services) as client:
        client.post("/api/modbus/inject", json={"kind": "exception", "slave": 1, "code": 1})
        client.post("/api/modbus/inject", json={"kind": "delay", "slave": 1, "delay_ms": 10})
        client.post("/api/modbus/inject", json={"kind": "exception", "slave": 2, "code": 4})
        by_slave = client.post("/api/modbus/inject/clear", json={"slave": 1}).json()
        rest = client.post("/api/modbus/inject/clear", json={}).json()
        empty = client.post("/api/modbus/inject/clear", json={}).json()
    assert by_slave == {"cleared": 2}
    assert rest == {"cleared": 1}
    assert empty == {"cleared": 0}
    entries = [e for e in services.journal.entries() if e.action == "inject_clear"]
    assert len(entries) == 3  # по одной записи на снятую инъекцию
    assert all(e.source == "web" for e in entries)


def test_inject_clear_by_id() -> None:
    services = build_services(Config())
    with _client(services) as client:
        first = client.post(
            "/api/modbus/inject", json={"kind": "exception", "slave": 1, "code": 1}
        ).json()
        client.post("/api/modbus/inject", json={"kind": "exception", "slave": 1, "code": 1})
        response = client.post("/api/modbus/inject/clear", json={"id": first["id"]})
    assert response.json() == {"cleared": 1}
    assert [inj.id for inj in services.injections.items()] == [2]

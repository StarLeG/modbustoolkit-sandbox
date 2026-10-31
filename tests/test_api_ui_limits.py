"""TASK-1004: лимиты публичного REST UI (ADR-012).

Все мутирующие действия из UI (поведения, снимки, инъекции) считаются одним
окном `WEB_MAX_WRITE_PER_MIN_PER_IP` на IP и отвечают 429 при переполнении;
чтение лимитом не ограничено. Пакет поведений ограничен `MAX_BATCH_ITEMS`.
"""
from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.models import MAX_BATCH_ITEMS
from app.api.services import AppServices, build_services
from app.modbus.config import Config


def _client(services: AppServices | None = None) -> TestClient:
    if services is None:
        services = build_services(Config())
    return TestClient(create_app(services=services, run_engine=False))


# (method, path, payload) — каждая пара мутирует состояние сервера.
MUTATIONS: list[tuple[str, str, dict[str, object]]] = [
    ("PUT", "/api/modbus/behaviors/1/hr/5", {"kind": "counter"}),
    (
        "POST",
        "/api/modbus/behaviors/batch",
        {"items": [{"slave": 1, "area": "hr", "address": 5, "kind": "counter"}]},
    ),
    ("POST", "/api/modbus/behaviors/reset", {}),
    ("POST", "/api/modbus/restore", {"slaves": {"1": {"hr": {"5": 42}}}}),
    ("POST", "/api/modbus/inject", {"kind": "timeout", "slave": 1}),
    ("POST", "/api/modbus/inject/clear", {}),
]


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    MUTATIONS,
    ids=[f"{method} {path}" for method, path, _ in MUTATIONS],
)
def test_mutation_endpoints_rate_limited(
    method: str,
    path: str,
    payload: dict[str, object],
) -> None:
    """Каждый мутирующий эндпоинт ограничен общим лимитом действий из UI."""
    services = build_services(replace(Config(), WEB_MAX_WRITE_PER_MIN_PER_IP=2))
    with _client(services) as client:
        first = client.request(method, path, json=payload)
        second = client.request(method, path, json=payload)
        denied = client.request(method, path, json=payload)
        read = client.get("/api/modbus/state", params={"slave": 1})
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert denied.status_code == 429
    assert read.status_code == 200  # чтение не ограничено


def test_mutation_budget_shared_with_writes() -> None:
    """Запись и изменения поведений тратят одно окно на IP (TASK-1004)."""
    services = build_services(replace(Config(), WEB_MAX_WRITE_PER_MIN_PER_IP=2))
    with _client(services) as client:
        write = client.post(
            "/api/modbus/write", json={"slave": 1, "area": "hr", "address": 5, "value": 1}
        )
        put = client.put("/api/modbus/behaviors/1/hr/5", json={"kind": "counter"})
        denied = client.post("/api/modbus/inject", json={"kind": "timeout", "slave": 1})
    assert write.status_code == 200
    assert put.status_code == 200
    assert denied.status_code == 429


def test_mutation_rate_limit_is_per_ip() -> None:
    """Окно действий считается по IP (TASK-1004), как и окно записей (TASK-503)."""
    services = build_services(replace(Config(), WEB_MAX_WRITE_PER_MIN_PER_IP=1))
    app = create_app(services=services, run_engine=False)
    with (
        TestClient(app, client=("1.2.3.4", 1)) as first,
        TestClient(app, client=("5.6.7.8", 2)) as second,
    ):
        ok = first.put("/api/modbus/behaviors/1/hr/5", json={"kind": "counter"})
        denied = first.put("/api/modbus/behaviors/1/hr/5", json={"kind": "counter"})
        ok_other = second.put("/api/modbus/behaviors/1/hr/5", json={"kind": "counter"})
    assert ok.status_code == 200
    assert denied.status_code == 429  # лимит исчерпан для 1.2.3.4
    assert ok_other.status_code == 200  # другой IP работает в своём окне


def test_batch_items_capped() -> None:
    """Пакет больше MAX_BATCH_ITEMS отклоняется схемой (422, TASK-1004)."""
    with _client() as client:
        response = client.post(
            "/api/modbus/behaviors/batch",
            json={
                "items": [
                    {"slave": 1, "area": "hr", "address": 5, "kind": "counter"}
                    for _ in range(MAX_BATCH_ITEMS + 1)
                ]
            },
        )
    assert response.status_code == 422


def test_batch_accepts_ui_range_size() -> None:
    """Диапазон UI целиком (до 1024 адресов) проходит в одном пакете."""
    with _client() as client:
        response = client.post(
            "/api/modbus/behaviors/batch",
            json={
                "items": [
                    {"slave": 1, "area": "hr", "address": address, "kind": "constant"}
                    for address in range(1, 1025)
                ]
            },
        )
    assert response.status_code == 200
    assert response.json() == {"applied": 1024, "removed": 0}

"""UI «Инъекции ошибок» + REST списка активных инъекций (TASK-743).

Страница: форма добавления (exception / delay / timeout; весь слейв или
диапазон адресов) и список активных инъекций с TTL. Список наполняет
`static/injections.js` через `GET /api/modbus/inject`; снятие —
`POST /api/modbus/inject/clear`.
"""
from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.services import AppServices, build_services
from app.modbus.config import Config


def _client(services: AppServices | None = None) -> TestClient:
    if services is None:
        services = build_services(Config())
    return TestClient(create_app(services=services, run_engine=False))


# --------------------------------------------------------------------------- UI


def test_injections_page_has_form_and_list() -> None:
    """Форма добавления, таблица активных и кнопка сброса (TASK-605, 743)."""
    with _client() as client:
        response = client.get("/injections")
    assert response.status_code == 200
    html = response.text
    for fragment in (
        "Инъекции ошибок",
        'id="inj-kind"',
        'id="inj-slave"',
        'id="inj-scope"',
        'id="inj-area"',
        'id="inj-address"',
        'id="inj-count"',
        'id="inj-code"',
        'id="inj-delay"',
        'id="inj-ttl"',
        'id="inj-add"',
        "Сбросить инъекции",
        'id="inj-status"',
        'id="inj-rows"',
        "/static/injections.js",
        "0x04 Server Device Failure",
        "timeout",
    ):
        assert fragment in html, fragment


def test_injections_page_has_shared_rules() -> None:
    """Плашка с правилами: инъекции общие, TTL из конфига, RU и EN."""
    config = replace(Config(), INJECTION_MAX_TTL_S=120)
    with _client(build_services(config)) as client:
        ru = client.get("/injections").text
        en = client.get("/injections?lang=en").text
    for fragment in (
        'class="banner inj-rules"',
        "Инъекции общие для всех.",
        "учебную аварию",
        "максимум, 120 с",
        "диапазона адресов",
        "кнопкой ✕",
        "«Журнале»",
    ):
        assert fragment in ru, fragment
    for fragment in (
        'class="banner inj-rules"',
        "Injections are shared by everyone.",
        "the maximum, 120 s",
        "«Journal»",
    ):
        assert fragment in en, fragment


def test_injections_js_served() -> None:
    with _client() as client:
        response = client.get("/static/injections.js")
    assert response.status_code == 200
    js = response.text
    for fragment in (
        "/api/modbus/inject",
        "/api/modbus/inject/clear",
        "inj-kind",
        "loadList",
        "renderRows",
    ):
        assert fragment in js, fragment


# --------------------------------------------------------------------- REST: список


def test_inject_list_empty() -> None:
    with _client() as client:
        body = client.get("/api/modbus/inject").json()
    assert body["items"] == []
    assert body["max_ttl_s"] == 300  # Config.INJECTION_MAX_TTL_S по умолчанию


def test_inject_list_returns_active_injections() -> None:
    """Список в порядке добавления, с остатком TTL (TASK-743)."""
    with _client() as client:
        client.post(
            "/api/modbus/inject",
            json={"kind": "exception", "slave": 1, "code": 2, "ttl_s": 60},
        )
        client.post(
            "/api/modbus/inject",
            json={"kind": "delay", "slave": 2, "delay_ms": 250},
        )
        body = client.get("/api/modbus/inject").json()
    items = body["items"]
    assert [item["id"] for item in items] == [1, 2]
    assert items[0]["kind"] == "exception" and items[0]["code"] == 2
    assert items[1]["kind"] == "delay" and items[1]["delay_ms"] == 250
    for item in items:
        assert 0 < item["remaining_s"] <= item["ttl_s"] + 1


def test_inject_list_reflects_clear() -> None:
    services = build_services(Config())
    with _client(services) as client:
        first = client.post(
            "/api/modbus/inject", json={"kind": "exception", "slave": 1, "code": 1}
        ).json()
        client.post("/api/modbus/inject", json={"kind": "exception", "slave": 1, "code": 1})
        client.post("/api/modbus/inject/clear", json={"id": first["id"]})
        items = client.get("/api/modbus/inject").json()["items"]
    assert [item["id"] for item in items] == [2]

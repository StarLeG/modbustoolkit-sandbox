"""UI «Журнал событий» (TASK-745): последние 1000 записей + live /ws/events.

Страница наполняется из `static/journal.js`: стартовый снимок через
`GET /api/modbus/journal?limit=1000`, новые события — кадрами `/ws/events`
(TASK-404). Сам REST журнала покрыт `test_api_journal.py` (TASK-308).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.services import build_services
from app.modbus.config import Config


def _client() -> TestClient:
    return TestClient(create_app(services=build_services(Config()), run_engine=False))


def test_journal_page_has_table_and_controls() -> None:
    """Страница /journal: кнопка обновления и таблица записей (TASK-745)."""
    with _client() as client:
        response = client.get("/journal")
    assert response.status_code == 200
    html = response.text
    for fragment in (
        "Журнал событий",
        'id="journal-refresh"',
        'id="journal-status"',
        'id="journal-rows"',
        "/static/journal.js",
        "Время",
        "Источник",
        "Действие",
        "Unit ID",
        "Значение",
        "/ws/events",
        "GET /api/modbus/journal",
    ):
        assert fragment in html, fragment


def test_journal_js_served() -> None:
    with _client() as client:
        response = client.get("/static/journal.js")
    assert response.status_code == 200
    js = response.text
    for fragment in (
        "/api/modbus/journal?limit=",
        "/ws/events",
        "journal-rows",
        "eventKey",
        "prepend",
        "WebSocket",
    ):
        assert fragment in js, fragment


def test_journal_js_caps_at_limit() -> None:
    """Клиент держит не более 1000 строк (JOURNAL_SIZE) (TASK-745)."""
    with _client() as client:
        js = client.get("/static/journal.js").text
    assert "const LIMIT = 1000;" in js
    assert "shownKeys.length > LIMIT" in js

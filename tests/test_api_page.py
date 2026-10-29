"""TASK-504: плашка «публичный сервер» на стартовой странице (ADR-001 п. 4).

Обязательный текст — про многопользовательский режим и перезапись значений
(DoD 10); константа `WARNING_BANNER` переиспользуется UI (Эпик 7).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.pages import WARNING_BANNER
from app.api.services import build_services
from app.modbus.config import Config


def _client() -> TestClient:
    return TestClient(create_app(services=build_services(Config()), run_engine=False))


def test_index_has_public_warning() -> None:
    with _client() as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text
    assert WARNING_BANNER in html  # обязательный текст плашки целиком
    for phrase in ("многопользователь", "перезапис", "last-write-wins", "в памяти", "лимит"):
        assert phrase in html, phrase


def test_index_shows_connection_info() -> None:
    with _client() as client:
        response = client.get("/")
    html = response.text
    assert "5020" in html  # порт Modbus
    assert "1..10" in html  # диапазон unit ID


def test_index_has_clear_injections_button() -> None:
    """Кнопка «Сбросить инъекции» → REST /inject/clear (TASK-605)."""
    with _client() as client:
        response = client.get("/")
    html = response.text
    assert "Сбросить инъекции" in html
    assert "/api/modbus/inject/clear" in html

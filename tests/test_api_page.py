"""UI-страницы: плашка «публичный сервер» (TASK-504) и «Обзор» (TASK-701).

Обязательный текст плашки — про многопользовательский режим и перезапись
значений (DoD 10); константа `WARNING_BANNER` — единственный источник текста
(ADR-008 п. 2). «Обзор» — параметры подключения и таблица форматов unit ID
1..10 из `SLAVE_SPECS`.
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


def test_injections_page_has_clear_button() -> None:
    """Кнопка «Сбросить инъекции» → REST /inject/clear (TASK-605).

    С ADR-008 кнопка живёт на странице «Инъекции» (на «Обзоре» — только ссылка
    на раздел через навигацию).
    """
    with _client() as client:
        response = client.get("/injections")
    html = response.text
    assert "Сбросить инъекции" in html
    assert "/api/modbus/inject/clear" in html


def test_index_has_formats_table() -> None:
    """Таблица форматов и адресов unit ID 1..10 (TASK-701, HRIR_SPEC)."""
    with _client() as client:
        response = client.get("/")
    html = response.text
    for fragment in (
        "uint16",
        "uint32 ABCD",
        "uint32 CDAB",
        "uint32 BADC",
        "uint32 DCBA",
        "float ABCD",
        "float CDAB",
        "float BADC",
        "float DCBA",
        "1..1024",  # slave 1: HR/IR
        "1,3,…,1023",  # slaves 2..9: сетка значений
        "1..268",  # slave 10: зоны часовых поясов
        "300..301",  # slave 10: Unix timestamp
    ):
        assert fragment in html, fragment
    assert html.count("<tr>") == 11  # шапка таблицы + 10 слейвов


def test_all_sections_serve_shell_with_banner() -> None:
    """Все разделы доступны и несут обязательную плашку (ADR-008 п. 2)."""
    with _client() as client:
        for path in (
            "/",
            "/registers",
            "/behaviors",
            "/timezones",
            "/injections",
            "/tools",
            "/docs",
        ):
            response = client.get(path)
            assert response.status_code == 200, path
            assert WARNING_BANNER in response.text, path


def test_static_assets_mounted() -> None:
    """Статика UI (ADR-008 п. 3) отдаётся с /static."""
    with _client() as client:
        response = client.get("/static/app.css")
    assert response.status_code == 200
    assert "banner" in response.text


def test_api_docs_moved_under_api() -> None:
    """Swagger/redoc — под /api/*, корневой /docs — страница UI (ADR-008 п. 2)."""
    with _client() as client:
        assert client.get("/api/docs").status_code == 200
        assert client.get("/api/openapi.json").status_code == 200
        assert "Раздел в разработке" in client.get("/docs").text

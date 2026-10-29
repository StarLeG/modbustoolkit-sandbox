"""UI «Документация» (TASK-751): форматы, ограничения, многопользовательность.

Страница рендерится сервером; таблица форматов — из `SLAVE_SPECS` (та же, что
на «Обзоре»), лимиты — из `Config` окружения.
"""
from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.services import build_services
from app.modbus.config import Config


def _client(config: Config | None = None) -> TestClient:
    return TestClient(
        create_app(services=build_services(config or Config()), run_engine=False)
    )


def test_docs_page_has_formats_spec() -> None:
    """Спецификация форматов: таблица слейвов и объяснение порядков (TASK-751)."""
    with _client() as client:
        response = client.get("/docs")
    assert response.status_code == 200
    html = response.text
    for fragment in (
        "Спецификация форматов",
        "<code>ABCD</code>",
        "<code>CDAB</code>",
        "<code>BADC</code>",
        "<code>DCBA</code>",
        "swap bytes",
        "uint32 CDAB",
        "float DCBA",
        "1,3,…,1023",
        "300..301",
        "Unix timestamp",
        "docs/HRIR_SPEC.md",
    ):
        assert fragment in html, fragment


def test_docs_page_has_limits() -> None:
    """Ограничения: функции, лимиты из Config, память (TASK-751)."""
    with _client() as client:
        html = client.get("/docs").text
    for fragment in (
        "Ограничения",
        "1, 2, 3, 4, 5, 6, 16",
        "0x01",
        "0x02",
        "0x03",
        "2 регистров",
        "INJECTION_MAX_TTL_S",
        "shared last-write-wins",
    ):
        assert fragment in html, fragment
    # Лимиты подставляются из Config (дефолты CLAUDE.md)
    for value in ("20", "200", "50", "300", "1000"):
        assert value in html, value


def test_docs_page_limits_follow_config() -> None:
    """Значения лимитов берутся из окружения, а не захардкожены (TASK-751)."""
    config = replace(Config(), WEB_MAX_WRITE_PER_MIN_PER_IP=7, JOURNAL_SIZE=42)
    with _client(config) as client:
        html = client.get("/docs").text
    assert "записей из UI в минуту на IP — 7" in html
    assert "последние 42 событий" in html


def test_docs_page_has_multiplayer_rules() -> None:
    """Правила многопользовательскости (TASK-751, инвариант 1)."""
    with _client() as client:
        html = client.get("/docs").text
    for fragment in (
        "Правила многопользовательскости",
        "HR и CL",
        "shared last-write-wins",
        "IR и DI",
        "только для чтения",
        "Аутентификации нет",
    ):
        assert fragment in html, fragment


def test_docs_page_has_api_pointers() -> None:
    with _client() as client:
        html = client.get("/docs").text
    for fragment in ("<h3>API</h3>", "/api/docs", "/ws/state", "/ws/events"):
        assert fragment in html, fragment

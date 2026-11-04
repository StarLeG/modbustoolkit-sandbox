"""UI «Документация» (TASK-751): форматы, ограничения, многопользовательность.

Страница рендерится сервером; таблица форматов — из `SLAVE_SPECS` (та же, что
на «Обзоре»), лимиты — из `Config` окружения.
"""
from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.pages import (
    AUTHOR_URL,
    ELECTROMAN_URL,
    OPCUA_SANDBOX_URL,
    PRODUCT_URL,
    SUPPORT_URL,
)
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
    assert "записей из UI в минуту на IP - 7" in html
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


def test_docs_page_has_code_examples() -> None:
    """Примеры кода с кнопками копирования (TASK-752)."""
    with _client() as client:
        html = client.get("/docs").text
    for fragment in (
        "Примеры кода",
        "pymodbus",
        "modbus-serial",
        "NModbus",
        'id="example-python"',
        'id="example-nodejs"',
        'id="example-csharp"',
        'data-copy="example-python"',
        'data-copy="example-nodejs"',
        'data-copy="example-csharp"',
        "Копировать",
        "/static/examples.js",
    ):
        assert fragment in html, fragment


def test_docs_page_has_beginner_guide_and_usecases() -> None:
    """Руководство для начинающих и примеры использования простым языком."""
    with _client() as client:
        html = client.get("/docs").text
    for fragment in (
        "Руководство для начинающих",
        "Примеры использования",
        "Первая проверка по шагам",
        "unit ID",
        "uint32 CDAB",
        "float DCBA",
        "Unix timestamp",
    ):
        assert fragment in html, fragment


def test_docs_page_examples_have_syntax_highlighting() -> None:
    """Примеры кода размечены под highlight.js, скрипт подключён на странице."""
    with _client() as client:
        html = client.get("/docs").text
    for fragment in (
        'class="language-python" id="example-python"',
        'class="language-javascript" id="example-nodejs"',
        'class="language-csharp" id="example-csharp"',
        "highlight.js/11.9.0/highlight.min.js",
    ):
        assert fragment in html, fragment


def test_docs_page_examples_addressing() -> None:
    """Примеры: хост, порт, device_id, shared last-write-wins (TASK-752)."""
    with _client() as client:
        html = client.get("/docs").text
    for fragment in (
        "modbus-sendbox.electroman.by",
        "5020",
        "device_id=1",
        "read_holding_registers",
        "write_registers",
        "не более 2 регистров",
        "readHoldingRegisters",
        "ReadHoldingRegisters",
        "WriteSingleRegister",
        "shared last-write-wins",
    ):
        assert fragment in html, fragment


def test_docs_page_endpoint_follows_config() -> None:
    """Хост и порт в руководстве и примерах - из окружения, как на «Обзоре»."""
    config = replace(Config(), MODBUS_PUBLIC_HOST="bench.example.org", MODBUS_TCP_PORT=1502)
    with _client(config) as client:
        for path in ("/docs", "/docs?lang=en"):
            html = client.get(path).text
            assert "<code>bench.example.org</code>" in html, path
            assert "<code>1502</code>" in html, path
            assert "ModbusTcpClient(&quot;bench.example.org&quot;, port=1502)" in html, path
            assert "connectTCP(&quot;bench.example.org&quot;, { port: 1502 })" in html, path
            assert "TcpClient(&quot;bench.example.org&quot;, 1502)" in html, path
            assert "{host}" not in html and "{port}" not in html, path
            # В разделе «Исходный код» 5020 - дефолт своей установки, не этого сервера
            about_this_server = html.split('id="source-clone"')[0]
            assert "5020" not in about_this_server, path


def test_docs_page_has_source_and_local_setup() -> None:
    """Раздел «Исходный код»: ссылка на GitHub, клонирование, запуск (RU и EN)."""
    with _client() as client:
        ru = client.get("/docs").text
        en = client.get("/docs?lang=en").text
    for html, title in ((ru, "Исходный код и свой сервер"), (en, "Source code and your own server")):
        for fragment in (
            title,
            'href="https://github.com/StarLeG/modbustoolkit-sandbox"',
            "git clone https://github.com/StarLeG/modbustoolkit-sandbox.git",
            "cd modbustoolkit-sandbox",
            "uv run python -m app",
            "docker compose up -d --build",
            "pip install .",
            "MODBUS_PUBLIC_HOST",
            'class="language-bash" id="source-clone"',
            'data-copy="source-clone"',
            "docs/DEPLOY.md",
        ):
            assert fragment in html, fragment


def test_footer_links_to_github() -> None:
    """Ссылка на репозиторий есть в футере каждой страницы."""
    with _client() as client:
        for path in ("/", "/registers", "/injections", "/docs"):
            html = client.get(path).text
            assert 'href="https://github.com/StarLeG/modbustoolkit-sandbox"' in html, path
            assert "Исходный код на GitHub" in html, path


def test_footer_has_ecosystem_groups() -> None:
    """Футер в стиле экосистемы Electroman: 4 группы ссылок (как на opcua-sendbox)."""
    with _client() as client:
        html = client.get("/").text
    for fragment in (
        'class="foot-top"',
        'class="foot-grid"',
        "<span class=\"eyebrow\">ModbusToolkit</span>",
        "Экосистема Electroman",
        "<span class=\"eyebrow\">Поддержка</span>",
        "Modbus Sandbox · этот сервер",
    ):
        assert fragment in html, fragment
    assert "Донаты идут на испытательный стенд" not in html  # строка заменена автором


def test_footer_links_ecosystem_and_author() -> None:
    """Ссылки футера: соседние сервисы, поддержка и автор (opcua-sendbox)."""
    with _client() as client:
        html = client.get("/").text
    for url in (OPCUA_SANDBOX_URL, PRODUCT_URL, ELECTROMAN_URL, SUPPORT_URL, AUTHOR_URL):
        assert f'href="{url}"' in html, url
    assert "Олег Старинский" in html
    with _client() as client:
        html = client.get("/?lang=en").text
    assert "Electroman ecosystem" in html
    assert "Author: <a" in html


def test_examples_js_copies_to_clipboard() -> None:
    """JS-модуль примеров копирует через Clipboard API (TASK-752)."""
    with _client() as client:
        response = client.get("/static/examples.js")
    assert response.status_code == 200
    js = response.text
    for fragment in ("data-copy", "navigator.clipboard", "writeText", "copy-btn"):
        assert fragment in js, fragment

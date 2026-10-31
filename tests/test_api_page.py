"""UI-страницы: shell, «Обзор», «Карта регистров» (TASK-504, 701, 711).

Обязательный текст плашки — про многопользовательский режим и перезапись
значений (DoD 10); константа `WARNING_BANNER` — единственный источник текста
(ADR-008 п. 2). «Обзор» — параметры подключения и таблица форматов unit ID
1..10 из `SLAVE_SPECS`; «Карта регистров» — live-таблица с геометрией
`REGISTER_MAP` и JS-модулем `static/registers.js`.
"""
from __future__ import annotations

from dataclasses import replace

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


def test_index_shows_public_host_not_listener() -> None:
    """«Обзор» показывает адрес для клиентов, а не слушатель 0.0.0.0."""
    config = replace(Config(), MODBUS_PUBLIC_HOST="modbus.example.org")
    with TestClient(create_app(services=build_services(config), run_engine=False)) as client:
        html = client.get("/").text
    assert "<code>modbus.example.org</code>" in html
    assert "<code>0.0.0.0</code>" not in html


def test_index_shows_connection_info() -> None:
    with _client() as client:
        response = client.get("/")
    html = response.text
    assert "5020" in html  # порт Modbus
    assert "1..10" in html  # диапазон unit ID


def test_registers_page_has_table_and_controls() -> None:
    """«Карта регистров»: таблица, выбор слейва/области, пагинация (TASK-711)."""
    with _client() as client:
        response = client.get("/registers")
    html = response.text
    for fragment in (
        'id="reg-slave"',
        'id="reg-area"',
        'id="reg-body"',
        'id="reg-prev"',
        'id="reg-next"',
        "window.REGISTER_MAP",
        'src="/static/registers.js"',
    ):
        assert fragment in html, fragment


def test_registers_page_register_map_geometry() -> None:
    """REGISTER_MAP несёт сетку значений из SLAVE_SPECS (TASK-711)."""
    with _client() as client:
        response = client.get("/registers")
    html = response.text
    assert "[[1, 1024, 1]]" in html  # slave 1: сплошная сетка
    assert "[[1, 1023, 2]]" in html  # слейвы 2..9: шаг 2
    assert "[[1, 268, 1], [300, 301, 1]]" in html  # slave 10: IR + timestamp
    assert '"fmt"' in html


def test_registers_js_served() -> None:
    """JS-модуль карты регистров отдаётся со статики (ADR-008 п. 3)."""
    with _client() as client:
        response = client.get("/static/registers.js")
    assert response.status_code == 200
    assert "/ws/state" in response.text
    assert "/api/modbus/state" in response.text


def test_registers_page_has_format_select() -> None:
    """Переключатель форматов значений на карте регистров (TASK-712)."""
    with _client() as client:
        response = client.get("/registers")
    assert 'id="reg-format"' in response.text


def test_registers_js_formats_values() -> None:
    """Форматирование HEX / DEC / signed / FLOAT по формату слейва (TASK-712)."""
    with _client() as client:
        response = client.get("/static/registers.js")
    js = response.text
    for fragment in ("DEC (знаковый)", "floatToBits", "bitsToFloat", "toSigned", '"hex"'):
        assert fragment in js, fragment


def test_registers_page_has_edit_warning() -> None:
    """Плашка про перезапись при inline-редактировании CL/HR (TASK-713)."""
    with _client() as client:
        response = client.get("/registers")
    html = response.text
    assert "last-write-wins" in html
    assert "перезаписана" in html
    assert 'id="reg-status"' in html


def test_registers_js_inline_edit() -> None:
    """Inline-редактор CL/HR пишет через REST /write (TASK-713)."""
    with _client() as client:
        response = client.get("/static/registers.js")
    js = response.text
    for fragment in ("/api/modbus/write", "startEdit", "saveValue"):
        assert fragment in js, fragment


def test_registers_page_has_watchlist() -> None:
    """Watch list на карте регистров: секция и модуль (TASK-714)."""
    with _client() as client:
        response = client.get("/registers")
    html = response.text
    for fragment in (
        "Избранное",
        'id="watch-body"',
        'id="watch-clear"',
        'src="/static/watchlist.js"',
    ):
        assert fragment in html, fragment


def test_watchlist_js_served() -> None:
    """Watch list: localStorage и WS-подписка (TASK-714)."""
    with _client() as client:
        response = client.get("/static/watchlist.js")
    assert response.status_code == 200
    js = response.text
    for fragment in ("modbus-watchlist", "localStorage", "/ws/state", "watch-add"):
        assert fragment in js, fragment


def test_registers_js_emits_watch_add() -> None:
    """Кнопка ★ на карте кидает событие watch-add (TASK-714)."""
    with _client() as client:
        response = client.get("/static/registers.js")
    assert "watch-add" in response.text


def test_registers_page_has_chart() -> None:
    """Live-график избранного: canvas и окно 60–300 с (TASK-715)."""
    with _client() as client:
        response = client.get("/registers")
    html = response.text
    for fragment in (
        'id="chart-canvas"',
        'id="chart-window"',
        "60",
        "120",
        "300",
        'src="/static/chart.js"',
    ):
        assert fragment in html, fragment


def test_chart_js_live_history() -> None:
    """График копит историю из watch-value и рисует canvas (TASK-715)."""
    with _client() as client:
        response = client.get("/static/chart.js")
    assert response.status_code == 200
    js = response.text
    for fragment in ("watch-value", "getContext", "HISTORY_S"):
        assert fragment in js, fragment


def test_watchlist_js_publishes_values() -> None:
    """Watch list публикует watch-value для графика (TASK-715)."""
    with _client() as client:
        response = client.get("/static/watchlist.js")
    assert "watch-value" in response.text


def test_injections_page_has_clear_button() -> None:
    """Кнопка «Сбросить инъекции» → REST /inject/clear (TASK-605, TASK-743).

    Кнопка живёт на странице «Инъекции»; вызов REST — в модуле
    `static/injections.js` (ADR-008).
    """
    with _client() as client:
        page = client.get("/injections").text
        js = client.get("/static/injections.js").text
    assert "Сбросить инъекции" in page
    assert "/api/modbus/inject/clear" in js


def test_index_has_ping_panel() -> None:
    """Панель «Проверить соединение» с пробой через REST (TASK-702)."""
    with _client() as client:
        response = client.get("/")
    html = response.text
    assert "Проверить соединение" in html
    assert "/api/modbus/ping" in html
    assert 'id="ping-slave"' in html
    for unit_id in range(1, 11):
        assert f"<option>{unit_id}</option>" in html


def test_index_has_metrics_panel() -> None:
    """Панель «Счётчики сервера» с поллингом REST (TASK-703)."""
    with _client() as client:
        response = client.get("/")
    html = response.text
    assert "Счётчики сервера" in html
    assert "/api/modbus/metrics" in html
    for field in ("metrics-conn", "metrics-req", "metrics-err", "metrics-uptime"):
        assert f'id="{field}"' in html


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
            "/journal",
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
        html = client.get("/docs").text
        assert "Спецификация форматов" in html  # страница документации UI (TASK-751)
        assert "<title>Документация" in html  # не страница Swagger UI
        assert "swagger-ui" not in html


def test_api_docs_page_wraps_swagger_in_site_shell() -> None:
    """Swagger UI — своя страница в оболочке сайта: бренд, навигация, тёмная тема."""
    with _client() as client:
        html = client.get("/api/docs").text
    assert "<title>REST API" in html
    assert "MODBUS" in html  # бренд в шапке
    assert 'id="swagger-ui"' in html
    assert "/static/swagger-dark.css" in html  # оформление в стиле сайта
    assert 'href="/"' in html  # возврат на сайт через шапку

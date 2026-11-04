"""UI «Поведения»: форма привязки и таблица переопределений (TASK-721).

Форма выбирает slave/area/address → kind → params; kind фильтруется по
области (числовые — hr/ir, дискретные — cl/di), поля параметров — по kind
(дефолты совпадают с `generators.py`). Установка — PUT одной привязки,
переопределения — GET `?only_overrides=true`.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.services import build_services
from app.modbus.config import Config


def _client() -> TestClient:
    return TestClient(create_app(services=build_services(Config()), run_engine=False))


def test_behaviors_page_has_form() -> None:
    with _client() as client:
        response = client.get("/behaviors")
    assert response.status_code == 200
    html = response.text
    for fragment in (
        'id="bh-slave"',
        'id="bh-area"',
        'id="bh-address"',
        'id="bh-kind"',
        'id="bh-params"',
        'id="bh-locked"',
        'id="bh-apply"',
        'id="bh-status"',
    ):
        assert fragment in html, fragment


def test_behaviors_page_slave_options_exclude_10() -> None:
    """Slave 10 поведений не имеет — в форме только unit ID 1..9."""
    with _client() as client:
        response = client.get("/behaviors")
    html = response.text
    for slave_id in range(1, 10):
        assert f"<option>{slave_id}</option>" in html, slave_id
    assert "<option>10</option>" not in html


def test_behaviors_page_has_overrides_table() -> None:
    with _client() as client:
        response = client.get("/behaviors")
    html = response.text
    for fragment in (
        "Текущие переопределения",
        'id="bh-list"',
        'id="bh-refresh"',
        "отличающиеся от поведений по умолчанию",
    ):
        assert fragment in html, fragment


def test_behaviors_page_has_type_select() -> None:
    """Селект «Тип» формы и колонка «Тип» в переопределениях (TASK-717)."""
    with _client() as client:
        response = client.get("/behaviors")
    html = response.text
    assert 'id="bh-dtype"' in html
    assert "<th>Тип</th>" in html  # таблица переопределений
    with _client() as client:
        js = client.get("/static/behaviors.js").text
    for fragment in (
        "renderDtypes",
        "selectedDtype",
        "dtypesForFmt",
        "dtype: selectedDtype()",
    ):
        assert fragment in js, fragment


def test_behaviors_page_serves_js_module() -> None:
    with _client() as client:
        response = client.get("/behaviors")
    assert 'src="/static/behaviors.js"' in response.text
    with _client() as client:
        js_response = client.get("/static/behaviors.js")
    assert js_response.status_code == 200


def test_behaviors_js_applies_single_binding() -> None:
    """Установка — PUT /api/modbus/behaviors/{slave}/{area}/{address} (TASK-721)."""
    with _client() as client:
        response = client.get("/static/behaviors.js")
    js = response.text
    for fragment in (
        '"/api/modbus/behaviors/" + slave + "/" + area + "/" + address',
        '"PUT"',
        "collectParams",
    ):
        assert fragment in js, fragment


def test_behaviors_js_filters_kinds_by_area() -> None:
    """Числовые kind — для hr/ir, дискретные — для cl/di (check_kind_for_area).

    Схема kind'ов — в общем модуле behavior-form.js (TASK-716: картe регистров
    нужна та же схема); behaviors.js её импортирует.
    """
    with _client() as client:
        form_js = client.get("/static/behavior-form.js").text
        page_js = client.get("/static/behaviors.js").text
    assert "NUMERIC_KINDS" in form_js
    assert "DISCRETE_KINDS" in form_js
    assert 'area === "hr" || area === "ir"' in form_js
    for kind in ("counter", "sawtooth", "sine", "follow"):
        assert f'"{kind}"' in form_js, kind
    for kind in ("toggle", "pwm", "random_bit", "counter_bits", "follow_bit"):
        assert f'"{kind}"' in form_js, kind
    assert 'from "./behavior-form.js"' in page_js


def test_behaviors_js_params_specs_match_generators() -> None:
    """Поля параметров по kind с дефолтами generators.py (TASK-721).

    PARAM_SPECS — в общем модуле behavior-form.js (TASK-716).
    """
    with _client() as client:
        form_js = client.get("/static/behavior-form.js").text
    for fragment in (
        "PARAM_SPECS",
        '"interval_s"',
        '"period_s"',
        '"mean_reversion"',
        '"duration_s"',
        '"wrap"',
        '"duty"',
        '"source"',
    ):
        assert fragment in form_js, fragment


def test_behaviors_js_lists_overrides() -> None:
    """Таблица переопределений — GET ?only_overrides=true (TASK-721)."""
    with _client() as client:
        response = client.get("/static/behaviors.js")
    js = response.text
    assert "/api/modbus/behaviors?only_overrides=true" in js
    assert "refreshList" in js


def test_behaviors_page_has_presets() -> None:
    """Пресеты: счётчик, пила, треугольник, меандр, шум, синус, ступеньки, сброс (TASK-722)."""
    with _client() as client:
        response = client.get("/behaviors")
    html = response.text
    assert 'id="bh-presets"' in html
    for preset in (
        "counter",
        "sawtooth",
        "triangle",
        "meander",
        "noise",
        "sine",
        "step",
        "reset",
    ):
        assert f'data-preset="{preset}"' in html, preset
    for label in ("Счётчик", "Пила", "Треугольник", "Меандр", "Шум", "Синус", "Ступеньки", "Сброс"):
        assert label in html, label


def test_behaviors_js_presets_fill_form() -> None:
    """Пресет заполняет kind + params формы и требует «Установить» (TASK-722)."""
    with _client() as client:
        response = client.get("/static/behaviors.js")
    js = response.text
    assert "PRESETS" in js
    assert "applyPreset" in js
    assert "reset:" in js
    assert "renderParams" in js
    # пресеты ограничены числовыми областями
    assert 'area !== "hr" && area !== "ir"' in js


def test_behaviors_js_preset_kinds_valid() -> None:
    """Kind'ы пресетов — из NUMERIC_KINDS (check_kind_for_area)."""
    with _client() as client:
        response = client.get("/static/behaviors.js")
    js = response.text
    for fragment in (
        'kind: "counter"',
        'kind: "sawtooth"',
        'kind: "triangle"',
        'kind: "meander"',
        'kind: "random_uniform"',
        'kind: "sine"',
        'kind: "step"',
        'kind: "constant"',
    ):
        assert fragment in js, fragment


def test_behaviors_page_has_range_controls() -> None:
    """Диапазон адресов и кнопки «Применить к диапазону» / «Сбросить» (TASK-723)."""
    with _client() as client:
        response = client.get("/behaviors")
    html = response.text
    for fragment in (
        'id="bh-address-to"',
        'id="bh-apply-range"',
        'id="bh-reset-range"',
        "Применить к диапазону",
        "Сбросить",
    ):
        assert fragment in html, fragment
    assert "window.REGISTER_MAP" in html  # геометрия сетки адресов для диапазона


def test_behaviors_js_applies_range_via_batch() -> None:
    """Диапазон применяется пакетно через POST /api/modbus/behaviors/batch (TASK-723)."""
    with _client() as client:
        response = client.get("/static/behaviors.js")
    js = response.text
    for fragment in (
        '"/api/modbus/behaviors/batch"',
        "applyRange",
        "resetRange",
        "rangeAddresses",
    ):
        assert fragment in js, fragment


def test_behaviors_js_reset_range_removes_overrides() -> None:
    """«Сбросить» — batch с remove=true (возврат к дефолтам) (TASK-723)."""
    with _client() as client:
        response = client.get("/static/behaviors.js")
    js = response.text
    assert "remove: true" in js
    assert "body.removed" in js
    assert "body.applied" in js


def test_behaviors_page_has_minichart() -> None:
    """Мини-график выбранного регистра за 60 с (TASK-724)."""
    with _client() as client:
        response = client.get("/behaviors")
    html = response.text
    for fragment in (
        "Мини-график",
        'id="bh-chart"',
        "60",
        'src="/static/minichart.js"',
    ):
        assert fragment in html, fragment


def test_minichart_js_served() -> None:
    """Мини-график: WS-подписка на адрес формы, окно 60 с (TASK-724)."""
    with _client() as client:
        response = client.get("/static/minichart.js")
    assert response.status_code == 200
    js = response.text
    for fragment in (
        "/ws/state",
        "WINDOW_S",
        "HISTORY_S",
        "getContext",
        "validTarget",
        'el("bh-address")',
    ):
        assert fragment in js, fragment

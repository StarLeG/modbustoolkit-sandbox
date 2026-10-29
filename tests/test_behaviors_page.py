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
    """Числовые kind — для hr/ir, дискретные — для cl/di (check_kind_for_area)."""
    with _client() as client:
        response = client.get("/static/behaviors.js")
    js = response.text
    assert "NUMERIC_KINDS" in js
    assert "DISCRETE_KINDS" in js
    assert 'area === "hr" || area === "ir"' in js
    for kind in ("counter", "sawtooth", "sine", "follow"):
        assert f'"{kind}"' in js, kind
    for kind in ("toggle", "pwm", "random_bit", "counter_bits", "follow_bit"):
        assert f'"{kind}"' in js, kind


def test_behaviors_js_params_specs_match_generators() -> None:
    """Поля параметров по kind с дефолтами generators.py (TASK-721)."""
    with _client() as client:
        response = client.get("/static/behaviors.js")
    js = response.text
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
        assert fragment in js, fragment


def test_behaviors_js_lists_overrides() -> None:
    """Таблица переопределений — GET ?only_overrides=true (TASK-721)."""
    with _client() as client:
        response = client.get("/static/behaviors.js")
    js = response.text
    assert "/api/modbus/behaviors?only_overrides=true" in js
    assert "refreshList" in js

"""Дифф-вью двух снимков состояния (TASK-744).

`POST /api/modbus/snapshot/diff` принимает пару снимков (A — «до», B — «после»)
и возвращает только различия: изменённые, добавленные и пропавшие значения
(`None` — записи в снимке нет). UI — секция «Дифф двух снимков» на `/tools`.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.services import build_services
from app.modbus.config import Config


def _client() -> TestClient:
    return TestClient(create_app(services=build_services(Config()), run_engine=False))


def _diff(a: dict[str, object], b: dict[str, object]) -> tuple[int, dict[str, object]]:
    with _client() as client:
        response = client.post("/api/modbus/snapshot/diff", json={"a": a, "b": b})
    return response.status_code, response.json()


# --------------------------------------------------------------------------- REST


def test_diff_reports_changed_added_removed() -> None:
    """Изменённые/добавленные/пропавшие значения + счётчик совпадений (TASK-744)."""
    a = {
        "version": 1,
        "slaves": {"1": {"hr": {"1": 10, "2": 20}, "cl": {"1": True}}},
    }
    b = {
        "version": 1,
        "slaves": {"1": {"hr": {"1": 11, "3": 30}, "cl": {"1": True}}},
    }
    status, body = _diff(a, b)
    assert status == 200
    assert body["same"] == 1  # cl 1 — совпало
    rows = body["rows"]
    assert [
        (row["slave"], row["area"], row["address"], row["a"], row["b"]) for row in rows
    ] == [
        (1, "hr", 1, 10, 11),  # изменено
        (1, "hr", 2, 20, None),  # пропало
        (1, "hr", 3, None, 30),  # добавилось
    ]


def test_diff_identical_snapshots() -> None:
    snapshot = {"version": 1, "slaves": {"2": {"hr": {"1": 1.5}, "di": {"1": False}}}}
    status, body = _diff(snapshot, snapshot)
    assert status == 200
    assert body["rows"] == []
    assert body["same"] == 2


def test_diff_orders_by_slave_area_address() -> None:
    """Строки упорядочены: slave → area (cl, di, hr, ir) → address (TASK-744)."""
    a = {"version": 1, "slaves": {"2": {"hr": {"5": 1}}, "1": {"hr": {"9": 1, "3": 1}}}}
    b = {"version": 1, "slaves": {"2": {"hr": {"5": 2}}, "1": {"hr": {"9": 2, "3": 2}}}}
    status, body = _diff(a, b)
    assert status == 200
    assert [(row["slave"], row["address"]) for row in body["rows"]] == [(1, 3), (1, 9), (2, 5)]


def test_diff_rejects_bad_version() -> None:
    snapshot = {"version": 2, "slaves": {}}
    status, body = _diff(snapshot, {"version": 1, "slaves": {}})
    assert status == 400
    assert "версия" in body["detail"]


def test_diff_schema_errors() -> None:
    with _client() as client:
        response = client.post("/api/modbus/snapshot/diff", json={"a": {"slaves": []}})
    assert response.status_code == 422


# ----------------------------------------------------------------------------- UI


def test_tools_page_has_diff_controls() -> None:
    with _client() as client:
        response = client.get("/tools")
    assert response.status_code == 200
    html = response.text
    for fragment in (
        "Дифф двух снимков",
        'id="diff-file-a"',
        'id="diff-file-b"',
        'id="diff-compare"',
        'id="diff-snapshot"',
        'id="diff-status"',
        'id="diff-summary"',
        'id="diff-rows"',
        "/static/snapshot-diff.js",
    ):
        assert fragment in html, fragment


def test_snapshot_diff_js_served() -> None:
    with _client() as client:
        response = client.get("/static/snapshot-diff.js")
    assert response.status_code == 200
    js = response.text
    for fragment in (
        "/api/modbus/snapshot/diff",
        "/api/modbus/snapshot",
        "diff-file-a",
        "renderRows",
    ):
        assert fragment in js, fragment

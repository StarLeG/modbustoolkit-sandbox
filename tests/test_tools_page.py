"""UI «Инструменты наладчика» + REST конвертера байт/слов (TASK-741).

Конвертер: значение ↔ пара регистров во всех 4 порядках (ABCD/CDAB/BADC/DCBA)
для uint32 и float. Пересчёт — серверный (`POST /api/modbus/convert`), тот же
кодек, что и у слейвов 2..9 (`app.modbus.codec`).
"""
from __future__ import annotations

import struct

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.services import build_services
from app.modbus.config import Config


def _client() -> TestClient:
    return TestClient(create_app(services=build_services(Config()), run_engine=False))


# --------------------------------------------------------------------------- UI


def test_tools_page_has_converter_controls() -> None:
    """Страница /tools: форма конвертера и таблица результатов (TASK-741)."""
    with _client() as client:
        response = client.get("/tools")
    assert response.status_code == 200
    html = response.text
    for fragment in (
        "Конвертер байт/слов",
        'id="tool-kind"',
        'id="tool-source"',
        'id="tool-value"',
        'id="tool-reg-hi"',
        'id="tool-reg-lo"',
        'id="tool-convert"',
        'id="tool-status"',
        'id="tool-rows"',
        "ABCD",
        "CDAB",
        "BADC",
        "DCBA",
    ):
        assert fragment in html, fragment


def test_tools_js_served() -> None:
    with _client() as client:
        response = client.get("/static/tools.js")
    assert response.status_code == 200
    js = response.text
    for fragment in (
        "/api/modbus/convert",
        "tool-kind",
        "tool-source",
        "tool-rows",
        "collectPayload",
    ):
        assert fragment in js, fragment


# ------------------------------------------------------------------ REST: value → regs


def test_convert_uint32_value_to_regs_all_orders() -> None:
    """0x12345678 → ABCD (0x1234,0x5678), CDAB (0x5678,0x1234),
    BADC (0x3412,0x7856), DCBA (0x7856,0x3412) (TASK-741)."""
    with _client() as client:
        response = client.post("/api/modbus/convert", json={"kind": "uint32", "value": 0x12345678})
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "uint32"
    rows = {row["order"]: row for row in body["rows"]}
    assert set(rows) == {"ABCD", "CDAB", "BADC", "DCBA"}
    assert rows["ABCD"]["regs"] == [0x1234, 0x5678]
    assert rows["CDAB"]["regs"] == [0x5678, 0x1234]
    assert rows["BADC"]["regs"] == [0x3412, 0x7856]
    assert rows["DCBA"]["regs"] == [0x7856, 0x3412]
    # Значение одно и то же (round-trip через decode)
    for row in body["rows"]:
        assert row["value"] == 0x12345678


def test_convert_float_value_to_regs_all_orders() -> None:
    """float 1.0 → ABCD (0x3F80,0x0000) и перестановки (TASK-741)."""
    with _client() as client:
        response = client.post("/api/modbus/convert", json={"kind": "float", "value": 1.0})
    assert response.status_code == 200
    rows = {row["order"]: row for row in response.json()["rows"]}
    assert rows["ABCD"]["regs"] == [0x3F80, 0x0000]
    assert rows["CDAB"]["regs"] == [0x0000, 0x3F80]
    assert rows["BADC"]["regs"] == [0x803F, 0x0000]
    assert rows["DCBA"]["regs"] == [0x0000, 0x803F]
    for row in rows.values():
        assert row["value"] == 1.0


# ------------------------------------------------------------------ REST: regs → value


def test_convert_uint32_regs_to_value_all_orders() -> None:
    """Регистры (0x1234, 0x5678): значение зависит от порядка (TASK-741)."""
    with _client() as client:
        response = client.post(
            "/api/modbus/convert", json={"kind": "uint32", "regs": [0x1234, 0x5678]}
        )
    assert response.status_code == 200
    rows = {row["order"]: row for row in response.json()["rows"]}
    assert rows["ABCD"]["value"] == 0x12345678
    assert rows["CDAB"]["value"] == 0x56781234
    assert rows["BADC"]["value"] == 0x34127856
    assert rows["DCBA"]["value"] == 0x78563412
    for row in rows.values():
        assert row["regs"] == [0x1234, 0x5678]


def test_convert_float_regs_to_value_all_orders() -> None:
    """Регистры (0x3F80, 0x0000): ABCD → 1.0, остальные порядки — иные биты."""
    with _client() as client:
        response = client.post(
            "/api/modbus/convert", json={"kind": "float", "regs": [0x3F80, 0x0000]}
        )
    assert response.status_code == 200
    rows = {row["order"]: row for row in response.json()["rows"]}
    assert rows["ABCD"]["value"] == 1.0
    assert rows["CDAB"]["value"] == struct.unpack(">f", bytes.fromhex("00003F80"))[0]
    assert rows["BADC"]["value"] == struct.unpack(">f", bytes.fromhex("803F0000"))[0]
    assert rows["DCBA"]["value"] == struct.unpack(">f", bytes.fromhex("0000803F"))[0]


# --------------------------------------------------------------------- REST: ошибки


def test_convert_rejects_both_value_and_regs() -> None:
    with _client() as client:
        response = client.post(
            "/api/modbus/convert",
            json={"kind": "uint32", "value": 1, "regs": [1, 2]},
        )
    assert response.status_code == 400
    assert "value" in response.json()["detail"]


def test_convert_rejects_neither_value_nor_regs() -> None:
    with _client() as client:
        response = client.post("/api/modbus/convert", json={"kind": "uint32"})
    assert response.status_code == 400


def test_convert_rejects_fractional_uint32() -> None:
    with _client() as client:
        response = client.post("/api/modbus/convert", json={"kind": "uint32", "value": 3.5})
    assert response.status_code == 400


def test_convert_rejects_out_of_range_uint32() -> None:
    with _client() as client:
        response = client.post("/api/modbus/convert", json={"kind": "uint32", "value": -1})
    assert response.status_code == 400
    with _client() as client:
        response = client.post(
            "/api/modbus/convert", json={"kind": "uint32", "value": 0x100000000}
        )
    assert response.status_code == 400


def test_convert_rejects_nan_float() -> None:
    with _client() as client:
        response = client.post("/api/modbus/convert", json={"kind": "float", "value": "nan"})
    assert response.status_code in (400, 422)

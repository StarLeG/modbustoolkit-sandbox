"""Генератор конфигов клиентских инструментов (TASK-742).

Modbus Poll / QModMaster / CAS Modbus Scanner — карточки настроек, pyModbus —
готовый скрипт чтения, Node-RED — импортируемый flow. Цель валидируется по
`SLAVE_SPECS`: область/адрес вне карты → 400, запрос > 125 регистров → 400.
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.services import build_services
from app.modbus.config import Config


def _client() -> TestClient:
    return TestClient(create_app(services=build_services(Config()), run_engine=False))


def _generate(body: dict[str, object]) -> tuple[int, dict[str, object]]:
    with _client() as client:
        response = client.post("/api/modbus/configgen", json=body)
    return response.status_code, response.json()


# --------------------------------------------------------------------------- UI


def test_tools_page_has_configgen_controls() -> None:
    """На /tools — форма генератора и оба модуля JS (TASK-742)."""
    with _client() as client:
        response = client.get("/tools")
    assert response.status_code == 200
    html = response.text
    for fragment in (
        "Генератор конфигов",
        'id="cfg-tool"',
        'id="cfg-slave"',
        'id="cfg-area"',
        'id="cfg-address"',
        'id="cfg-count"',
        'id="cfg-generate"',
        'id="cfg-copy"',
        'id="cfg-download"',
        'id="cfg-status"',
        'id="cfg-output"',
        "/static/configgen.js",
        "/static/tools.js",
        "Modbus Poll",
        "QModMaster",
        "CAS Modbus Scanner",
        "pyModbus",
        "Node-RED",
    ):
        assert fragment in html, fragment


def test_configgen_js_served() -> None:
    with _client() as client:
        response = client.get("/static/configgen.js")
    assert response.status_code == 200
    js = response.text
    for fragment in (
        "/api/modbus/configgen",
        "cfg-tool",
        "cfg-output",
        "cfg-download",
        "collectPayload",
    ):
        assert fragment in js, fragment


# ------------------------------------------------------- карточки GUI-инструментов


def test_configgen_modbus_poll_card() -> None:
    """Modbus Poll: соединение, FC 3, количество регистров, формат (TASK-742)."""
    status, body = _generate(
        {"tool": "modbus_poll", "slave": 3, "area": "hr", "address": 1, "count": 2}
    )
    assert status == 200
    assert body["filename"] == "modbus-poll.txt"
    content = body["content"]
    for fragment in (
        "Modbus Poll",
        "modbustoolkit.electroman.by",
        "5020",
        "Slave ID:   3",
        "Read Holding Registers (FC 3)",
        "Address:   1",
        "Quantity:  4",  # 2 значения × 2 регистра (uint32 CDAB)
        "CDAB",
        "shared last-write-wins",
    ):
        assert fragment in content, fragment


def test_configgen_qmodmaster_card() -> None:
    status, body = _generate(
        {"tool": "qmodmaster", "slave": 1, "area": "ir", "address": 5, "count": 3}
    )
    assert status == 200
    assert body["filename"] == "qmodmaster.txt"
    content = body["content"]
    for fragment in (
        "QModMaster",
        "Unit ID:       1",
        "04 — Read Input Registers (FC 4)",
        "Base Address:  5",
        "Number of Registers: 3",
    ):
        assert fragment in content, fragment


def test_configgen_cas_scanner_card() -> None:
    status, body = _generate(
        {"tool": "cas_scanner", "slave": 2, "area": "cl", "address": 1, "count": 4}
    )
    assert status == 200
    assert body["filename"] == "cas-modbus-scanner.txt"
    content = body["content"]
    for fragment in (
        "CAS Modbus Scanner",
        "Device ID: 2",
        "Read Coils (FC 1)",
        "Address:  1",
        "Length:   4",
    ):
        assert fragment in content, fragment


def test_configgen_slave10_notes_timestamp() -> None:
    """Slave 10: только FC 4, отметка про Unix timestamp 300..301 (TASK-742)."""
    status, body = _generate(
        {"tool": "modbus_poll", "slave": 10, "area": "ir", "address": 1, "count": 1}
    )
    assert status == 200
    content = body["content"]
    assert "Read Input Registers (FC 4)" in content
    assert "Unix timestamp" in content


# ------------------------------------------------------------------- скрипт pyModbus


def test_configgen_pymodbus_script_float_cdab() -> None:
    """pyModbus: float CDAB — скрипт с decode_value и перестановкой слов (TASK-742)."""
    status, body = _generate(
        {"tool": "pymodbus", "slave": 7, "area": "hr", "address": 1, "count": 2}
    )
    assert status == 200
    assert body["filename"] == "pymodbus_read.py"
    content = body["content"]
    for fragment in (
        "from pymodbus.client import ModbusTcpClient",
        'HOST = "modbustoolkit.electroman.by"',
        "PORT = 5020",
        "SLAVE = 7",
        "ADDRESS = 1",
        "COUNT = 2",
        "QUANTITY = 4",
        "read_holding_registers",
        "device_id=SLAVE",
        "decode_value",
        'struct.unpack(">f"',
        "struct.pack(\">HH\", regs[1], regs[0])",  # CDAB: младшее слово первым
    ):
        assert fragment in content, fragment


def test_configgen_pymodbus_script_badc_swaps_bytes() -> None:
    """pyModbus: BADC — _swap16 для обоих слов (TASK-742)."""
    status, body = _generate(
        {"tool": "pymodbus", "slave": 4, "area": "hr", "address": 1, "count": 1}
    )
    assert status == 200
    content = body["content"]
    assert "_swap16" in content
    assert "struct.pack(\">HH\", _swap16(regs[0]), _swap16(regs[1]))" in content
    assert 'struct.unpack(">I"' in content  # uint32


def test_configgen_pymodbus_script_uint16() -> None:
    """pyModbus: slave 1 (uint16) — регистры как есть, без decode (TASK-742)."""
    status, body = _generate(
        {"tool": "pymodbus", "slave": 1, "area": "ir", "address": 5, "count": 3}
    )
    assert status == 200
    content = body["content"]
    assert "read_input_registers" in content
    assert "QUANTITY = 3" in content
    assert "decode_value" not in content
    assert "regs[i]" in content


def test_configgen_pymodbus_script_coils() -> None:
    """pyModbus: cl — read_coils, биты, без struct (TASK-742)."""
    status, body = _generate(
        {"tool": "pymodbus", "slave": 2, "area": "cl", "address": 1, "count": 4}
    )
    assert status == 200
    content = body["content"]
    assert "read_coils" in content
    assert "QUANTITY = 4" in content
    assert "result.bits[i]" in content
    assert "import struct" not in content


# -------------------------------------------------------------------- flow Node-RED


def test_configgen_nodered_flow() -> None:
    """Node-RED: валидный JSON c modbus-client, inject и flex-getter (TASK-742)."""
    status, body = _generate(
        {"tool": "nodered", "slave": 6, "area": "ir", "address": 3, "count": 1}
    )
    assert status == 200
    assert body["filename"] == "nodered-flow.json"
    flow = json.loads(body["content"])
    nodes = {node["type"]: node for node in flow}
    client_node = nodes["modbus-client"]
    assert client_node["tcpHost"] == "modbustoolkit.electroman.by"
    assert client_node["tcpPort"] == "5020"
    assert client_node["unitId"] == "6"
    payload = json.loads(nodes["inject"]["payload"])
    assert payload == {"fc": 4, "unitid": 6, "address": 3, "quantity": 2}  # float — 2 регистра
    getter = nodes["modbus-flex-getter"]
    assert getter["server"] == "modbustoolkit-client"
    assert "modbustoolkit-getter" in nodes["inject"]["wires"][0]


# ---------------------------------------------------------------------- валидация


def test_configgen_rejects_absent_area() -> None:
    """Slave 10 без cl/di/hr → 400 (TASK-742)."""
    status, body = _generate(
        {"tool": "modbus_poll", "slave": 10, "area": "cl", "address": 1, "count": 1}
    )
    assert status == 400
    assert "отсутствует" in body["detail"]


def test_configgen_rejects_address_out_of_map() -> None:
    cases = (
        {"tool": "modbus_poll", "slave": 1, "area": "hr", "address": 2000, "count": 1},
        {"tool": "modbus_poll", "slave": 3, "area": "hr", "address": 2, "count": 1},  # чётный
        # 269 — «дырка» между блоками slave 10 (1..268 и 300..301)
        {"tool": "modbus_poll", "slave": 10, "area": "ir", "address": 268, "count": 2},
    )
    for payload in cases:
        status, body = _generate(payload)
        assert status == 400, payload
        assert "вне карты" in body["detail"], payload


def test_configgen_rejects_too_many_registers() -> None:
    """FC 3/4 — не более 125 регистров за запрос (TASK-742)."""
    status, body = _generate(
        {"tool": "modbus_poll", "slave": 3, "area": "hr", "address": 1, "count": 63}
    )
    assert status == 400
    assert "125" in body["detail"]
    # 62 значения × 2 регистра = 124 — допустимо
    status, _ = _generate(
        {"tool": "modbus_poll", "slave": 3, "area": "hr", "address": 1, "count": 62}
    )
    assert status == 200


def test_configgen_rejects_bad_count() -> None:
    status, _ = _generate(
        {"tool": "modbus_poll", "slave": 1, "area": "hr", "address": 1, "count": 0}
    )
    assert status == 422


def test_configgen_host_port_overrides() -> None:
    status, body = _generate(
        {
            "tool": "modbus_poll",
            "slave": 1,
            "area": "hr",
            "address": 1,
            "count": 1,
            "host": "127.0.0.1",
            "port": 1502,
        }
    )
    assert status == 200
    assert "127.0.0.1:1502" in body["content"]

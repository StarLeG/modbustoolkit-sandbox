"""TASK-307: POST /api/modbus/snapshot / restore (ADR-004 п. 3).

Снимок — JSON со значениями всех слейвов по сетке адресов; restore пишет
только hr/cl (ir/di генерируются движком, инвариант 2), locked-привязки
пропускаются (ADR-003 п. 3), валидация атомарна.
"""
from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.services import AppServices, build_services
from app.modbus.behavior import Behavior
from app.modbus.codec import encode
from app.modbus.config import Config
from app.modbus.types import RegisterFormat


def _client(services: AppServices | None = None) -> TestClient:
    if services is None:
        services = build_services(Config())
    return TestClient(create_app(services=services, run_engine=False))


# --- снимок -------------------------------------------------------------------------------


def test_snapshot_structure() -> None:
    services = build_services(Config())
    services.maps[1].set_registers("hr", 5, [42])
    with _client(services) as client:
        body = client.post("/api/modbus/snapshot").json()
    assert body["version"] == 1
    slaves = body["slaves"]
    assert set(slaves) == {str(s) for s in range(1, 11)}
    assert set(slaves["1"]) == {"cl", "di", "hr", "ir"}
    assert set(slaves["10"]) == {"ir"}  # slave 10 — только IR (инвариант 3)
    assert slaves["1"]["hr"]["5"] == 42
    assert len(slaves["1"]["hr"]) == 1024  # uint16: 1..1024
    assert len(slaves["2"]["hr"]) == 512  # value-grid 1,3,...,1023
    assert "2" not in slaves["2"]["hr"]  # чётные адреса — не сетка значений
    assert len(slaves["10"]["ir"]) == 269  # 1..268 + 300 (timestamp)
    assert "301" not in slaves["10"]["ir"]


# --- восстановление -----------------------------------------------------------------------


def test_restore_round_trip() -> None:
    services = build_services(Config())
    services.maps[1].set_registers("hr", 5, [42])
    services.maps[1].set_bits("cl", 3, [True])
    services.maps[2].set_registers("hr", 1, encode(0x12345678, RegisterFormat.UINT32_ABCD))
    services.maps[6].set_registers("hr", 3, encode(1.5, RegisterFormat.FLOAT_ABCD))
    with _client(services) as client:
        snap = client.post("/api/modbus/snapshot").json()
        # «загрязняем» состояние
        services.maps[1].set_registers("hr", 5, [7])
        services.maps[1].set_bits("cl", 3, [False])
        services.maps[2].set_registers("hr", 1, [0, 0])
        services.maps[6].set_registers("hr", 3, [0, 0])
        response = client.post("/api/modbus/restore", json=snap)
        snap2 = client.post("/api/modbus/snapshot").json()
    assert response.status_code == 200
    hr_cl = sum(
        len(values)
        for areas in snap["slaves"].values()
        for area, values in areas.items()
        if area in ("cl", "hr")
    )
    ir_di = sum(
        len(values)
        for areas in snap["slaves"].values()
        for area, values in areas.items()
        if area in ("ir", "di")
    )
    # демо-слоты HR locked (ADR-009): restore их пропускает, остальное пишет
    locked_hr = sum(
        1
        for slave in range(1, 10)
        for address in snap["slaves"][str(slave)]["hr"]
        if services.registry.is_locked(slave, "hr", int(address))
    )
    assert response.json() == {
        "restored": hr_cl - locked_hr,
        "skipped_locked": locked_hr,
        "ignored": ir_di,
    }
    # slave 10 — только вычисляемый ir, restore его не пишет; слейвы 1..9 — бит-в-бит
    for slave in range(1, 10):
        assert snap2["slaves"][str(slave)] == snap["slaves"][str(slave)]


def test_restore_writes_values_and_journals() -> None:
    services = build_services(Config())
    payload = {"version": 1, "slaves": {"1": {"hr": {"5": 42}, "cl": {"3": True}}}}
    with _client(services) as client:
        response = client.post("/api/modbus/restore", json=payload)
    assert response.status_code == 200
    assert response.json() == {"restored": 2, "skipped_locked": 0, "ignored": 0}
    assert services.maps[1].get_registers("hr", 5, 1) == [42]
    assert services.maps[1].get_bits("cl", 3, 1) == [True]
    entries = [e for e in services.journal.entries() if e.action == "restore"]
    assert len(entries) == 2
    assert all(e.source == "web" for e in entries)
    assert entries[0].ip is not None
    assert sorted(str(e.value) for e in entries) == ["42", "True"]


def test_restore_ignores_ir_di() -> None:
    payload = {
        "slaves": {"1": {"ir": {"1": 999, "2": 998}, "di": {"1": True}, "hr": {"5": 42}}}
    }
    with _client() as client:
        response = client.post("/api/modbus/restore", json=payload)
    assert response.status_code == 200
    assert response.json() == {"restored": 1, "skipped_locked": 0, "ignored": 3}


def test_restore_normalizes_to_format() -> None:
    services = build_services(Config())
    with _client(services) as client:
        response = client.post(
            "/api/modbus/restore", json={"slaves": {"1": {"hr": {"5": 70000}}}}
        )
    assert response.json()["restored"] == 1
    assert services.maps[1].get_registers("hr", 5, 1) == [65535]  # clamp UINT16
    entry = [e for e in services.journal.entries() if e.action == "restore"][-1]
    assert entry.value == 65535


def test_restore_ignores_pair_shadow_address() -> None:
    """Младший регистр пары N, N+1 в снимке игнорируется — значением владеет N (TASK-717)."""
    services = build_services(Config())
    services.registry.set(1, "hr", 5, Behavior("constant", {"value": 0}, dtype="float32"))
    payload = {"slaves": {"1": {"hr": {"5": 841.37, "6": 999}}}}
    with _client(services) as client:
        response = client.post("/api/modbus/restore", json=payload)
    assert response.status_code == 200
    assert response.json() == {"restored": 1, "skipped_locked": 0, "ignored": 1}
    assert services.maps[1].get_registers("hr", 5, 2) == encode(
        841.37, RegisterFormat.FLOAT_ABCD
    )


def test_restore_writes_by_binding_dtype() -> None:
    """Значения hr восстанавливаются по типу значения привязки (TASK-717)."""
    services = build_services(Config())
    services.registry.set(1, "hr", 5, Behavior("constant", {"value": 0}, dtype="int16"))
    payload = {"slaves": {"1": {"hr": {"5": -5}}}}
    with _client(services) as client:
        response = client.post("/api/modbus/restore", json=payload)
    assert response.json()["restored"] == 1
    assert services.maps[1].get_registers("hr", 5, 1) == encode(-5, RegisterFormat.INT16)
    entry = [e for e in services.journal.entries() if e.action == "restore"][-1]
    assert entry.value == -5


def test_restore_locked_skipped() -> None:
    services = build_services(Config())
    services.registry.set(1, "hr", 5, Behavior("constant", {"value": 0}, locked=True))
    payload = {"slaves": {"1": {"hr": {"5": 42}, "cl": {"3": True}}}}
    with _client(services) as client:
        response = client.post("/api/modbus/restore", json=payload)
    assert response.status_code == 200
    assert response.json() == {"restored": 1, "skipped_locked": 1, "ignored": 0}
    assert services.maps[1].get_registers("hr", 5, 1) == [0]  # не записано
    rejected = [e for e in services.journal.entries() if e.action == "write_rejected_locked"]
    assert len(rejected) == 1
    assert rejected[0].source == "web"  # политика журналирует отказ сама
    restored = [e for e in services.journal.entries() if e.action == "restore"]
    assert len(restored) == 1  # только cl прошёл


# --- ошибки -------------------------------------------------------------------------------


def test_restore_rejects_bad_payload() -> None:
    cases: list[tuple[dict[str, Any], str]] = [
        ({"version": 2, "slaves": {}}, "версия"),
        ({"version": 3, "slaves": {"1": {"hr": {"5": 42}}}}, "версия"),
        ({"slaves": {"11": {"hr": {"1": 0}}}}, "неизвестный slave"),
        ({"slaves": {"1": {"hr": {"abc": 0}}}}, "некорректный адрес"),
        ({"slaves": {"2": {"hr": {"2": 0}}}}, "вне карты"),  # чётный адрес
        ({"slaves": {"1": {"cl": {"3": 1}}}}, "ожидается bool"),
        ({"slaves": {"1": {"hr": {"5": True}}}}, "ожидается число"),
        ({"slaves": {"10": {"hr": {"1": 0}}}}, "вне карты"),  # slave 10 без hr
    ]
    with _client() as client:
        for payload, match in cases:
            response = client.post("/api/modbus/restore", json=payload)
            assert response.status_code == 400, (payload, response.text)
            assert match in response.json()["detail"]


def test_restore_atomic_on_validation_error() -> None:
    services = build_services(Config())
    payload = {"slaves": {"1": {"hr": {"5": 42}}, "2": {"hr": {"2": 1}}}}  # 2-й — вне карты
    with _client(services) as client:
        response = client.post("/api/modbus/restore", json=payload)
    assert response.status_code == 400
    assert services.maps[1].get_registers("hr", 5, 1) == [0]  # ничего не записано
    assert not [e for e in services.journal.entries() if e.action == "restore"]


def test_restore_bad_schema_rejected() -> None:
    with _client() as client:
        assert client.post("/api/modbus/restore", json={"version": 1}).status_code == 422
        bad = client.post("/api/modbus/restore", json={"slaves": {"1": {"zz": {"1": 0}}}})
        assert bad.status_code == 422

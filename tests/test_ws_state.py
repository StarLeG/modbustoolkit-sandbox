"""TASK-401: /ws/state — подписка {slave, areas, addresses?} (ADR-005 п. 2).

Первый кадр сразу после подписки (подтверждение); периодичность — TASK-402.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.app import create_app
from app.api.services import AppServices, build_services
from app.api.state_bus import StateSubscription
from app.modbus.config import Config


def _client(services: AppServices | None = None) -> TestClient:
    if services is None:
        services = build_services(Config())
    return TestClient(create_app(services=services, run_engine=False))


# --- разбор подписки (unit) ---------------------------------------------------------------


def test_subscription_parse_defaults() -> None:
    sub = StateSubscription.parse({"slave": 2})
    assert sub.slave == 2
    assert sub.areas == ("cl", "di", "hr", "ir")  # все области слейва
    assert sub.addresses is None  # вся сетка значений


def test_subscription_parse_errors() -> None:
    cases = [
        ([], "JSON-объект"),
        ({}, "slave"),
        ({"slave": 11}, "1..10"),
        ({"slave": True}, "1..10"),
        ({"slave": 1, "areas": []}, "непустой список"),
        ({"slave": 1, "areas": ["zz"]}, "неизвестная область"),
        ({"slave": 10, "areas": ["hr"]}, "отсутствует"),
        ({"slave": 1, "addresses": []}, "addresses"),
        ({"slave": 1, "addresses": ["5"]}, "должен быть int"),
        ({"slave": 1, "addresses": [2000]}, "вне карты"),
    ]
    for raw, match in cases:
        with pytest.raises((ValueError, TypeError), match=match):
            StateSubscription.parse(raw)


# --- интеграция через WebSocket -----------------------------------------------------------


def test_ws_state_subscribed_frame() -> None:
    services = build_services(Config())
    services.maps[1].set_registers("hr", 5, [42])
    with _client(services) as client, client.websocket_connect("/ws/state") as ws:
        ws.send_text(json.dumps({"slave": 1, "areas": ["hr", "cl"]}))
        frame = ws.receive_json()
    assert frame["type"] == "state"
    assert frame["slave"] == 1
    assert frame["fmt"] == "uint16"
    assert set(frame["areas"]) == {"hr", "cl"}
    assert frame["areas"]["hr"]["5"] == 42
    assert len(frame["areas"]["hr"]) == 1024  # uint16: 1..1024
    assert len(frame["areas"]["cl"]) == 64


def test_ws_state_addresses_filter() -> None:
    with _client() as client, client.websocket_connect("/ws/state") as ws:
        ws.send_text(json.dumps({"slave": 2, "areas": ["hr"], "addresses": [1, 3]}))
        frame = ws.receive_json()
    assert frame["areas"] == {"hr": {"1": 0, "3": 0}}


def test_ws_state_default_areas_slave10() -> None:
    with _client() as client, client.websocket_connect("/ws/state") as ws:
        ws.send_text(json.dumps({"slave": 10}))
        frame = ws.receive_json()
    assert set(frame["areas"]) == {"ir"}  # slave 10 — только IR
    assert len(frame["areas"]["ir"]) == 269  # 1..268 + 300


def test_ws_state_resubscribe() -> None:
    with _client() as client, client.websocket_connect("/ws/state") as ws:
        ws.send_text(json.dumps({"slave": 1, "areas": ["hr"], "addresses": [5]}))
        first = ws.receive_json()
        ws.send_text(json.dumps({"slave": 2, "areas": ["hr"], "addresses": [1]}))
        second = ws.receive_json()
    assert first["slave"] == 1
    assert first["areas"] == {"hr": {"5": 0}}
    assert second["slave"] == 2
    assert second["areas"] == {"hr": {"1": 0}}


def test_ws_state_bad_subscription() -> None:
    cases = [
        ("[]", "JSON-объект"),
        (json.dumps({"slave": 11}), "1..10"),
        (json.dumps({"slave": 10, "areas": ["hr"]}), "отсутствует"),
        (json.dumps({"slave": 1, "addresses": [2000]}), "вне карты"),
    ]
    with _client() as client:
        for raw, match in cases:
            with client.websocket_connect("/ws/state") as ws:
                ws.send_text(raw)
                frame = ws.receive_json()
                assert frame["type"] == "error"
                assert match in frame["detail"]
                with pytest.raises(WebSocketDisconnect):
                    ws.receive_text()  # сервер закрыл соединение (1008)

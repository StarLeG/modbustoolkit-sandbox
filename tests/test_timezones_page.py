"""UI «Часовые пояса»: 27 зон slave 10 + Unix timestamp (TASK-731, 732).

Карточки Z=0..26 с датой/временем/смещением наполняются из IR slave 10
(блоки 1..268), Unix timestamp — IR 300..301; расхождение — разница
серверного timestamp с системным временем браузера.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.services import build_services
from app.modbus.config import Config


def _client() -> TestClient:
    return TestClient(create_app(services=build_services(Config()), run_engine=False))


def test_timezones_page_has_zone_cards() -> None:
    """27 карточек Z=0..26 с подписями UTC±HH:00 (TASK-731)."""
    with _client() as client:
        response = client.get("/timezones")
    assert response.status_code == 200
    html = response.text
    for zone in range(27):
        assert f'<h3>Z={zone} · UTC' in html, zone
        assert f'id="tz-{zone}-date"' in html, zone
        assert f'id="tz-{zone}-time"' in html, zone
        assert f'id="tz-{zone}-weekday"' in html, zone
        assert f'id="tz-{zone}-offset"' in html, zone
    assert "UTC+00:00" in html  # Z=0
    assert "UTC+14:00" in html  # Z=14
    assert "UTC−01:00" in html  # Z=15
    assert "UTC−12:00" in html  # Z=26


def test_timezones_page_has_unix_timestamp() -> None:
    """Unix timestamp (IR 300..301) и расхождение с системным (TASK-732)."""
    with _client() as client:
        response = client.get("/timezones")
    html = response.text
    for fragment in (
        "Unix timestamp",
        'id="tz-unix"',
        'id="tz-system"',
        'id="tz-delta"',
        "300..301",
        "uint32 ABCD",
    ):
        assert fragment in html, fragment


def test_timezones_js_served() -> None:
    with _client() as client:
        response = client.get("/static/timezones.js")
    assert response.status_code == 200
    js = response.text
    for fragment in (
        "/ws/state",
        "/api/modbus/state?slave=10&area=ir",
        "ZONE_COUNT",
        "renderTimestamp",
        "renderClock",
    ):
        assert fragment in js, fragment


def test_timezones_js_reads_zone_blocks() -> None:
    """Блок зоны Z — адреса 1+10*Z .. 8+10*Z, смещение — int16 (TASK-731)."""
    with _client() as client:
        response = client.get("/static/timezones.js")
    js = response.text
    for fragment in ("ZONE_SLOTS", "toSigned16", "WEEKDAYS", 'state.values.get("300")'):
        assert fragment in js, fragment

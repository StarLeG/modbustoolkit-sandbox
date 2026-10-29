"""Smoke-база Эпика 0: конфигурация и SLAVE_SPECS согласованы с CLAUDE.md и HRIR_SPEC."""
from __future__ import annotations

import pytest

from app.modbus import SLAVE_SPECS, AddressRange, Config, RegisterFormat


def test_config_defaults_match_env_table() -> None:
    cfg = Config()
    assert cfg.MODBUS_TCP_HOST == "0.0.0.0"
    assert cfg.MODBUS_TCP_PORT == 5020
    assert cfg.MODBUS_MAX_CONN_PER_IP == 20
    assert cfg.MODBUS_MAX_WRITE_PER_SEC == 200
    assert cfg.WEB_MAX_WRITE_PER_MIN_PER_IP == 50
    assert cfg.ENGINE_TICK_MS == 100
    assert cfg.WS_STATE_HZ == 2
    assert cfg.JOURNAL_SIZE == 1000
    assert cfg.INJECTION_MAX_TTL_S == 300


def test_config_from_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODBUS_TCP_HOST", "127.0.0.1")
    monkeypatch.setenv("MODBUS_TCP_PORT", "1502")
    monkeypatch.setenv("ENGINE_TICK_MS", "250")
    cfg = Config.from_env()
    assert cfg.MODBUS_TCP_HOST == "127.0.0.1"
    assert cfg.MODBUS_TCP_PORT == 1502
    assert cfg.ENGINE_TICK_MS == 250
    assert cfg.JOURNAL_SIZE == 1000  # не задана — дефолт


def test_config_from_env_ignores_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODBUS_TCP_HOST", "")
    cfg = Config.from_env()
    assert cfg.MODBUS_TCP_HOST == "0.0.0.0"


def test_slave_specs_cover_ten_units() -> None:
    assert sorted(SLAVE_SPECS) == list(range(1, 11))


def test_register_formats_match_hrir_spec() -> None:
    assert len(RegisterFormat) == 9
    assert RegisterFormat.UINT16.registers_per_value == 1
    assert RegisterFormat.UINT32_CDAB.registers_per_value == 2
    assert RegisterFormat.FLOAT_DCBA.is_float
    assert not RegisterFormat.UINT32_ABCD.is_float


def test_address_range_contains_respects_step() -> None:
    odd = AddressRange(1, 1023, step=2)
    assert 1 in odd and 1023 in odd and 513 in odd
    assert 2 not in odd and 0 not in odd and 1025 not in odd


def test_32bit_slaves_use_odd_value_addresses() -> None:
    for slave_id in range(2, 10):
        spec = SLAVE_SPECS[slave_id]
        assert spec.fmt.is_32bit
        assert spec.has_address("hr", 1)
        assert spec.has_address("hr", 1023)
        assert spec.has_address("ir", 1)
        assert not spec.has_address("hr", 2)
        assert not spec.has_address("hr", 1024)
        assert spec.has_address("cl", 1) and spec.has_address("cl", 64)
        assert not spec.has_address("cl", 65)


def test_slave1_covers_contiguous_1024() -> None:
    spec = SLAVE_SPECS[1]
    assert spec.fmt is RegisterFormat.UINT16
    assert spec.has_address("hr", 1) and spec.has_address("hr", 1024)
    assert spec.has_address("ir", 2)  # uint16 — шаг 1
    assert not spec.has_address("hr", 1025)


def test_slave10_is_ir_only() -> None:
    spec = SLAVE_SPECS[10]
    assert not spec.cl and not spec.di and not spec.hr
    assert not spec.has_address("cl", 1)
    assert not spec.has_address("di", 1)
    assert not spec.has_address("hr", 1)
    assert spec.has_address("ir", 1)
    assert spec.has_address("ir", 268)
    assert spec.has_address("ir", 300)
    assert spec.has_address("ir", 301)
    assert not spec.has_address("ir", 269)  # «дырка» за пределами карты
    assert not spec.has_address("ir", 302)

"""Конфигурация сервиса из переменных окружения.

Секретов нет; все переменные и дефолты — таблица «Env» в CLAUDE.md.
Единственный источник дефолтов — значения полей Config.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


@dataclass(frozen=True)
class Config:
    """Настройки runtime; значения по умолчанию согласованы с CLAUDE.md (Env)."""

    MODBUS_TCP_HOST: str = "0.0.0.0"
    MODBUS_TCP_PORT: int = 5020
    # Адрес, по которому клиенты подключаются снаружи (UI, генератор конфигов);
    # слушатель MODBUS_TCP_HOST обычно 0.0.0.0 и для клиента бесполезен.
    MODBUS_PUBLIC_HOST: str = "modbustoolkit.electroman.by"
    MODBUS_MAX_CONN_PER_IP: int = 20
    MODBUS_MAX_WRITE_PER_SEC: int = 200
    WEB_MAX_WRITE_PER_MIN_PER_IP: int = 50
    ENGINE_TICK_MS: int = 100
    WS_STATE_HZ: int = 2
    JOURNAL_SIZE: int = 1000
    INJECTION_MAX_TTL_S: int = 300

    @classmethod
    def from_env(cls) -> Config:
        """Создать конфигурацию, считав env; не заданные переменные берут дефолт."""
        base = cls()
        return cls(
            MODBUS_TCP_HOST=os.environ.get("MODBUS_TCP_HOST") or base.MODBUS_TCP_HOST,
            MODBUS_TCP_PORT=_env_int("MODBUS_TCP_PORT", base.MODBUS_TCP_PORT),
            MODBUS_PUBLIC_HOST=os.environ.get("MODBUS_PUBLIC_HOST") or base.MODBUS_PUBLIC_HOST,
            MODBUS_MAX_CONN_PER_IP=_env_int("MODBUS_MAX_CONN_PER_IP", base.MODBUS_MAX_CONN_PER_IP),
            MODBUS_MAX_WRITE_PER_SEC=_env_int(
                "MODBUS_MAX_WRITE_PER_SEC", base.MODBUS_MAX_WRITE_PER_SEC
            ),
            WEB_MAX_WRITE_PER_MIN_PER_IP=_env_int(
                "WEB_MAX_WRITE_PER_MIN_PER_IP", base.WEB_MAX_WRITE_PER_MIN_PER_IP
            ),
            ENGINE_TICK_MS=_env_int("ENGINE_TICK_MS", base.ENGINE_TICK_MS),
            WS_STATE_HZ=_env_int("WS_STATE_HZ", base.WS_STATE_HZ),
            JOURNAL_SIZE=_env_int("JOURNAL_SIZE", base.JOURNAL_SIZE),
            INJECTION_MAX_TTL_S=_env_int("INJECTION_MAX_TTL_S", base.INJECTION_MAX_TTL_S),
        )

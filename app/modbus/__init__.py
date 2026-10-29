"""Ядро тестового Modbus-сервера: конфигурация и типы карты регистров."""
from __future__ import annotations

from app.modbus.config import Config
from app.modbus.types import (
    AREAS,
    SLAVE_SPECS,
    AddressRange,
    Area,
    RegisterFormat,
    SlaveSpec,
)

__all__ = [
    "AREAS",
    "SLAVE_SPECS",
    "AddressRange",
    "Area",
    "Config",
    "RegisterFormat",
    "SlaveSpec",
]

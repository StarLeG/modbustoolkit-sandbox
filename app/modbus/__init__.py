"""Ядро тестового Modbus-сервера: конфигурация и типы карты регистров."""
from __future__ import annotations

from app.modbus.config import Config
from app.modbus.slave_map import ModbusMapError, SlaveMap
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
    "ModbusMapError",
    "RegisterFormat",
    "SlaveMap",
    "SlaveSpec",
]

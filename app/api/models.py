"""Pydantic-схемы REST (Эпик 3, ADR-004 п. 5)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.modbus.types import Area


class BehaviorOut(BaseModel):
    """Поведение привязки в REST-представлении."""

    slave: int
    area: Area
    address: int
    kind: str
    params: dict[str, Any]
    locked: bool
    is_default: bool


class BehaviorsResponse(BaseModel):
    """Полный конфиг поведений (TASK-301).

    `total` — все привязки реестра (слейвы 1..9), `overrides` — число
    переопределённых (не дефолтных) привязок, `items` — результат фильтра.
    """

    items: list[BehaviorOut]
    total: int
    overrides: int

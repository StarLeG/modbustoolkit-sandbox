"""Pydantic-схемы REST (Эпик 3, ADR-004 п. 5)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.modbus.types import Area


class BehaviorIn(BaseModel):
    """Входное поведение для установки (TASK-302).

    `kind` проверяется моделью `Behavior` (400 на неизвестный), value-параметры
    приводятся к формату регистра при установке (TASK-241).
    """

    kind: str
    params: dict[str, Any] = {}
    locked: bool = False


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


class BatchItem(BaseModel):
    """Элемент пакетной операции (TASK-303).

    `remove=True` — сброс привязки к дефолту (kind в этом случае запрещён),
    иначе — установка поведения (kind обязателен).
    """

    slave: int
    area: Area
    address: int
    kind: str | None = None
    params: dict[str, Any] = {}
    locked: bool = False
    remove: bool = False


class BatchIn(BaseModel):
    """Пакет установки/сброса поведений; применяется атомарно (TASK-303)."""

    items: list[BatchItem]


class BatchResponse(BaseModel):
    """Итог пакета: `applied` — установлено, `removed` — сброшено к дефолтам."""

    applied: int
    removed: int


class ResetIn(BaseModel):
    """Сброс переопределений: без `slave` — все, иначе — одного слейва (TASK-304)."""

    slave: int | None = Field(default=None, ge=1, le=10)


class ResetResponse(BaseModel):
    """Число сброшенных переопределений (дефолтные привязки не считаются)."""

    reset: int

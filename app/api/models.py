"""Pydantic-схемы REST (Эпик 3, ADR-004 п. 5)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.modbus.injection import InjectionKind
from app.modbus.types import Area, RegisterFormat


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


class StateResponse(BaseModel):
    """Снимок значений слейва (TASK-305).

    Значения — по сетке адресов (1,3,…,1023 для 32-битных форматов),
    декодированы по `fmt`; биты cl/di — bool. Исключение slave 10: IR 300 —
    одно значение UINT32_ABCD (Unix timestamp, `docs/HRIR_SPEC.md`).
    """

    slave: int
    fmt: RegisterFormat
    areas: dict[Area, dict[str, int | float | bool]]


class WriteIn(BaseModel):
    """Запись значения CL/HR из UI (TASK-306).

    `address` — адрес сетки значений (1,3,…,1023 для 32-битных форматов);
    для cl значение — bool, для hr — число (приводится к формату регистра).
    """

    slave: int
    area: Area
    address: int
    value: bool | int | float


class WriteResponse(BaseModel):
    """Подтверждение записи: значение после приведения к формату (TASK-306)."""

    slave: int
    area: Area
    address: int
    value: bool | int | float


class Snapshot(BaseModel):
    """Снимок состояния слейвов (TASK-307, ADR-004 п. 3).

    Значения по сетке адресов (1,3,…,1023 для 32-битных форматов),
    декодированные по формату слейва. `version` — формат снимка (сейчас 1).
    """

    version: int = 1
    slaves: dict[int, dict[Area, dict[str, int | float | bool]]]


class JournalEntryOut(BaseModel):
    """Запись журнала (TASK-308, TASK-505).

    `area`/`address` — None для событий уровня слейва (например, инъекций).
    """

    action: str
    slave: int
    area: Area | None
    address: int | None
    value: Any = None
    source: str
    ip: str | None = None
    timestamp: float


class JournalResponse(BaseModel):
    """Записи журнала от старых к новым (TASK-308).

    `total` — записей в буфере сейчас (с учётом вытеснения по `JOURNAL_SIZE`).
    """

    items: list[JournalEntryOut]
    total: int


class RestoreResponse(BaseModel):
    """Итог восстановления (TASK-307).

    `restored` — записано значений hr/cl; `skipped_locked` — пропущено из-за
    locked-поведений (ADR-003 п. 3); `ignored` — значения ir/di из снимка
    (генерируются движком, не восстанавливаются).
    """

    restored: int
    skipped_locked: int
    ignored: int


class InjectionIn(BaseModel):
    """Инъекция ошибки (TASK-309, ADR-004 п. 6).

    `kind`: `"exception"` — отвечать кодом `code` (0x01/0x02/0x03/0x04/0x06);
    `"delay"` — задержка `delay_ms` (0..5000) перед ответом; `"timeout"` — не
    отвечать `ttl_s` секунд. Цель: весь слейв либо диапазон `area`/`address`/
    `count` (по сетке значений). `ttl_s` ≤ `INJECTION_MAX_TTL_S` (ленивый
    автосброс).
    """

    kind: InjectionKind
    slave: int = Field(ge=1, le=10)
    area: Area | None = None
    address: int | None = Field(default=None, ge=1)
    count: int | None = Field(default=None, ge=1)
    code: int | None = None
    delay_ms: int | None = Field(default=None, ge=0)
    ttl_s: float | None = Field(default=None, gt=0)


class InjectionOut(BaseModel):
    """Активная инъекция: id, цель, параметры и срок жизни (TASK-309)."""

    id: int
    kind: InjectionKind
    slave: int
    area: Area | None
    address: int | None
    count: int | None
    code: int | None
    delay_ms: int | None
    ttl_s: float
    created_at: float
    expires_at: float


class InjectClearIn(BaseModel):
    """Снятие инъекций (TASK-309, TASK-605): по `id`, по `slave` или все."""

    id: int | None = Field(default=None, ge=1)
    slave: int | None = Field(default=None, ge=1, le=10)


class InjectClearResponse(BaseModel):
    """Число снятых инъекций."""

    cleared: int

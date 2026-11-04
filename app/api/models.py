"""Pydantic-схемы REST (Эпик 3, ADR-004 п. 5)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.api.configgen import ConfigTool
from app.modbus.injection import InjectionKind
from app.modbus.types import Area, RegisterFormat, ValueDType


class BehaviorIn(BaseModel):
    """Входное поведение для установки (TASK-302).

    `kind` проверяется моделью `Behavior` (400 на неизвестный), value-параметры
    приводятся к типу значения при установке (TASK-241). `dtype` — тип значения
    (TASK-717): None — нативный тип слейва; задаётся только для hr/ir.
    """

    kind: str
    params: dict[str, Any] = {}
    locked: bool = False
    dtype: ValueDType | None = None


class BehaviorOut(BaseModel):
    """Поведение привязки в REST-представлении.

    `dtype` (TASK-717) — эффективный тип значения привязки (явный или
    нативный тип слейва); None для cl/di (биты).
    """

    slave: int
    area: Area
    address: int
    kind: str
    params: dict[str, Any]
    locked: bool
    is_default: bool
    dtype: ValueDType | None = None


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
    иначе — установка поведения (kind обязателен). `dtype` — тип значения
    (TASK-717), как в `BehaviorIn`.
    """

    slave: int
    area: Area
    address: int
    kind: str | None = None
    params: dict[str, Any] = {}
    locked: bool = False
    remove: bool = False
    dtype: ValueDType | None = None


#: Потолок элементов пакета (TASK-1004, ADR-012): UI шлёт диапазон целиком
#: (до 1024 адресов области), остальное — запас для API-сценариев. Крупные
#: пакеты дробятся на несколько запросов.
MAX_BATCH_ITEMS = 2048


class BatchIn(BaseModel):
    """Пакет установки/сброса поведений; применяется атомарно (TASK-303).

    Не больше `MAX_BATCH_ITEMS` элементов (422 при переполнении).
    """

    items: list[BatchItem] = Field(max_length=MAX_BATCH_ITEMS)


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
    #: Пользовательские теги `{область: {адрес: тег}}` (TASK-718); без тегов — пусто.
    tags: dict[str, dict[str, str]] = {}


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


class SnapshotDiffIn(BaseModel):
    """Пара снимков для сравнения (TASK-744): `a` — «до», `b` — «после»."""

    a: Snapshot
    b: Snapshot


class SnapshotDiffRow(BaseModel):
    """Одно различие снимков: значение по адресу в A и B (TASK-744).

    `None` — записи нет в этом снимке (значение добавилось или пропало).
    """

    slave: int
    area: Area
    address: int
    a: int | float | bool | None
    b: int | float | bool | None


class SnapshotDiffResponse(BaseModel):
    """Дифф снимков: только различия + число совпавших значений (TASK-744).

    `rows` — по порядку slave → area → address; `same` — значений, которые
    совпали в обоих снимках (в таблицу не попадают).
    """

    rows: list[SnapshotDiffRow]
    same: int


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
    (генерируются движком, не восстанавливаются) и младшие регистры пар
    N, N+1 (TASK-717: значением управляет привязка N).
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
    """Активная инъекция: id, цель, параметры и срок жизни (TASK-309).

    `remaining_s` — остаток TTL на момент ответа (TASK-743): `created_at`/
    `expires_at` — монотонические часы реестра, браузеру они бесполезны.
    """

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
    remaining_s: float


class InjectionsResponse(BaseModel):
    """Активные инъекции ошибок (TASK-743).

    `items` — в порядке добавления (без истёкших); `max_ttl_s` — предел
    `INJECTION_MAX_TTL_S` для формы UI.
    """

    items: list[InjectionOut]
    max_ttl_s: float


class InjectClearIn(BaseModel):
    """Снятие инъекций (TASK-309, TASK-605): по `id`, по `slave` или все."""

    id: int | None = Field(default=None, ge=1)
    slave: int | None = Field(default=None, ge=1, le=10)


class InjectClearResponse(BaseModel):
    """Число снятых инъекций."""

    cleared: int


class PingIn(BaseModel):
    """Параметры пробы соединения (TASK-702).

    `count` — число контрольных чтений (1..10, ограничение пробы —
    `app.modbus.ping.MAX_ATTEMPTS`).
    """

    slave: int = Field(ge=1, le=10)
    count: int = Field(default=5, ge=1, le=10)


class PingResponse(BaseModel):
    """Итог пробы соединения (TASK-702).

    `ok` / `errors` — число успешных и неудачных попыток; `rtt_*_ms` —
    min/avg/max по успешным ответам (None, если успехов не было);
    `last_error` — последняя ошибка попытки.
    """

    slave: int
    attempts: int
    ok: int
    errors: int
    rtt_min_ms: float | None
    rtt_avg_ms: float | None
    rtt_max_ms: float | None
    last_error: str | None


class MetricsResponse(BaseModel):
    """Счётчики Modbus-сервера (TASK-703).

    `req_per_s` / `err_per_s` — скорости за скользящее окно
    (`app.modbus.metrics.RATE_WINDOW_S`), `uptime_s` — с запуска сервера.
    """

    active_connections: int
    requests_total: int
    errors_total: int
    req_per_s: float
    err_per_s: float
    uptime_s: float


class HealthzResponse(BaseModel):
    """Ответ healthcheck (TASK-310)."""

    status: str


class ConvertIn(BaseModel):
    """Вход конвертера байт/слов (TASK-741).

    Ровно одно из полей: `value` — значение (uint32 или float) для перевода
    в регистры, `regs` — два регистра (слова) для перевода в значение.
    Порядок — все четыре: ABCD, CDAB, BADC, DCBA.
    """

    kind: Literal["uint32", "float"]
    value: int | float | None = None
    regs: tuple[int, int] | None = None


class ConvertRow(BaseModel):
    """Строка таблицы конвертации: значение ↔ регистры в одном порядке.

    `value` — строкой только NaN/inf/-inf (в JSON они не сериализуются).
    """

    order: Literal["ABCD", "CDAB", "BADC", "DCBA"]
    regs: tuple[int, int]
    value: int | float | str


class ConvertResponse(BaseModel):
    """Таблица конвертации по всем 4 порядкам (TASK-741).

    При входе `value` регистры строк различаются (значение одно), при входе
    `regs` — различаются значения (регистры одни).
    """

    kind: Literal["uint32", "float"]
    rows: list[ConvertRow]


class ConfigGenIn(BaseModel):
    """Вход генератора конфигов клиентских инструментов (TASK-742).

    Цель: `slave`/`area`/`address`/`count` (количество значений; для 32-битных
    форматов — по 2 регистра на значение). `host`/`port` — необязательные
    переопределения адреса сервера (по умолчанию — публичный хост и
    `MODBUS_TCP_PORT`).
    """

    tool: ConfigTool
    slave: int = Field(ge=1, le=10)
    area: Area
    address: int = Field(ge=1)
    count: int = Field(default=1, ge=1, le=1024)
    host: str | None = Field(default=None, max_length=253)
    port: int | None = Field(default=None, ge=1, le=65535)


class ConfigGenResponse(BaseModel):
    """Сгенерированный конфиг: имя файла для скачивания и его содержимое (TASK-742)."""

    tool: ConfigTool
    filename: str
    content: str


class TagIn(BaseModel):
    """Пользовательский тег привязки (TASK-718).

    Пустой тег после `strip` — ошибка 400; чтобы вернуть автоматическое имя,
    используйте `DELETE /api/modbus/tags/{slave}/{area}/{address}`.
    """

    tag: str = Field(min_length=1, max_length=256)


class TagOut(BaseModel):
    """Заданный пользователем тег привязки (TASK-718)."""

    slave: int
    area: Area
    address: int
    tag: str


class TagsResponse(BaseModel):
    """Все пользовательские теги (TASK-718): только явно заданные переопределения."""

    items: list[TagOut]
    total: int


class TagDeleteResponse(BaseModel):
    """Результат сброса тега: `cleared=False` — тега и не было (TASK-718)."""

    slave: int
    area: Area
    address: int
    cleared: bool

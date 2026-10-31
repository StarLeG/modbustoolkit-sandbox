"""REST-роуты `/api/modbus/*` (Эпик 3, ADR-004)."""
from __future__ import annotations

import math
import time
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import PlainTextResponse

from app.api.configgen import PUBLIC_HOST, ConfigTarget, generate
from app.api.models import (
    BatchIn,
    BatchResponse,
    BehaviorIn,
    BehaviorOut,
    BehaviorsResponse,
    ConfigGenIn,
    ConfigGenResponse,
    ConvertIn,
    ConvertResponse,
    ConvertRow,
    HealthzResponse,
    InjectClearIn,
    InjectClearResponse,
    InjectionIn,
    InjectionOut,
    InjectionsResponse,
    JournalEntryOut,
    JournalResponse,
    MetricsResponse,
    PingIn,
    PingResponse,
    ResetIn,
    ResetResponse,
    RestoreResponse,
    Snapshot,
    SnapshotDiffIn,
    SnapshotDiffResponse,
    SnapshotDiffRow,
    StateResponse,
    WriteIn,
    WriteResponse,
)
from app.api.prometheus import MEDIA_TYPE as PROMETHEUS_MEDIA_TYPE
from app.api.prometheus import render_prometheus
from app.api.services import AppServices
from app.api.state_view import area_values
from app.modbus.behavior import (
    Behavior,
    Binding,
    check_binding,
    check_kind_for_area,
    default_behavior,
)
from app.modbus.codec import clamp_value, decode, encode
from app.modbus.injection import Injection
from app.modbus.journal import JournalEntry
from app.modbus.ping import probe_slave
from app.modbus.slave_map import ModbusMapError
from app.modbus.types import AREAS, SLAVE_SPECS, Area, RegisterFormat

router = APIRouter(prefix="/api/modbus")


def get_services(request: Request) -> AppServices:
    """Сервисы приложения (см. `create_app`)."""
    services: AppServices = request.app.state.services
    return services


ServicesDep = Annotated[AppServices, Depends(get_services)]


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _ui_action_ip(request: Request, services: AppServices) -> str:
    """Rate-limit мутирующих действий из UI: `WEB_MAX_WRITE_PER_MIN_PER_IP`.

    Общий бюджет на IP с `POST /write` и `POST /ping` (ADR-004 п. 4): любые
    изменения из UI (поведения, снимки, инъекции) считаются одним окном
    (TASK-1004, ADR-012). Превышение → 429. Возвращает IP для журнала.
    """
    ip = _client_ip(request) or ""
    if not services.write_limiter.allow(ip):
        raise HTTPException(
            status_code=429,
            detail="слишком много изменений из UI, повторите позже",
        )
    return ip


def _behavior_out(behavior: Behavior, binding: Binding) -> BehaviorOut:
    slave, area, address = binding
    return BehaviorOut(
        slave=slave,
        area=area,
        address=address,
        kind=behavior.kind,
        params=behavior.params,
        locked=behavior.locked,
        is_default=behavior is default_behavior(slave, area, address),
    )


def _injection_out(injection: Injection) -> InjectionOut:
    return InjectionOut(
        id=injection.id,
        kind=injection.kind,
        slave=injection.slave,
        area=injection.area,
        address=injection.address,
        count=injection.count,
        code=injection.code,
        delay_ms=injection.delay_ms,
        ttl_s=injection.ttl_s,
        created_at=injection.created_at,
        expires_at=injection.expires_at,
        # Часы реестра — time.monotonic (по умолчанию); остаток TTL для UI.
        remaining_s=max(0.0, injection.expires_at - time.monotonic()),
    )


@router.get("/behaviors", response_model=BehaviorsResponse)
def get_behaviors(
    services: ServicesDep,
    slave: Annotated[int | None, Query(ge=1, le=10)] = None,
    area: Annotated[Area | None, Query()] = None,
    only_overrides: Annotated[bool, Query()] = False,
) -> BehaviorsResponse:
    """Полный конфиг поведений (TASK-301).

    Фильтры: `slave`, `area`, `only_overrides` (только переопределённые
    привязки). Без фильтров — все привязки слейвов 1..9 с их поведениями.
    """
    items: list[BehaviorOut] = []
    overrides = 0
    for (b_slave, b_area, b_address), behavior in services.registry.items():
        is_default = behavior is default_behavior(b_slave, b_area, b_address)
        if not is_default:
            overrides += 1
        if slave is not None and b_slave != slave:
            continue
        if area is not None and b_area != area:
            continue
        if only_overrides and is_default:
            continue
        items.append(_behavior_out(behavior, (b_slave, b_area, b_address)))
    return BehaviorsResponse(items=items, total=len(services.registry), overrides=overrides)


@router.put("/behaviors/{slave}/{area}/{address}", response_model=BehaviorOut)
def put_behavior(
    request: Request,
    body: BehaviorIn,
    slave: Annotated[int, Path(ge=1, le=10)],
    area: Area,
    address: Annotated[int, Path(ge=1)],
    services: ServicesDep,
) -> BehaviorOut:
    """Установить поведение привязки (TASK-302).

    Возвращает поведение после нормализации value-параметров по формату
    регистра. Ошибки привязки/kind → 400 (ADR-004 п. 5); действие из UI
    лимитируется общим `WEB_MAX_WRITE_PER_MIN_PER_IP` (429, TASK-1004).
    Запись в журнал — `behavior_set` c `source="web"`.
    """
    ip = _ui_action_ip(request, services)
    try:
        behavior = Behavior(body.kind, body.params, locked=body.locked)  # type: ignore[arg-type]
        services.registry.set(slave, area, address, behavior)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    stored = services.registry.get(slave, area, address)
    assert stored is not None  # только что установлена
    services.journal.add(
        JournalEntry(
            action="behavior_set",
            slave=slave,
            area=area,
            address=address,
            value={"kind": stored.kind, "params": stored.params, "locked": stored.locked},
            source="web",
            ip=ip,
        )
    )
    return _behavior_out(stored, (slave, area, address))


@router.post("/behaviors/batch", response_model=BatchResponse)
def post_behaviors_batch(
    request: Request,
    body: BatchIn,
    services: ServicesDep,
) -> BatchResponse:
    """Пакетная установка/сброс поведений (TASK-303).

    Атомарность: сперва валидируются все элементы (привязка, kind, область),
    затем применяются все. Ошибка в любом элементе → 400, ничего не применяется.
    Пакет не больше `MAX_BATCH_ITEMS` (422); действие из UI лимитируется общим
    `WEB_MAX_WRITE_PER_MIN_PER_IP` (429, TASK-1004). `remove=True` сбрасывает
    привязку к дефолту (ADR-003 п. 5). В журнал пишется по одной записи на
    элемент: `behavior_set` / `behavior_reset` (`source="web"`).
    """
    ip = _ui_action_ip(request, services)
    prepared: list[tuple[Binding, Behavior | None]] = []
    for index, item in enumerate(body.items):
        binding: Binding = (item.slave, item.area, item.address)
        try:
            if item.remove:
                if item.kind is not None:
                    raise ValueError("remove=true не сочетается с kind")
                check_binding(item.slave, item.area, item.address)
                prepared.append((binding, None))
            else:
                if item.kind is None:
                    raise ValueError("kind обязателен (либо remove=true)")
                behavior = Behavior(item.kind, item.params, locked=item.locked)  # type: ignore[arg-type]
                check_binding(item.slave, item.area, item.address)
                check_kind_for_area(behavior.kind, item.area)
                prepared.append((binding, behavior))
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=f"элемент {index}: {exc}") from exc

    applied = 0
    removed = 0
    for binding, new_behavior in prepared:
        slave, area, address = binding
        if new_behavior is None:
            services.registry.remove(slave, area, address)
            action: str = "behavior_reset"
            value: object = None
            removed += 1
        else:
            services.registry.set(slave, area, address, new_behavior)
            stored = services.registry.get(slave, area, address)
            assert stored is not None  # только что установлена
            action = "behavior_set"
            value = {"kind": stored.kind, "params": stored.params, "locked": stored.locked}
            applied += 1
        services.journal.add(
            JournalEntry(
                action=action,
                slave=slave,
                area=area,
                address=address,
                value=value,
                source="web",
                ip=ip,
            )
        )
    return BatchResponse(applied=applied, removed=removed)


@router.post("/behaviors/reset", response_model=ResetResponse)
def post_behaviors_reset(
    request: Request,
    body: ResetIn,
    services: ServicesDep,
) -> ResetResponse:
    """Сброс переопределений к дефолтам (TASK-304).

    Без `slave` — все переопределения, с `slave` — одного слейва. Возвращает
    число сброшенных (дефолтные привязки не считаются). Slave 10 поведений не
    имеет → 400. Действие из UI лимитируется общим `WEB_MAX_WRITE_PER_MIN_PER_IP`
    (429, TASK-1004). В журнал пишется `behavior_reset` по каждой сброшенной
    привязке.
    """
    ip = _ui_action_ip(request, services)
    if body.slave == 10:
        raise HTTPException(
            status_code=400,
            detail="slave 10: поведения не поддерживаются (IR вычисляются)",
        )
    doomed = [
        binding
        for binding, behavior in services.registry.items()
        if (body.slave is None or binding[0] == body.slave)
        and behavior is not default_behavior(*binding)
    ]
    count = services.registry.reset(body.slave)
    for slave, area, address in doomed:
        services.journal.add(
            JournalEntry(
                action="behavior_reset",
                slave=slave,
                area=area,
                address=address,
                source="web",
                ip=ip,
            )
        )
    return ResetResponse(reset=count)


@router.get("/state", response_model=StateResponse)
def get_state(
    services: ServicesDep,
    slave: Annotated[int, Query(ge=1, le=10)],
    area: Annotated[Area | None, Query()] = None,
) -> StateResponse:
    """Снимок значений слейва (TASK-305).

    Без `area` — все области, которые есть на слейве (для slave 10 — только ir).
    Значения — по сетке адресов, декодированы по формату слейва. Запрос области,
    отсутствующей на слейве (cl/di/hr на slave 10), → 400.
    """
    smap = services.maps[slave]
    if area is not None:
        if not smap.spec.area_ranges(area):
            raise HTTPException(status_code=400, detail=f"slave {slave}: область {area} отсутствует")
        areas_to_read: tuple[Area, ...] = (area,)
    else:
        areas_to_read = tuple(a for a in AREAS if smap.spec.area_ranges(a))
    return StateResponse(
        slave=slave,
        fmt=smap.spec.fmt,
        areas={a: area_values(smap, a) for a in areas_to_read},
    )


@router.post("/write", response_model=WriteResponse)
def post_write(
    request: Request,
    body: WriteIn,
    services: ServicesDep,
) -> WriteResponse:
    """Запись значения CL/HR из UI (TASK-306).

    Только cl (bool) и hr (число): ir/di генерируются движком (инвариант 2).
    Rate-limit — общий `WEB_MAX_WRITE_PER_MIN_PER_IP` на IP со всеми
    действиями из UI (429, ADR-004 п. 4, TASK-1004); запись в
    locked-привязку → 409 (write_policy, ADR-003 п. 3). Значение
    приводится к формату регистра (clamp), в ответе — итог. Журнал: `write`
    (`source="web"`), отклонённые записи фиксирует политика.
    """
    ip = _client_ip(request) or ""
    if not services.write_limiter.allow(ip):
        raise HTTPException(
            status_code=429,
            detail="слишком много записей из UI, повторите позже",
        )
    if body.area not in ("cl", "hr"):
        raise HTTPException(
            status_code=400,
            detail=f"запись из UI поддерживается только для cl/hr: {body.area}",
        )
    spec = SLAVE_SPECS[body.slave]
    if not spec.has_address(body.area, body.address):
        raise HTTPException(
            status_code=400,
            detail=f"slave {body.slave}: {body.area} адрес {body.address} вне карты значений",
        )
    if body.area == "cl":
        if not isinstance(body.value, bool):
            raise HTTPException(status_code=400, detail="для cl ожидается bool")
        written: bool | int | float = body.value
        values: list[int] | list[bool] = [body.value]
    else:
        if isinstance(body.value, bool):
            raise HTTPException(status_code=400, detail="для hr ожидается число")
        written = clamp_value(body.value, spec.fmt)
        values = encode(written, spec.fmt)
    try:
        services.write_policy(body.slave, body.area, body.address, values, source="web", ip=ip)
    except ModbusMapError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    smap = services.maps[body.slave]
    if body.area == "cl":
        smap.set_bits("cl", body.address, [bool(v) for v in values])
    else:
        smap.set_registers("hr", body.address, [int(v) for v in values])
    services.journal.add(
        JournalEntry(
            action="write",
            slave=body.slave,
            area=body.area,
            address=body.address,
            value=written,
            source="web",
            ip=ip,
        )
    )
    return WriteResponse(slave=body.slave, area=body.area, address=body.address, value=written)


@router.post("/snapshot", response_model=Snapshot)
def post_snapshot(services: ServicesDep) -> Snapshot:
    """Снять снимок состояния всех слейвов (TASK-307).

    Все области, значения по сетке адресов, декодированные по формату слейва
    (slave 10 — только ir, см. `docs/HRIR_SPEC.md`). Снимок — JSON в ответе,
    сервер ничего не хранит (инвариант 9).
    """
    slaves: dict[int, dict[Area, dict[str, int | float | bool]]] = {}
    for slave_id, smap in services.maps.items():
        slaves[slave_id] = {a: area_values(smap, a) for a in AREAS if smap.spec.area_ranges(a)}
    return Snapshot(slaves=slaves)


def _diff_snapshots(a: Snapshot, b: Snapshot) -> tuple[list[SnapshotDiffRow], int]:
    """Различия снимков: строки только для несовпавших значений + счётчик совпавших.

    `None` в строке — записи нет в этом снимке (значение добавилось/пропало).
    Порядок строк: slave → area → address.
    """
    rows: list[SnapshotDiffRow] = []
    same = 0
    for slave in sorted(set(a.slaves) | set(b.slaves)):
        areas_a = a.slaves.get(slave, {})
        areas_b = b.slaves.get(slave, {})
        for area in AREAS:
            values_a = areas_a.get(area, {})
            values_b = areas_b.get(area, {})
            for address_str in sorted(set(values_a) | set(values_b), key=int):
                value_a = values_a.get(address_str)
                value_b = values_b.get(address_str)
                if (
                    address_str in values_a
                    and address_str in values_b
                    and value_a == value_b
                ):
                    same += 1
                    continue
                rows.append(
                    SnapshotDiffRow(
                        slave=slave,
                        area=area,
                        address=int(address_str),
                        a=value_a,
                        b=value_b,
                    )
                )
    return rows, same


@router.post("/snapshot/diff", response_model=SnapshotDiffResponse)
def post_snapshot_diff(body: SnapshotDiffIn) -> SnapshotDiffResponse:
    """Дифф двух снимков состояния (TASK-744).

    `a` — «до», `b` — «после»; в ответе только различия (изменённые,
    добавленные, пропавшие значения) и число совпавших. Чистая функция:
    без состояния, лимитов и журнала. Версия снимка ≠ 1 → 400 (как в restore);
    нечисловой ключ адреса → 400, а не 500 (TASK-1004).
    """
    for name, snapshot in (("a", body.a), ("b", body.b)):
        if snapshot.version != 1:
            raise HTTPException(
                status_code=400,
                detail=f"неподдерживаемая версия снимка {name}: {snapshot.version}",
            )
        for areas in snapshot.slaves.values():
            for values in areas.values():
                for raw_address in values:
                    try:
                        int(raw_address)
                    except ValueError as exc:
                        raise HTTPException(
                            status_code=400,
                            detail=f"снимок {name}: некорректный адрес {raw_address!r}",
                        ) from exc
    rows, same = _diff_snapshots(body.a, body.b)
    return SnapshotDiffResponse(rows=rows, same=same)


@router.post("/restore", response_model=RestoreResponse)
def post_restore(
    request: Request,
    body: Snapshot,
    services: ServicesDep,
) -> RestoreResponse:
    """Восстановить значения из снимка (TASK-307).

    Пишутся только hr/cl: ir/di генерируются движком (инвариант 2), их
    значения в снимке игнорируются (`ignored`). Атомарно по валидации: любая
    ошибка формата/адреса → 400, ничего не пишется. Locked-привязки
    пропускаются (write_policy, ADR-003 п. 3) — `skipped_locked`. Действие из
    UI лимитируется общим `WEB_MAX_WRITE_PER_MIN_PER_IP` (429, TASK-1004).
    В журнал пишется `restore` по каждой записанной привязке.
    """
    ip = _ui_action_ip(request, services)
    if body.version != 1:
        raise HTTPException(
            status_code=400,
            detail=f"неподдерживаемая версия снимка: {body.version}",
        )
    prepared: list[tuple[int, Area, int, bool | int | float]] = []
    ignored = 0
    for slave, areas in body.slaves.items():
        if slave not in SLAVE_SPECS:
            raise HTTPException(status_code=400, detail=f"неизвестный slave: {slave}")
        spec = SLAVE_SPECS[slave]
        for area, values in areas.items():
            if area not in ("cl", "hr"):
                ignored += len(values)  # ir/di генерируются движком
                continue
            for raw_address, value in values.items():
                try:
                    address = int(raw_address)
                except ValueError as exc:
                    raise HTTPException(
                        status_code=400, detail=f"slave {slave}: некорректный адрес {raw_address!r}"
                    ) from exc
                if not spec.has_address(area, address):
                    raise HTTPException(
                        status_code=400,
                        detail=f"slave {slave}: {area} адрес {address} вне карты значений",
                    )
                if area == "cl" and not isinstance(value, bool):
                    raise HTTPException(
                        status_code=400, detail=f"slave {slave}: cl {address} — ожидается bool"
                    )
                if area == "hr" and isinstance(value, bool):
                    raise HTTPException(
                        status_code=400, detail=f"slave {slave}: hr {address} — ожидается число"
                    )
                prepared.append((slave, area, address, value))

    restored = 0
    skipped_locked = 0
    for slave, area, address, value in prepared:
        spec = SLAVE_SPECS[slave]
        if area == "cl":
            written: bool | int | float = bool(value)
            values_out: list[int] | list[bool] = [bool(value)]
        else:
            written = clamp_value(value, spec.fmt)
            values_out = encode(written, spec.fmt)
        try:
            services.write_policy(slave, area, address, values_out, source="web", ip=ip)
        except ModbusMapError:
            skipped_locked += 1  # политика уже журналировала write_rejected_locked
            continue
        smap = services.maps[slave]
        if area == "cl":
            smap.set_bits("cl", address, [bool(v) for v in values_out])
        else:
            smap.set_registers("hr", address, [int(v) for v in values_out])
        services.journal.add(
            JournalEntry(
                action="restore",
                slave=slave,
                area=area,
                address=address,
                value=written,
                source="web",
                ip=ip,
            )
        )
        restored += 1
    return RestoreResponse(restored=restored, skipped_locked=skipped_locked, ignored=ignored)


@router.get("/journal", response_model=JournalResponse)
def get_journal(
    services: ServicesDep,
    limit: Annotated[int | None, Query(ge=1)] = None,
) -> JournalResponse:
    """Записи журнала событий (TASK-308).

    От старых к новым; `limit` — максимум последних записей. Источник —
    кольцевой буфер `JOURNAL_SIZE` (TASK-505): при переполнении старые записи
    вытесняются, хранение только в памяти (инвариант 9).
    """
    entries = services.journal.entries(limit)
    return JournalResponse(
        items=[
            JournalEntryOut(
                action=entry.action,
                slave=entry.slave,
                area=entry.area,
                address=entry.address,
                value=entry.value,
                source=entry.source,
                ip=entry.ip,
                timestamp=entry.timestamp,
            )
            for entry in entries
        ],
        total=len(services.journal),
    )


@router.get("/inject", response_model=InjectionsResponse)
def get_inject(services: ServicesDep) -> InjectionsResponse:
    """Активные инъекции ошибок (TASK-743).

    Порядок — как добавлялись; истёкшие не возвращаются (ленивый TTL реестра).
    Быстрый read-only: без журнала и лимитов. `max_ttl_s` — предел TTL для UI.
    """
    return InjectionsResponse(
        items=[_injection_out(injection) for injection in services.injections.items()],
        max_ttl_s=services.injections.max_ttl_s,
    )


@router.post("/inject", response_model=InjectionOut)
def post_inject(
    request: Request,
    body: InjectionIn,
    services: ServicesDep,
) -> InjectionOut:
    """Зарегистрировать инъекцию ошибки (TASK-309).

    Инъекция попадает в реестр с ленивым TTL ≤ `INJECTION_MAX_TTL_S`
    (ADR-004 п. 6); активных инъекций не больше `MAX_ACTIVE_INJECTIONS`
    (TASK-1004). Ошибки параметров и переполнение реестра → 400. Действие из
    UI лимитируется общим `WEB_MAX_WRITE_PER_MIN_PER_IP` (429). Журнал:
    `inject` (`source="web"`).
    """
    ip = _ui_action_ip(request, services)
    try:
        injection = services.injections.add(
            body.kind,
            body.slave,
            area=body.area,
            address=body.address,
            count=body.count,
            code=body.code,
            delay_ms=body.delay_ms,
            ttl_s=body.ttl_s,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    services.journal.add(
        JournalEntry(
            action="inject",
            slave=injection.slave,
            area=injection.area,
            address=injection.address,
            value={
                "id": injection.id,
                "kind": injection.kind,
                "code": injection.code,
                "delay_ms": injection.delay_ms,
                "ttl_s": injection.ttl_s,
            },
            source="web",
            ip=ip,
        )
    )
    return _injection_out(injection)


@router.post("/inject/clear", response_model=InjectClearResponse)
def post_inject_clear(
    request: Request,
    body: InjectClearIn,
    services: ServicesDep,
) -> InjectClearResponse:
    """Снять инъекции ошибок (TASK-309, TASK-605).

    Фильтры: `id` — конкретная инъекция, `slave` — все инъекции слейва, без
    фильтров — все. Возвращает число снятых; каждая фиксируется в журнале
    (`inject_clear`, `source="web"`). Действие из UI лимитируется общим
    `WEB_MAX_WRITE_PER_MIN_PER_IP` (429, TASK-1004).
    """
    ip = _ui_action_ip(request, services)
    doomed = [
        injection
        for injection in services.injections.items()
        if (body.id is None or injection.id == body.id)
        and (body.slave is None or injection.slave == body.slave)
    ]
    cleared = services.injections.clear(injection_id=body.id, slave=body.slave)
    for injection in doomed:
        services.journal.add(
            JournalEntry(
                action="inject_clear",
                slave=injection.slave,
                area=injection.area,
                address=injection.address,
                value={"id": injection.id, "kind": injection.kind},
                source="web",
                ip=ip,
            )
        )
    return InjectClearResponse(cleared=cleared)


@router.post("/ping", response_model=PingResponse)
async def post_ping(
    request: Request,
    body: PingIn,
    services: ServicesDep,
) -> PingResponse:
    """Проба соединения: серверный опрос выбранного slave (TASK-702).

    Сервер через loopback делает `count` контрольных чтений slave и возвращает
    min/avg/max RTT и число успешных/ошибочных попыток (см. `app.modbus.ping`).
    Ограничение действий из UI — тот же лимит `WEB_MAX_WRITE_PER_MIN_PER_IP`
    на IP (429, ADR-004 п. 4); проба не пишет в регистры и не журналируется
    (анти-флуд журнала). Async-роут: опрос не занимает threadpool.
    """
    ip = _client_ip(request) or ""
    if not services.write_limiter.allow(ip):
        raise HTTPException(
            status_code=429,
            detail="слишком много запросов из UI, повторите позже",
        )
    result = await probe_slave(services.config, body.slave, body.count)
    return PingResponse(
        slave=result.slave,
        attempts=result.attempts,
        ok=result.ok,
        errors=result.errors,
        rtt_min_ms=result.rtt_min_ms,
        rtt_avg_ms=result.rtt_avg_ms,
        rtt_max_ms=result.rtt_max_ms,
        last_error=result.last_error,
    )


#: Порядки конвертера (TASK-741) — все 4, как в `RegisterFormat`.
_CONVERT_ORDERS: tuple[Literal["ABCD", "CDAB", "BADC", "DCBA"], ...] = ("ABCD", "CDAB", "BADC", "DCBA")


def _convert_value(value: float, kind: Literal["uint32", "float"]) -> int | float:
    """Нормализовать значение конвертера: uint32 — целое в диапазоне, float — конечный."""
    if kind == "uint32":
        if isinstance(value, bool) or not float(value).is_integer():
            raise HTTPException(status_code=400, detail="uint32 требует целого значения")
        int_value = int(value)
        if not 0 <= int_value <= 0xFFFFFFFF:
            raise HTTPException(status_code=400, detail="uint32 вне диапазона 0..4294967295")
        return int_value
    float_value = float(value)
    if not math.isfinite(float_value):
        raise HTTPException(status_code=400, detail="NaN/Inf недопустимы")
    return float_value


def _finite(value: float) -> int | float | str:
    """NaN/Inf в JSON не сериализуются — для float-раскладок отдаём строками."""
    if isinstance(value, float) and not math.isfinite(value):
        return "NaN" if math.isnan(value) else ("inf" if value > 0 else "-inf")
    return value


@router.post("/convert", response_model=ConvertResponse)
def post_convert(body: ConvertIn) -> ConvertResponse:
    """Конвертер байт/слов: значение ↔ регистры в 4 порядках (TASK-741).

    Ровно одно из полей входа: `value` — значение (uint32/float) переводится
    в пару регистров для каждого порядка ABCD/CDAB/BADC/DCBA; `regs` — пара
    регистров переводится в значение для каждого порядка. Пересчёт — кодексом
    `app.modbus.codec`, поэтому результат совпадает с поведением слейвов 2..9.
    Чистая функция: без состояния, лимитов и журнала.
    """
    if body.value is not None and body.regs is not None:
        raise HTTPException(status_code=400, detail="укажите value или regs, но не оба")
    rows: list[ConvertRow] = []
    if body.value is not None:
        value = _convert_value(body.value, body.kind)
        for order in _CONVERT_ORDERS:
            fmt = RegisterFormat(f"{body.kind}_{order.lower()}")
            try:
                regs = encode(value, fmt)
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            rows.append(
                ConvertRow(
                    order=order,
                    regs=(regs[0], regs[1]),
                    value=_finite(decode(regs, fmt)),
                )
            )
    elif body.regs is not None:
        for order in _CONVERT_ORDERS:
            fmt = RegisterFormat(f"{body.kind}_{order.lower()}")
            try:
                decoded = decode(body.regs, fmt)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            rows.append(ConvertRow(order=order, regs=body.regs, value=_finite(decoded)))
    else:
        raise HTTPException(status_code=400, detail="укажите value или regs")
    return ConvertResponse(kind=body.kind, rows=rows)


@router.post("/configgen", response_model=ConfigGenResponse)
def post_configgen(body: ConfigGenIn, services: ServicesDep) -> ConfigGenResponse:
    """Генератор конфигов клиентских инструментов (TASK-742).

    Modbus Poll / QModMaster / CAS Modbus Scanner — карточки настроек,
    pyModbus — готовый скрипт чтения, Node-RED — импортируемый flow. Цель
    проверяется по `SLAVE_SPECS` (400 вне карты/лимита FC 3/4). Чистая
    функция: без состояния, лимитов и журнала.
    """
    target = ConfigTarget(
        slave=body.slave,
        area=body.area,
        address=body.address,
        count=body.count,
        host=body.host or PUBLIC_HOST,
        port=body.port or services.config.MODBUS_TCP_PORT,
    )
    try:
        result = generate(body.tool, target)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ConfigGenResponse(tool=body.tool, filename=result.filename, content=result.content)


@router.get("/metrics", response_model=MetricsResponse)
def get_metrics(services: ServicesDep) -> MetricsResponse:
    """Счётчики Modbus-сервера (TASK-703).

    Активные соединения, нарастающие итоги запросов/ошибок, скорости за
    скользящее окно и uptime (см. `app.modbus.metrics`). UI поллит эндпоинт;
    быстрый read-only, без лимитов и журнала.
    """
    snapshot = services.metrics.snapshot()
    return MetricsResponse(
        active_connections=snapshot.active_connections,
        requests_total=snapshot.requests_total,
        errors_total=snapshot.errors_total,
        req_per_s=snapshot.req_per_s,
        err_per_s=snapshot.err_per_s,
        uptime_s=snapshot.uptime_s,
    )


#: Healthcheck — вне префикса /api/modbus (ADR-004 п. 1).
health_router = APIRouter()


@health_router.get("/healthz", response_model=HealthzResponse)
def get_healthz() -> HealthzResponse:
    """Проверка живости процесса (TASK-310).

    Используется healthcheck-ом деплоя (TASK-903) и мониторингом; отвечает
    быстро и без побочных эффектов — только подтверждает, что event loop жив.
    """
    return HealthzResponse(status="ok")


#: Метрики Prometheus — вне префикса /api/modbus, как /healthz (TASK-904).
metrics_router = APIRouter()


@metrics_router.get("/metrics", response_class=PlainTextResponse)
def get_prometheus_metrics(services: ServicesDep) -> PlainTextResponse:
    """Счётчики сервера в экспозиции Prometheus (TASK-904).

    Те же данные, что `GET /api/modbus/metrics` отдаёт UI, но в текстовом
    формате Prometheus для систем мониторинга.
    """
    return PlainTextResponse(
        render_prometheus(services.metrics.snapshot()),
        media_type=PROMETHEUS_MEDIA_TYPE,
    )

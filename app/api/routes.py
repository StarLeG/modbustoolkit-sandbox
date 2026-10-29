"""REST-роуты `/api/modbus/*` (Эпик 3, ADR-004)."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request

from app.api.models import (
    BatchIn,
    BatchResponse,
    BehaviorIn,
    BehaviorOut,
    BehaviorsResponse,
    ResetIn,
    ResetResponse,
    StateResponse,
    WriteIn,
    WriteResponse,
)
from app.api.services import AppServices
from app.modbus.behavior import (
    Behavior,
    Binding,
    check_binding,
    check_kind_for_area,
    default_behavior,
)
from app.modbus.codec import clamp_value, decode, encode
from app.modbus.journal import JournalEntry
from app.modbus.slave10 import TIMESTAMP_ADDR
from app.modbus.slave_map import ModbusMapError, SlaveMap
from app.modbus.types import AREAS, SLAVE_SPECS, Area, RegisterFormat

router = APIRouter(prefix="/api/modbus")


def get_services(request: Request) -> AppServices:
    """Сервисы приложения (см. `create_app`)."""
    services: AppServices = request.app.state.services
    return services


ServicesDep = Annotated[AppServices, Depends(get_services)]


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


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


def _slave10_ir_values(smap: SlaveMap) -> dict[str, int | float | bool]:
    """IR slave 10: 1..268 — uint16-метрики, 300 — Unix timestamp (UINT32_ABCD)."""
    values: dict[str, int | float | bool] = {}
    for address in range(1, 269):
        values[str(address)] = smap.get_registers("ir", address, 1)[0]
    values[str(TIMESTAMP_ADDR)] = decode(
        smap.get_registers("ir", TIMESTAMP_ADDR, 2), RegisterFormat.UINT32_ABCD
    )
    return values


def _area_values(smap: SlaveMap, area: Area) -> dict[str, int | float | bool]:
    """Значения области по сетке адресов (TASK-305).

    hr/ir декодируются по формату слейва (для 32-битных — значения 1,3,…,1023),
    cl/di — bool. Неинициализированные регистры читаются как 0 (sparse-хранилище).
    """
    if smap.spec.slave_id == 10 and area == "ir":
        return _slave10_ir_values(smap)
    fmt = smap.spec.fmt
    values: dict[str, int | float | bool] = {}
    bit_area = area in ("cl", "di")
    for rng in smap.spec.area_ranges(area):
        for address in range(rng.start, rng.stop + 1, rng.step):
            if bit_area:
                values[str(address)] = smap.get_bits(area, address, 1)[0]
            else:
                raw = smap.get_registers(area, address, fmt.registers_per_value)
                values[str(address)] = decode(raw, fmt)
    return values


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
    регистра. Ошибки привязки/kind → 400 (ADR-004 п. 5); запись в журнал —
    `behavior_set` c `source="web"`.
    """
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
            ip=_client_ip(request),
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
    `remove=True` сбрасывает привязку к дефолту (ADR-003 п. 5). В журнал пишется
    по одной записи на элемент: `behavior_set` / `behavior_reset` (`source="web"`).
    """
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

    ip = _client_ip(request)
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
    имеет → 400. В журнал пишется `behavior_reset` по каждой сброшенной привязке.
    """
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
    ip = _client_ip(request)
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
        areas={a: _area_values(smap, a) for a in areas_to_read},
    )


@router.post("/write", response_model=WriteResponse)
def post_write(
    request: Request,
    body: WriteIn,
    services: ServicesDep,
) -> WriteResponse:
    """Запись значения CL/HR из UI (TASK-306).

    Только cl (bool) и hr (число): ir/di генерируются движком (инвариант 2).
    Rate-limit — `WEB_MAX_WRITE_PER_MIN_PER_IP` на IP (429, ADR-004 п. 4);
    запись в locked-привязку → 409 (write_policy, ADR-003 п. 3). Значение
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

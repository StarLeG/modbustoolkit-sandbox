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
)
from app.api.services import AppServices
from app.modbus.behavior import (
    Behavior,
    Binding,
    check_binding,
    check_kind_for_area,
    default_behavior,
)
from app.modbus.journal import JournalEntry
from app.modbus.types import Area

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

"""REST-роуты `/api/modbus/*` (Эпик 3, ADR-004)."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.api.models import BehaviorOut, BehaviorsResponse
from app.api.services import AppServices
from app.modbus.behavior import default_behavior
from app.modbus.types import Area

router = APIRouter(prefix="/api/modbus")


def get_services(request: Request) -> AppServices:
    """Сервисы приложения (см. `create_app`)."""
    services: AppServices = request.app.state.services
    return services


ServicesDep = Annotated[AppServices, Depends(get_services)]


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
        items.append(
            BehaviorOut(
                slave=b_slave,
                area=b_area,
                address=b_address,
                kind=behavior.kind,
                params=behavior.params,
                locked=behavior.locked,
                is_default=is_default,
            )
        )
    return BehaviorsResponse(items=items, total=len(services.registry), overrides=overrides)

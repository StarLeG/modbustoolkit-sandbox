"""FastAPI-приложение: фабрика и lifespan (TASK-301, ADR-004 п. 2).

`create_app` собирает web-слой поверх `AppServices`; lifespan запускает и
останавливает движок поведений. Для детерминированных тестов — `run_engine=False`:
тесты управляют тиками сами (`engine.tick(now)`).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import health_router, router
from app.api.services import AppServices, build_services
from app.modbus.config import Config


def create_app(
    config: Config | None = None,
    services: AppServices | None = None,
    *,
    run_engine: bool = True,
) -> FastAPI:
    """Создать приложение REST (uvicorn: `app.api.app:create_app --factory`).

    `services` — готовый контейнер (тесты); без него сервисы собираются из
    `config` (или env). `run_engine=False` не запускает фоновый тик-цикл.
    """
    if services is None:
        services = build_services(config)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if run_engine:
            services.engine.start()
        try:
            yield
        finally:
            if run_engine:
                await services.engine.stop()

    app = FastAPI(title="modbustoolkit.electroman.by", lifespan=lifespan)
    app.state.services = services
    app.include_router(router)
    app.include_router(health_router)
    return app

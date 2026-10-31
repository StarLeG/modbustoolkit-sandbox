"""FastAPI-приложение: фабрика и lifespan (TASK-301, ADR-004 п. 2).

`create_app` собирает web-слой поверх `AppServices`; lifespan запускает и
останавливает движок поведений. Для детерминированных тестов — `run_engine=False`:
тесты управляют тиками сами (`engine.tick(now)`).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.pages import pages_router
from app.api.routes import health_router, metrics_router, router
from app.api.services import AppServices, build_services
from app.api.ws import ws_router
from app.modbus.config import Config

#: Статика UI (CSS/JS) — ADR-008 п. 3.
_STATIC_DIR = Path(__file__).resolve().parent / "static"


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

    # Swagger — свой роут /api/docs в pages.py (в стиле сайта, TASK-751);
    # встроенный FastAPI-Swagger выключен, redoc остаётся под /api/redoc.
    # Корневой /docs отдан странице «Документация» (ADR-008 п. 2).
    app = FastAPI(
        title="Modbus Sandbox API",
        description=(
            "REST API публичного тестового Modbus-TCP сервера MODBUS·SANDBOX "
            "(modbustoolkit.electroman.by). Сервер бесплатно эмулирует 10 "
            "устройств с unit ID от 1 до 10. Значения регистров живут в "
            "памяти и общие для всех подключённых пользователей, перезапуск "
            "сервера всё сбрасывает."
        ),
        lifespan=lifespan,
        docs_url=None,
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
        openapi_tags=[
            {
                "name": "Поведения",
                "description": "Генерация значений: счётчики, волны, шум и другие поведения.",
            },
            {
                "name": "Значения",
                "description": "Текущие значения слейвов, запись и проба соединения.",
            },
            {
                "name": "Снимки",
                "description": "Снимки состояния, сравнение и восстановление.",
            },
            {"name": "Журнал", "description": "События сервера: записи, поведения, инъекции."},
            {
                "name": "Инъекции",
                "description": "Имитация сбоев устройства: ошибки, задержки, молчание.",
            },
            {
                "name": "Инструменты",
                "description": "Конвертер байт/слов и готовые конфиги для клиентских программ.",
            },
            {"name": "Служебное", "description": "Здоровье процесса и счётчики для мониторинга."},
        ],
    )
    app.state.services = services
    app.include_router(router)
    app.include_router(health_router)
    app.include_router(metrics_router)
    app.include_router(pages_router)
    app.include_router(ws_router)
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
    return app

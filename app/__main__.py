"""Сквозной запуск: web UI и Modbus-TCP в одном event loop (CLAUDE.md, ADR-001).

Оба сервиса разделяют одни `AppServices`: карты слейвов, движок поведений,
журнал, инъекции и метрики общие. Modbus-клиент и UI видят одни и те же
значения, включая демо-поведения (ADR-009).

Запуск: `uv run python -m app`.
Web-слушатель: `WEB_HOST` / `WEB_PORT`; Modbus: `MODBUS_TCP_*` (см. .env.example).
"""
from __future__ import annotations

import asyncio
import os

import uvicorn

from app.api.app import create_app
from app.api.services import build_services
from app.modbus.server import SlaveMapContext, serve

WEB_HOST_ENV = "WEB_HOST"
WEB_PORT_ENV = "WEB_PORT"
WEB_HOST_DEFAULT = "127.0.0.1"
WEB_PORT_DEFAULT = "8765"


async def main() -> None:
    """Поднять Modbus-TCP и FastAPI на общих сервисах до сигнала остановки."""
    services = build_services()
    config = services.config
    context = SlaveMapContext(services.maps, services.write_policy)
    uv_config = uvicorn.Config(
        create_app(services=services),
        host=os.environ.get(WEB_HOST_ENV, WEB_HOST_DEFAULT),
        port=int(os.environ.get(WEB_PORT_ENV, WEB_PORT_DEFAULT)),
        log_level="info",
    )
    server = uvicorn.Server(uv_config)
    async with serve(config, context, services.injections, services.metrics):
        await server.serve()


if __name__ == "__main__":
    asyncio.run(main())

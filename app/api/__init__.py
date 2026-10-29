"""Web-слой: FastAPI REST поверх сервисов ядра (Эпик 3, ADR-004)."""
from __future__ import annotations

from app.api.app import create_app
from app.api.services import AppServices, build_services

__all__ = ["AppServices", "build_services", "create_app"]

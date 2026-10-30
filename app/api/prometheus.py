"""Экспозиция счётчиков в формате Prometheus (TASK-904).

Формат текстовый (exposition format 0.0.4), без сторонних зависимостей:
prometheus_client в проект не добавлялся, счётчиков мало и их проще
отдать строкой. Рендер — чистая функция над `MetricsSnapshot`.
"""
from __future__ import annotations

from app.modbus.metrics import MetricsSnapshot

#: Тело ответа с медиа-типом по спецификации Prometheus.
MEDIA_TYPE = "text/plain; version=0.0.4; charset=utf-8"

#: (имя, тип, help, значение) — порядок строк в ответе.
_METRICS = (
    (
        "modbus_active_connections",
        "gauge",
        "Открытые сейчас соединения Modbus-клиентов.",
    ),
    (
        "modbus_requests_total",
        "counter",
        "Всего обработанных Modbus-запросов с запуска сервера.",
    ),
    (
        "modbus_errors_total",
        "counter",
        "Всего ответов с ошибкой (exception, молчание, сбой обработки).",
    ),
    (
        "modbus_requests_per_second",
        "gauge",
        "Modbus-запросов в секунду за скользящее окно.",
    ),
    (
        "modbus_errors_per_second",
        "gauge",
        "Ошибочных ответов в секунду за скользящее окно.",
    ),
    (
        "modbus_uptime_seconds",
        "gauge",
        "Время работы сервера с запуска, секунд.",
    ),
)


def render_prometheus(snapshot: MetricsSnapshot) -> str:
    """Собрать текст экспозиции Prometheus из снимка счётчиков."""
    values = (
        snapshot.active_connections,
        snapshot.requests_total,
        snapshot.errors_total,
        snapshot.req_per_s,
        snapshot.err_per_s,
        snapshot.uptime_s,
    )
    lines: list[str] = []
    for (name, metric_type, help_text), value in zip(_METRICS, values, strict=True):
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {metric_type}")
        lines.append(f"{name} {value}")
    return "\n".join(lines) + "\n"

# ADR-000 · Подтверждение стека

- **Статус:** Accepted
- **Дата:** 2026-10-29
- **Контекст:** TASK-001 — изучить проект и подтвердить стек перед началом работ

## Результат аудита проекта

На момент старта в директории `modbus-test-server` отсутствовал какой-либо код:
наличествовал только исходный документ `ТЗ.md` (пакет артефактов проекта).
`pyproject.toml`, `package.json`, `Dockerfile`, `docker-compose.yml`, `nginx/`,
`.github/workflows/` — отсутствуют. Проект **greenfield**, конфликтов с существующим
стеком нет.

## Доступный тулчейн

| Инструмент | Версия / статус |
|---|---|
| Python | 3.14.6 (системный; `requires-python >= 3.11`) |
| uv | доступен (менеджмент venv и зависимостей) |
| git | 2.55 |
| Docker | доступен (пригодится в Эпике 9) |

Примечание: если у какой-либо зависимости окажется отсутствующий wheel под CPython 3.14,
venv создаётся через `uv python pin 3.12` — на `requires-python` это не влияет.

## Решение

Принят **целевой стек из `CLAUDE.md`** без изменений:

- **Python 3.11+**, `asyncio` — единый event loop (см. ADR-001, решение 1).
- **pymodbus** (async API) — Modbus-TCP сервер, `ModbusServerContext(single=False)`.
- **FastAPI + uvicorn** — REST + WebSocket (`/api/modbus/*`, `/ws/state`).
- **pytest + pytest-asyncio** — тесты (`tests/` unit, `tests/integration/`).
- **ruff** — линтинг, **mypy** (strict) — проверка типов.
- **Логирование — stdlib `logging`** (вариант из «Соглашений по коду»; structlog
  не используется — минимум зависимостей без потери функциональности).
- **Docker / docker-compose + nginx** — деплой (Эпик 9, по мере готовности).

Управление зависимостями — `pyproject.toml` + uv; версия проекта указана **только**
в `pyproject.toml` (SemVer, старт `0.1.0`).

## Ссылки

- `CLAUDE.md` — раздел «Стек и архитектура (ожидаемые)»
- `docs/adr/ADR-001-architecture.md` — архитектурные решения

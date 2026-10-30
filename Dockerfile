# TASK-901: образ Modbus-TCP сервера с web UI (multi-stage).
#
# Сборка:  docker build -t modbus-test-server .
# Запуск:  docker compose up -d   (см. docker-compose.yml)
#
# Стадия builder готовит venv через uv (файл uv.lock — источник версий),
# runtime-стадия минимальная: python-slim без тулчейна и исходников.
# Внутренние порты фиксированы (WEB_PORT=8765, MODBUS_TCP_PORT=5020),
# наружу маппятся в docker-compose.yml.

# --- builder: зависимости ----------------------------------------------------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /build

# Сначала только метаданные проекта: слой зависимостей кэшируется,
# пока не меняются pyproject.toml / uv.lock.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Исходники и установка самого пакета (app/) в venv. Без --no-editable uv
# ставит проект ссылкой на /build, которой в runtime-стадии нет.
COPY app ./app
RUN uv sync --frozen --no-dev --no-editable

# --- runtime: минимальный образ ----------------------------------------------
FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.title="modbus-test-server" \
      org.opencontainers.image.description="Публичный тестовый Modbus-TCP сервер (modbustoolkit.electroman.by)"

# Приложение работает под непривилегированным пользователем.
RUN useradd --create-home --uid 10001 appuser

COPY --from=builder /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Порт только в документации EXPOSE: реальный маппинг — docker-compose.yml.
EXPOSE 5020/tcp 8765/tcp

USER appuser
WORKDIR /home/appuser

CMD ["python", "-m", "app"]

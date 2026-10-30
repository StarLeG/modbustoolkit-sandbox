# Деплой Modbus-TCP сервера

Инструкция для запуска публичного тестового сервера (modbustoolkit.electroman.by):
веб-интерфейс с REST/WS и Modbus-TCP на 10 unit ID. Значения регистров живут
в памяти, перезапуск их сбрасывает - это задумано.

## Требования

- Python 3.11 и новее, для локального запуска ещё [uv](https://docs.astral.sh/uv/);
- Docker с плагином Compose 2.24 или новее (нужен синтаксис `!reset`) для контейнеров;
- systemd - если запускаете без Docker на Linux.

## Вариант 1: локально

```bash
uv sync
uv run python -m app
```

Слушатели берут адреса и порты из переменных окружения (см. `.env.example`):
по умолчанию Modbus на `0.0.0.0:5020`, веб на `127.0.0.1:8765`.

## Вариант 2: Docker Compose

```bash
docker compose up -d --build
```

Образ собирается multi-stage (см. `Dockerfile`), приложение работает под
непривилегированным пользователем. Порты наружу маппятся на те же номера,
что заданы в `MODBUS_TCP_PORT` и `WEB_PORT`; переопределяются через `.env`
рядом с `docker-compose.yml` или переменными окружения хоста.

Статус и логи:

```bash
docker compose ps
docker compose logs -f app
```

## Вариант 3: за nginx

```bash
docker compose -f docker-compose.yml -f docker-compose.proxy.yml up -d --build
```

Наружу смотрит только nginx: `WEB_PORT` отдаёт HTTP и WebSocket, `MODBUS_TCP_PORT`
проксируется stream-блоком как сырой TCP (см. `nginx/nginx.conf`). Приложение
доступно лишь из сети compose, публичных портов у него нет.

Что стоит знать про этот режим:

- HTTP-лимиты по IP работают корректно: uvicorn читает `X-Forwarded-For`
  от доверенных адресов (`FORWARDED_ALLOW_IPS`, в этом режиме `*`, потому
  что единственным источником заголовка остаётся nginx).
- Modbus-лимит по IP (`MODBUS_MAX_CONN_PER_IP`) за stream-прокси считает
  соединения от адреса nginx-контейнера, а не реального клиента: сырым TCP
  заголовок с адресом не передать. Если лимит критичен, публикуйте порт
  Modbus напрямую из сервиса `app` (вариант 2) или раздавайте клиентам
  разные адреса.
- Конфигурация nginx лежит в репозитории, изменения требуют
  `docker compose restart nginx`.

## Вариант 4: systemd (без Docker)

Сборка и установка (под root):

```bash
useradd --system --home /opt/modbus-test-server --shell /usr/sbin/nologin modbus-test
install -d -o modbus-test -g modbus-test /opt/modbus-test-server
cp -r app pyproject.toml uv.lock scripts /opt/modbus-test-server/
sudo -u modbus-test uv sync --frozen --no-dev --directory /opt/modbus-test-server
install -d /etc/modbus-test-server
cp .env.example /etc/modbus-test-server/env   # и поправьте под окружение
cp deploy/modbus-test-server.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now modbus-test-server
```

Падение процесса ведёт к перезапуску через 5 секунд (`Restart=on-failure`);
штатная остановка через `systemctl stop` перезапуска не вызывает. Логи - в journald:

```bash
journalctl -u modbus-test-server -f
```

## Переменные окружения

Все перечислены в `.env.example` с комментариями; дефолты те же, что
в CLAUDE.md (раздел «Env»). Секреты держите только в переменных окружения,
в код и репозиторий их не включайте.

## Healthcheck и мониторинг

- `GET /healthz` - живость процесса, отвечает `{"status": "ok"}`.
- `GET /metrics` - счётчики в текстовом формате Prometheus: соединения,
  запросы и ошибки нарастающим итогом и за скользящее окно, uptime.
- `scripts/healthcheck.py` - ручная проверка для cron или диагностики:

```bash
uv run python scripts/healthcheck.py --modbus-port 5020
```

Код выхода 0 - сервер жив, 1 - проверка не прошла. Docker Compose и systemd
используют собственные healthcheck-и (см. `docker-compose.yml` и юнит).

## Поведение под нагрузкой

- Лимит Modbus-записей (`MODBUS_MAX_WRITE_PER_SEC`, по умолчанию 200 на
  соединение) отвечает exception 0x06 (Device Busy) и пишет это в метрики.
- Лимит соединений с одного IP (`MODBUS_MAX_CONN_PER_IP`) и лимит записей
  из UI (`WEB_MAX_WRITE_PER_MIN_PER_IP`) - отдельные ручки.
- Нагрузочный прогон: `uv run python scripts/load_test.py --host ... --port ...`
  (опция `--embedded` поднимает тестовый сервер в том же процессе).

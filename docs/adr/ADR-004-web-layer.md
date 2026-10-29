# ADR-004: web-слой (FastAPI) — композиция сервисов и форматы REST

- **Статус:** принято
- **Дата:** 2026-10-29
- **Задача:** TASK-301…310 (Эпик 3)

## Контекст

Эпик 3 добавляет REST поверх in-process сервисов (реестр поведений, движок,
карты слейвов, журнал). CLAUDE.md задаёт префиксы (`/api/modbus/*`, `/ws/state`),
env-переменные и инварианты, но не описывает: композицию web-приложения,
формат снимков состояния, механизм rate-limit UI-записей и границу между
REST-инъекциями (Эпик 3) и их реализацией (Эпик 6). Эти развилки решаются до кода.

## Решения

1. **Пакет `app/api/`** — web-слой отдельно от `app/modbus/` (ядро):
   `app.py` — фабрика `create_app(config, services, run_engine)` + lifespan;
   `services.py` — контейнер `AppServices` (config, registry, maps, engine,
   journal, write_policy, injections); `models.py` — Pydantic-схемы;
   `routes.py` — `APIRouter` с префиксом `/api/modbus`. `GET /healthz` — вне
   префикса. Точка входа uvicorn — `app.api.app:create_app --factory`
   (run-скрипт и деплой — Эпик 9).

2. **FastAPI lifespan запускает и останавливает `BehaviorEngine`.**
   Для детерминированных тестов — флаг `run_engine=False`: тесты вызывают
   `engine.tick(now)` сами, без гонки с фоновым циклом. Event loop не блокируется
   (инвариант 6).

3. **Снимок состояния (TASK-307) — значения сетки (value-grid), не сырые регистры.**
   Формат: `{"version": 1, "slaves": {"1": {"hr": {"1": 0.0, …},
   "cl": {"1": true, …}}}}` — адреса 1,3,…,1023 для 32-битных слейвов, значения
   декодированы по формату слейва. Восстановление пишет только `hr`/`cl`:
   `ir`/`di` генерируются движком (инвариант 2), их восстановление бессмысленно.

4. **Rate-limit UI-записей (TASK-306) — скользящее окно в памяти по IP**
   (`WEB_MAX_WRITE_PER_MIN_PER_IP`) на `POST /api/modbus/write`; превышение → 429.
   IP берётся из `request.client.host` (за nginx — X-Forwarded-For, Эпик 9).
   Хранения на диск нет (инвариант 9).

5. **Маппинг ошибок:** 422 — схема Pydantic; 400 — доменные `ValueError`/`TypeError`
   (неизвестный slave, адрес вне карты, неприменимый kind); 409 — запись в
   locked-привязку (по Modbus это 0x04, ADR-003 п. 3); 429 — rate limit.

6. **Инъекции (TASK-309): REST + хранилище сейчас, enforcement — Эпик 6.**
   `app/modbus/injection.py` — реестр инъекций (exception / delay / timeout,
   ленивый TTL ≤ `INJECTION_MAX_TTL_S`); REST-эндпоинты регистрируют и снимают
   инъекции. Само влияние на Modbus-запросы реализуется в Эпике 6 поверх этого
   реестра (TASK-601…604).

7. **Сериализация — JSON поверх существующих моделей.** Поведения — `Behavior`
   (`kind`, `params`, `locked`) + `is_default` (сравнение идентичности с дефолтом,
   ADR-003 п. 5). Записи журнала — `action` ∈ {`behavior_set`, `behavior_reset`,
   `write`, `restore`, `inject`, …}, `source="web"`, `ip` клиента. Для
   `TestClient` — dev-зависимость `httpx`.

## Последствия

- WebSocket (Эпик 4) подключается к тому же `AppServices`; `WebStateBus`
  добавится в контейнер без изменения REST-слоя.
- «Полный конфиг» (TASK-301) при 11 392 привязках отдаётся с фильтрами
  `slave`, `area`, `only_overrides` — без фильтров это полный список.
- Значения регистров нигде не персистятся (инвариант 9); снимок — только JSON
  в ответе/запросе клиента.

# ISSUES — Modbus Test Server

Формат: `[P0] [ ] TASK-001 · Заголовок`
Приоритеты: **P0** — блокер релиза, **P1** — нужно к релизу, **P2** — можно после.
Каждый пункт — атомарная задача на 1–4 часа. Отмечать `[x]` после мерджа.

---

## Эпик 0 · Discovery и фундамент

- [x] [P0] **TASK-001 · Изучить проект и подтвердить стек**
  - `ls`, `pyproject.toml`, `package.json`, `Dockerfile`, `docker-compose.yml`, `nginx/`, `.github/`
  - Записать вывод в `docs/adr/ADR-000-stack.md`
  - Если стек ≠ ожидаемому — согласовать с владельцем
- [x] [P0] **TASK-002 · Создать каркас модуля `app/modbus/`**
  - `__init__.py`, `config.py` (env), `types.py` (area/slave/format)
  - Подключить `pymodbus`, `fastapi`, `uvicorn`
- [x] [P0] **TASK-003 · `docs/HRIR_SPEC.md`** — перенести спецификацию форматов в репозиторий
- [x] [P0] **TASK-004 · `.env.example`** — все переменные из CLAUDE.md (Env)
- [x] [P0] **TASK-005 · CI: pytest + ruff + mypy** (если ещё нет) — baseline зелёный

---

## Эпик 1 · Modbus-TCP сервер (ядро)

### 1.1 Слушатель и карта
- [x] [P0] **TASK-101 · Async-сервер pymodbus на :5020**
  - `ModbusServerContext(single=False)`, 10 unit ID
  - Логирование старта, graceful shutdown
- [x] [P0] **TASK-102 · Модель карты адресов**
  - Класс `SlaveMap(slave_id)` с областями `cl/di/hr/ir`
  - Диапазоны и типы из `HRIR_SPEC`
  - Валидация: чтение/запись за пределами диапазона → exception 0x02
- [x] [P0] **TASK-103 · Slave 10 — только IR**
  - Регистры 1..268 (27 зон × 8 метрик + 4 «дырки»), 300..301 timestamp
  - CL/DI/HR → exception 0x02
  - Тест: чтение часов по всем 27 зонам, граничные регистры 9/10/19/20 = 0

### 1.2 Функции
- [x] [P0] **TASK-111 · FC 1/2 — чтение CL/DI**
- [x] [P0] **TASK-112 · FC 3/4 — чтение HR/IR**
- [x] [P0] **TASK-113 · FC 5 — запись одного CL**
- [x] [P0] **TASK-114 · FC 6 — запись одного HR**
- [x] [P0] **TASK-115 · FC 16 — запись N HR, N ≤ 2, иначе 0x03**
- [x] [P0] **TASK-116 · Негативные тесты: FC 15, 17, 22, 23 → 0x01; невалидный адрес → 0x02**

### 1.3 Форматы байт/слов
- [x] [P0] **TASK-121 · Кодек uint16/uint32/float ↔ ABCD/CDAB/BADC/DCBA**
  - Двунаправленный, с тестами round-trip
- [x] [P0] **TASK-122 · Интеграционный тест эталонным клиентом**
  - `pymodbus`-клиент читает по 1 регистру с каждого slave; значения сверяются с ожидаемыми

---

## Эпик 2 · Движок поведений

### 2.1 Каркас
- [ ] [P0] **TASK-201 · Модель `Behavior` и реестр `(slave, area, address) → Behavior`**
- [ ] [P0] **TASK-202 · Тик-цикл 100 мс** (asyncio task), graceful stop
- [ ] [P0] **TASK-203 · `locked: bool`** — запись при locked → 0x04 + журнал
- [ ] [P0] **TASK-204 · try/except вокруг каждого поведения** — ошибка → `constant 0` + лог
- [ ] [P0] **TASK-205 · Дефолтные поведения**
  - HR/CL — `constant 0`, `locked=False`
  - IR slaves 1–9 — `counter step=1`, `locked=True`
  - DI slaves 1–9 — `counter_bits` (биты счётчика секунд), `locked=True`

### 2.2 Числовые поведения
- [ ] [P0] **TASK-211 · constant**
- [ ] [P0] **TASK-212 · counter** (`start, step, min, max, wrap`)
- [ ] [P0] **TASK-213 · sawtooth** (`min, max, period_s, phase`)
- [ ] [P0] **TASK-214 · triangle** (`min, max, period_s, phase`)
- [x] [P0] **TASK-215 · meander** (`min, max, period_s, duty, phase`)
- [x] [P1] **TASK-216 · sine** (`min, max, period_s, phase`)
- [x] [P1] **TASK-217 · random_uniform** (`min, max`)
- [x] [P1] **TASK-218 · random_walk** (`min, max, step, mean_reversion`)
- [x] [P1] **TASK-219 · ramp_oneshot** (`from, to, duration_s, then`)
- [x] [P1] **TASK-220 · countdown** (`start, step, min`)
- [x] [P2] **TASK-221 · step** (`values, interval_s`)
- [x] [P2] **TASK-222 · follow** (`source: "hr:15"` — копия другого регистра)

### 2.3 Дискретные поведения
- [x] [P0] **TASK-231 · constant (bool)**
- [x] [P0] **TASK-232 · toggle** (`period_s`)
- [ ] [P1] **TASK-233 · pwm** (`period_s, duty`)
- [ ] [P1] **TASK-234 · random_bit** (вероятность)
- [ ] [P0] **TASK-235 · counter_bits** (счётчик секунд → биты)
- [ ] [P2] **TASK-236 · follow_bit** (`source, bit`)

### 2.4 Валидация и приведение
- [ ] [P0] **TASK-241 · Валидатор параметров** с учётом формата регистра
  - uint16: clamp 0..65535
  - uint32: clamp 0..2^32-1
  - float: любой, кроме NaN/Inf → 0
- [ ] [P0] **TASK-242 · Тесты границ** для каждого поведения

---

## Эпик 3 · REST API управлений

- [ ] [P0] **TASK-301 · `GET /api/modbus/behaviors`** — полный конфиг
- [ ] [P0] **TASK-302 · `PUT /api/modbus/behaviors/{slave}/{area}/{address}`**
- [ ] [P0] **TASK-303 · `POST /api/modbus/behaviors/batch`**
- [ ] [P0] **TASK-304 · `POST /api/modbus/behaviors/reset`** (`all` / `{slave}`)
- [ ] [P0] **TASK-305 · `GET /api/modbus/state?slave=N`** — снимок значений области
- [ ] [P0] **TASK-306 · `POST /api/modbus/write`** — запись CL/HR из UI (с rate-limit)
- [ ] [P1] **TASK-307 · `POST /api/modbus/snapshot` / `restore`** — JSON state
- [ ] [P1] **TASK-308 · `GET /api/modbus/journal?limit=…`** — ring buffer
- [ ] [P1] **TASK-309 · `POST /api/modbus/inject` / `POST …/inject/clear`**
- [ ] [P0] **TASK-310 · `GET /healthz`**

---

## Эпик 4 · WebSocket live-состояние

- [ ] [P0] **TASK-401 · `/ws/state`** — подписка на `{slave, areas, addresses?}`
- [ ] [P0] **TASK-402 · Периодическая рассылка** (`WS_STATE_HZ`, по умолчанию 2 Гц)
- [ ] [P0] **TASK-403 · Backpressure** — медленный клиент отключается, не тормозит остальных
- [ ] [P1] **TASK-404 · Канал событий** `/ws/events` (журнал, инъекции, изменение поведений)

---

## Эпик 5 · Многопользовательскость и защита

- [ ] [P0] **TASK-501 · Лимит соединений с IP** (`MODBUS_MAX_CONN_PER_IP`)
- [ ] [P0] **TASK-502 · Rate-limit Modbus-записей** (`MODBUS_MAX_WRITE_PER_SEC`)
- [ ] [P0] **TASK-503 · Rate-limit UI-записей** (`WEB_MAX_WRITE_PER_MIN_PER_IP`)
- [ ] [P0] **TASK-504 · Плашка «публичный сервер»** в UI (обязательный текст)
- [ ] [P1] **TASK-505 · Журнал событий** (ring buffer `JOURNAL_SIZE`)
  - время, IP, источник (modbus/web), slave, area, address, action, value

---

## Эпик 6 · Инъекция ошибок

- [ ] [P1] **TASK-601 · Exception injection** по коду (0x01/0x02/0x03/0x04/0x06) на slave/диапазон
- [ ] [P1] **TASK-602 · Delay injection** (0..5000 мс)
- [ ] [P1] **TASK-603 · Timeout injection** (не отвечать N секунд)
- [ ] [P1] **TASK-604 · TTL инъекции** (`INJECTION_MAX_TTL_S`), автосброс
- [ ] [P1] **TASK-605 · Кнопка «сбросить инъекции»** в UI + REST
- [ ] [P1] **TASK-606 · Тест: каждый код возвращается как ожидается**

---

## Эпик 7 · Web UI

### 7.1 Страница «Обзор»
- [ ] [P0] **TASK-701 · Хост/порт, unit ID 1..10, таблица форматов**
- [ ] [P0] **TASK-702 · Кнопка «Проверить соединение»** (серверный опрос выбранного slave)
  - min/avg/max RTT, число успешных/ошибочных
- [ ] [P1] **TASK-703 · Счётчики** (active connections, req/s, err/s, uptime)

### 7.2 Карта регистров
- [ ] [P0] **TASK-711 · Таблица адресов по слейву** (live, WS)
- [ ] [P0] **TASK-712 · Форматирование** HEX / DEC / FLOAT / signed / unsigned — по формату слейва
- [ ] [P0] **TASK-713 · Inline-редактор CL/HR** с плашкой про перезапись
- [ ] [P1] **TASK-714 · Watch list** (localStorage, WS-подписка)
- [ ] [P1] **TASK-715 · Live-график** 60–300 с для выбранных регистров

### 7.3 Поведения
- [ ] [P0] **TASK-721 · Форма выбора slave/area/address-range → behavior → params**
- [ ] [P0] **TASK-722 · Пресеты** (счётчик, пила, треугольник, меандр, шум, синус, ступеньки, сброс)
- [ ] [P0] **TASK-723 · «Применить к диапазону» / «Сбросить»**
- [ ] [P1] **TASK-724 · Мини-график** выбранного регистра за 60 с

### 7.4 Часовые пояса (Slave 10)
- [ ] [P0] **TASK-731 · 27 карточек Z=0..26** с датой/временем/смещением
- [ ] [P1] **TASK-732 · Unix timestamp + расхождение с системным**

### 7.5 Инструменты наладчика
- [ ] [P1] **TASK-741 · Конвертер байт/слов** — все 4 порядка для uint32 и float
- [ ] [P1] **TASK-742 · Генератор конфигов** для Modbus Poll, QModMaster, CAS Scanner, pyModbus, Node-RED
- [ ] [P1] **TASK-743 · Страница «Инъекции ошибок»**
- [ ] [P2] **TASK-744 · Дифф-вью** двух снимков
- [ ] [P1] **TASK-745 · Журнал событий в UI** (последние 1000)

### 7.6 Документация
- [ ] [P0] **TASK-751 · Страница «Документация»** — спецификация форматов + ограничения + правила многопользовательскости
- [ ] [P1] **TASK-752 · Примеры кода** (Python pymodbus, Node.js modbus-serial, C# NModbus) с копированием

---

## Эпик 8 · Тесты и качество

- [ ] [P0] **TASK-801 · Unit-тесты кодеков** (uint16/uint32/float ↔ 4 порядка)
- [ ] [P0] **TASK-802 · Integration по каждому slave и каждой функции**
- [ ] [P0] **TASK-803 · Негативные сценарии** (FC 15 → 0x01, невалидный адрес → 0x02, FC16 > 2 рег → 0x03)
- [ ] [P0] **TASK-804 · Тесты всех поведений** (границы, периодичность, монотонность)
- [ ] [P0] **TASK-805 · Slave 10: все 27 зон + 4 «дырки» + 300..301**
- [ ] [P0] **TASK-806 · Конкурентность**: 50 параллельных клиентов, 30 с, без ошибок и утечек
- [ ] [P1] **TASK-807 · Тесты инъекций** (каждый код, TTL, сброс)
- [ ] [P0] **TASK-808 · mypy + ruff зелёные в CI**
- [ ] [P1] **TASK-809 · Нагрузочный мини-тест** (Locust или собственный asyncio-скрипт)

---

## Эпик 9 · Деплой и эксплуатация

- [ ] [P0] **TASK-901 · Dockerfile** (multi-stage) + `docker-compose.yml`
- [ ] [P0] **TASK-902 · nginx**: проксирование HTTP+WS, отдельный stream для Modbus-порта
- [ ] [P0] **TASK-903 · systemd / healthcheck** (restart on failure)
- [ ] [P0] **TASK-904 · Метрики `/metrics`** (Prometheus, если принято в проекте)
- [ ] [P1] **TASK-905 · Инструкция по деплою** в `docs/DEPLOY.md`
- [ ] [P1] **TASK-906 · Бэкап конфига поведений** (если включаем персистентность)
- [ ] [P2] **TASK-907 · Sentry / structured logs shipping**

---

## Эпик 10 · Документация и приёмка

- [ ] [P0] **TASK-1001 · `README.md` модуля** — что это, зачем, как пользоваться
- [ ] [P0] **TASK-1002 · Пройти по чек-листу приёмки** (см. ниже) — приложить скрин/лог
- [ ] [P1] **TASK-1003 · Записать 2–3 демо-сценария** для инженеров-наладчиков
  - «Проверить клиент на uint32 CDAB», «Проверить реакцию на timeout», «Проверить float DCBA»
- [ ] [P0] **TASK-1004 · Ревью безопасности публичного порта** (лимиты, отсутствие утечек, healthz)

---

## ✅ Чек-лист приёмки (Definition of Done)

1. [ ] TCP-порт открыт, отвечают 10 unit ID с картой из `HRIR_SPEC.md`.
2. [ ] FC 1,2,3,4,5,6,16 работают согласно спецификации; остальные → 0x01.
3. [ ] Форматы ABCD/CDAB/BADC/DCBA для uint32 и float читаются эталонными клиентами корректно.
4. [ ] Поведения §2.2 и §2.3 настраиваются из UI и применяются к диапазонам.
5. [ ] Slave 10 отдаёт корректное время по всем 27 зонам и Unix timestamp.
6. [ ] ≥50 одновременных клиентов без деградации; UI показывает live-состояние.
7. [ ] Инъекция ошибок работает и сбрасывается (в т.ч. по TTL).
8. [ ] Журнал событий, снимки/восстановление, watch list, live-график — есть.
9. [ ] Тесты зелёные (`pytest`, `ruff`, `mypy`), документация опубликована.
10. [ ] На странице явно написано про многопользовательский режим и перезапись значений.
11. [ ] `healthz` отвечает, метрики собираются (если включены).
12. [ ] Инструкция по деплою воспроизводима с нуля.

---

## Порядок работы (рекомендуемый)

1. **Неделя 1:** Эпики 0 → 1 → 2.1–2.2 (базовые поведения).
2. **Неделя 2:** Эпик 2.3–2.4 → 3 → 4 → 5.
3. **Неделя 3:** Эпик 6 → 7.1–7.4.
4. **Неделя 4:** Эпик 7.5–7.6 → 8 → 9 → 10.

Параллельно: 8 и 10 — по мере готовности задач, не в конце.

---

## Как пользоваться этим файлом в Claude Code

- Старт сессии: `claude` в корне репозитория → «прочитай CLAUDE.md и ISSUES.md,
  начни с TASK-001».
- Перед задачей: «берём TASK-NNN, план — 3 строки, потом код».
- После задачи: «отметь в ISSUES.md, сделай коммит, прогони pytest/ruff/mypy».
- Для спорных решений: «сначала ADR в docs/adr/, потом код».

#!/usr/bin/env bash
# Использование: ./create_issues.sh [REPO]
# Требует: gh CLI, авторизованный (gh auth login)
# Создаёт эпики (labels) и задачи. Повторный запуск создаёт дубликаты — не запускать дважды без очистки.
set -euo pipefail

REPO="${1:-electroman/modbustoolkit}"

# --- Labels ---
gh label create "P0" --color "b60205" --description "Блокер релиза" -R "$REPO" 2>/dev/null || true
gh label create "P1" --color "d93f0b" --description "Нужно к релизу" -R "$REPO" 2>/dev/null || true
gh label create "P2" --color "fbca04" --description "После релиза"   -R "$REPO" 2>/dev/null || true
gh label create "modbus"    --color "1d76db" -R "$REPO" 2>/dev/null || true
gh label create "backend"   --color "0e8a16" -R "$REPO" 2>/dev/null || true
gh label create "frontend"  --color "5319e7" -R "$REPO" 2>/dev/null || true
gh label create "infra"     --color "000000" -R "$REPO" 2>/dev/null || true
gh label create "tests"     --color "c2e0c6" -R "$REPO" 2>/dev/null || true
gh label create "docs"      --color "bfdadc" -R "$REPO" 2>/dev/null || true

issue() {
  local title="$1"; local body="$2"; local labels="$3"
  gh issue create -R "$REPO" --title "$title" --body "$body" --label "$labels"
}

# --- Эпик 0 ---
issue "[TASK-001] Изучить проект и подтвердить стек" \
"Прочитать проект: ls, pyproject.toml, package.json, Dockerfile, docker-compose.yml, nginx/, .github/. Записать вывод в docs/adr/ADR-000-stack.md. Если стек != ожидаемому (FastAPI+uvicorn+pymodbus asyncio) — согласовать с владельцем до начала работ." \
"P0,modbus,backend"

issue "[TASK-002] Каркас модуля app/modbus/" \
"Создать app/modbus/{__init__,config,types}.py. Env через config.py. Подключить pymodbus/fastapi/uvicorn." \
"P0,modbus,backend"

issue "[TASK-003] Перенести спецификацию форматов в docs/HRIR_SPEC.md" \
"Нормализовать спецификацию форматов HR/IR. См. приложенный файл «Поддерживаемые форматы HRIR.txt»." \
"P0,docs"

issue "[TASK-004] .env.example" \
"Все переменные из CLAUDE.md (Env). MODBUS_TCP_HOST/PORT, MAX_CONN_PER_IP, MAX_WRITE_PER_SEC, ENGINE_TICK_MS, WS_STATE_HZ, JOURNAL_SIZE, INJECTION_MAX_TTL_S и т.д." \
"P0,infra"

issue "[TASK-005] CI: pytest + ruff + mypy" \
"Настроить GitHub Actions. Baseline должен быть зелёным." \
"P0,infra,tests"

# --- Эпик 1 ---
issue "[TASK-101] Async-сервер pymodbus на :5020" \
"ModbusServerContext(single=False), 10 unit ID. Логирование старта, graceful shutdown." \
"P0,modbus,backend"

issue "[TASK-102] Модель карты адресов SlaveMap" \
"Класс SlaveMap(slave_id) с областями cl/di/hr/ir. Диапазоны и типы из HRIR_SPEC. Валидация: за пределами → exception 0x02." \
"P0,modbus,backend"

issue "[TASK-103] Slave 10 — только IR" \
"IR 1..268 (27 зон × 8 метрик + 4 дырки), 300..301 timestamp. CL/DI/HR → exception 0x02. Тест: все 27 зон, граничные регистры 9/10/19/20 = 0." \
"P0,modbus,backend"

issue "[TASK-111] FC 1/2 — чтение CL/DI" "" "P0,modbus,backend"
issue "[TASK-112] FC 3/4 — чтение HR/IR" "" "P0,modbus,backend"
issue "[TASK-113] FC 5 — запись одного CL" "" "P0,modbus,backend"
issue "[TASK-114] FC 6 — запись одного HR" "" "P0,modbus,backend"
issue "[TASK-115] FC 16 — запись N HR, N<=2, иначе 0x03" "" "P0,modbus,backend"
issue "[TASK-116] Негативные тесты FC 15,17,22,23 -> 0x01; невалидный адрес -> 0x02" "" "P0,tests,modbus"

issue "[TASK-121] Кодек uint16/uint32/float ↔ ABCD/CDAB/BADC/DCBA" \
"Двунаправленный, с round-trip тестами на всех 9 форматов. См. таблицу раскодирования в docs/HRIR_SPEC.md." \
"P0,modbus,backend"

issue "[TASK-122] Интеграционный тест эталонным клиентом" \
"pymodbus-клиент читает по одному регистру с каждого slave; значения сверяются с ожидаемыми по HRIR_SPEC." \
"P0,tests,modbus"

# --- Эпик 2 ---
issue "[TASK-201] Модель Behavior и реестр (slave, area, address) -> Behavior" "" "P0,modbus,backend"
issue "[TASK-202] Тик-цикл 100 мс (asyncio task), graceful stop" "" "P0,modbus,backend"
issue "[TASK-203] locked: bool — запись при locked -> 0x04 + журнал" "" "P0,modbus,backend"
issue "[TASK-204] try/except вокруг каждого поведения — ошибка -> constant 0 + лог" "" "P0,modbus,backend"
issue "[TASK-205] Дефолтные поведения: HR/CL constant 0; IR counter 1 Гц; DI counter_bits" "" "P0,modbus,backend"

issue "[TASK-211] Behavior: constant" "" "P0,modbus,backend"
issue "[TASK-212] Behavior: counter (start, step, min, max, wrap)" "" "P0,modbus,backend"
issue "[TASK-213] Behavior: sawtooth" "" "P0,modbus,backend"
issue "[TASK-214] Behavior: triangle" "" "P0,modbus,backend"
issue "[TASK-215] Behavior: meander (duty, phase)" "" "P0,modbus,backend"
issue "[TASK-216] Behavior: sine" "" "P1,modbus,backend"
issue "[TASK-217] Behavior: random_uniform" "" "P1,modbus,backend"
issue "[TASK-218] Behavior: random_walk (mean_reversion)" "" "P1,modbus,backend"
issue "[TASK-219] Behavior: ramp_oneshot (then)" "" "P1,modbus,backend"
issue "[TASK-220] Behavior: countdown" "" "P1,modbus,backend"
issue "[TASK-221] Behavior: step (values, interval_s)" "" "P2,modbus,backend"
issue "[TASK-222] Behavior: follow (source: hr:15)" "" "P2,modbus,backend"

issue "[TASK-231] Discrete: constant(bool)" "" "P0,modbus,backend"
issue "[TASK-232] Discrete: toggle (period_s)" "" "P0,modbus,backend"
issue "[TASK-233] Discrete: pwm (duty)" "" "P1,modbus,backend"
issue "[TASK-234] Discrete: random_bit (p)" "" "P1,modbus,backend"
issue "[TASK-235] Discrete: counter_bits (биты счётчика секунд)" "" "P0,modbus,backend"
issue "[TASK-236] Discrete: follow_bit (source, bit)" "" "P2,modbus,backend"

issue "[TASK-241] Валидатор параметров по формату регистра" \
"uint16: clamp 0..65535; uint32: clamp 0..2^32-1; float: NaN/Inf -> 0." \
"P0,modbus,backend"

issue "[TASK-242] Тесты границ для каждого поведения" "" "P0,tests,modbus"

# --- Эпик 3 ---
issue "[TASK-301] GET /api/modbus/behaviors" "" "P0,backend,modbus"
issue "[TASK-302] PUT /api/modbus/behaviors/{slave}/{area}/{address}" "" "P0,backend,modbus"
issue "[TASK-303] POST /api/modbus/behaviors/batch" "" "P0,backend,modbus"
issue "[TASK-304] POST /api/modbus/behaviors/reset (all / {slave})" "" "P0,backend,modbus"
issue "[TASK-305] GET /api/modbus/state?slave=N" "" "P0,backend,modbus"
issue "[TASK-306] POST /api/modbus/write (rate-limited)" "" "P0,backend,modbus"
issue "[TASK-307] POST /api/modbus/snapshot / restore" "" "P1,backend,modbus"
issue "[TASK-308] GET /api/modbus/journal?limit=…" "" "P1,backend,modbus"
issue "[TASK-309] POST /api/modbus/inject / …/inject/clear" "" "P1,backend,modbus"
issue "[TASK-310] GET /healthz" "" "P0,backend,infra"

# --- Эпик 4 ---
issue "[TASK-401] /ws/state — подписка на {slave, areas, addresses?}" "" "P0,backend"
issue "[TASK-402] Периодическая рассылка WS_STATE_HZ (по умолчанию 2 Гц)" "" "P0,backend"
issue "[TASK-403] Backpressure: медленный клиент отключается, не тормозит остальных" "" "P0,backend"
issue "[TASK-404] /ws/events (журнал, инъекции, изменение поведений)" "" "P1,backend"

# --- Эпик 5 ---
issue "[TASK-501] Лимит соединений с IP (MODBUS_MAX_CONN_PER_IP)" "" "P0,modbus,backend"
issue "[TASK-502] Rate-limit Modbus-записей (MODBUS_MAX_WRITE_PER_SEC)" "" "P0,modbus,backend"
issue "[TASK-503] Rate-limit UI-записей (WEB_MAX_WRITE_PER_MIN_PER_IP)" "" "P0,backend"
issue "[TASK-504] Плашка «публичный сервер» в UI" "" "P0,frontend"
issue "[TASK-505] Журнал событий (ring buffer JOURNAL_SIZE)" "" "P1,backend"

# --- Эпик 6 ---
issue "[TASK-601] Exception injection по коду (0x01..0x06) на slave/диапазон" "" "P1,modbus,backend"
issue "[TASK-602] Delay injection 0..5000 мс" "" "P1,modbus,backend"
issue "[TASK-603] Timeout injection (не отвечать N секунд)" "" "P1,modbus,backend"
issue "[TASK-604] TTL инъекции (INJECTION_MAX_TTL_S), автосброс" "" "P1,modbus,backend"
issue "[TASK-605] Кнопка «сбросить инъекции» в UI + REST" "" "P1,frontend,backend"
issue "[TASK-606] Тест: каждый код возвращается как ожидается" "" "P1,tests"

# --- Эпик 7 ---
issue "[TASK-701] Страница «Обзор»: хост/порт, unit ID, таблица форматов" "" "P0,frontend"
issue "[TASK-702] Кнопка «Проверить соединение» (min/avg/max RTT)" "" "P0,frontend,backend"
issue "[TASK-703] Счётчики: active connections, req/s, err/s, uptime" "" "P1,frontend,backend"

issue "[TASK-711] Таблица адресов по слейву (live, WS)" "" "P0,frontend"
issue "[TASK-712] Форматирование HEX/DEC/FLOAT/signed/unsigned по формату слейва" "" "P0,frontend"
issue "[TASK-713] Inline-редактор CL/HR с плашкой про перезапись" "" "P0,frontend"
issue "[TASK-714] Watch list (localStorage + WS-подписка)" "" "P1,frontend"
issue "[TASK-715] Live-график 60–300 с для выбранных регистров" "" "P1,frontend"

issue "[TASK-721] Форма slave/area/address-range -> behavior -> params" "" "P0,frontend"
issue "[TASK-722] Пресеты поведений (счётчик, пила, треугольник, меандр, шум, синус, ступеньки, сброс)" "" "P0,frontend"
issue "[TASK-723] «Применить к диапазону» / «Сбросить»" "" "P0,frontend"
issue "[TASK-724] Мини-график выбранного регистра за 60 с" "" "P1,frontend"

issue "[TASK-731] Slave 10: 27 карточек Z=0..26 с датой/временем/смещением" "" "P0,frontend"
issue "[TASK-732] Unix timestamp + расхождение с системным" "" "P1,frontend"

issue "[TASK-741] Конвертер байт/слов для всех 4 порядков (uint32, float)" "" "P1,frontend"
issue "[TASK-742] Генератор конфигов Modbus Poll/QModMaster/CAS/pyModbus/Node-RED" "" "P1,frontend"
issue "[TASK-743] Страница «Инъекции ошибок»" "" "P1,frontend"
issue "[TASK-744] Дифф-вью двух снимков" "" "P2,frontend"
issue "[TASK-745] Журнал событий в UI (последние 1000)" "" "P1,frontend"

issue "[TASK-751] Страница «Документация» (спецификация + ограничения + правила)" "" "P0,frontend,docs"
issue "[TASK-752] Примеры кода (Python pymodbus, Node.js, C# NModbus) с копированием" "" "P1,frontend,docs"

# --- Эпик 8 ---
issue "[TASK-801] Unit-тесты кодеков (uint16/uint32/float ↔ 4 порядка)" "" "P0,tests"
issue "[TASK-802] Integration по каждому slave и каждой функции" "" "P0,tests"
issue "[TASK-803] Негативные сценарии (FC15->0x01, невалидный адрес->0x02, FC16>2->0x03)" "" "P0,tests"
issue "[TASK-804] Тесты всех поведений (границы, периодичность, монотонность)" "" "P0,tests"
issue "[TASK-805] Slave 10: все 27 зон + 4 дырки + 300..301" "" "P0,tests"
issue "[TASK-806] Конкурентность: 50 клиентов × 30 с без ошибок и утечек" "" "P0,tests"
issue "[TASK-807] Тесты инъекций (каждый код, TTL, сброс)" "" "P1,tests"
issue "[TASK-808] mypy + ruff зелёные в CI" "" "P0,tests,infra"
issue "[TASK-809] Нагрузочный мини-тест (Locust/собственный)" "" "P1,tests"

# --- Эпик 9 ---
issue "[TASK-901] Dockerfile (multi-stage) + docker-compose.yml" "" "P0,infra"
issue "[TASK-902] nginx: HTTP+WS проксирование, отдельный stream для Modbus-порта" "" "P0,infra"
issue "[TASK-903] systemd / healthcheck (restart on failure)" "" "P0,infra"
issue "[TASK-904] Метрики /metrics (Prometheus)" "" "P0,infra"
issue "[TASK-905] docs/DEPLOY.md" "" "P1,docs,infra"
issue "[TASK-906] Бэкап конфига поведений (опционально)" "" "P1,infra"
issue "[TASK-907] Sentry / structured logs shipping" "" "P2,infra"

# --- Эпик 10 ---
issue "[TASK-1001] README.md модуля" "" "P0,docs"
issue "[TASK-1002] Пройти чек-лист приёмки" \
"Чек-лист в ISSUES.md (12 пунктов). Приложить скрин/лог к закрытию." \
"P0,docs"
issue "[TASK-1003] Демо-сценарии для наладчиков" \
"«Проверить клиент на uint32 CDAB», «Проверить реакцию на timeout», «Проверить float DCBA»." \
"P1,docs"
issue "[TASK-1004] Ревью безопасности публичного порта" "" "P0,infra,backend"

echo "Done. Check: gh issue list -R $REPO"

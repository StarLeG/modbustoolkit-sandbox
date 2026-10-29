# ADR-006: Enforcement лимитов публичного порта

- **Статус:** принято
- **Дата:** 2026-10-29
- **Задача:** TASK-501, TASK-502, TASK-505 (Эпик 5); база — ADR-001 п. 5

## Контекст

ADR-001 п. 5 выбирает **лимиты вместо аутентификации**: `MODBUS_MAX_CONN_PER_IP`
(одновременные соединения), `MODBUS_MAX_WRITE_PER_SEC` (записи), ring buffer
журнала — «для разбора инцидентов». В pymodbus 3.16 нет хуков уровня accept:
`trace_connect(bool)` не передаёт адрес пира, `trace_pdu` не знает соединение,
контекст (`async_setValues`) не знает, откуда пришёл запрос. Нужно решить:
где считать соединения, как отказывать, чем отвечать на превышение лимита
записей и как атрибутировать modbus-события журнала по IP.

## Решения

1. **Точки enforcement** — подклассы pymodbus: `LimitedTcpServer(ModbusTcpServer)`
   переопределяет `callback_new_connection()` → `GuardedRequestHandler(
   ServerRequestHandler)`. Учёт соединений живёт в `callback_connected` /
   `callback_disconnected` хендлера, адрес пира — `transport.get_extra_info(
   "peername")`. Хуки ванильного pymodbus при этом продолжают работать
   (`trace_pdu` — гейт FC, ADR-002).

2. **TASK-501 · лимит соединений по IP.** `ConnectionLimiter` (in-memory
   счётчики, `app/modbus/limits.py`) держит не более `MODBUS_MAX_CONN_PER_IP`
   **одновременных** соединений с одного IP (не скорость подключений).
   Превышение → немедленный `close()` нового соединения: на этапе TCP-connect
   Modbus не имеет протокольного способа отказать, закрытие — единственный
   честный сигнал. Освобождение слота — по факту разрыва (`callback_disconnected`).

3. **TASK-502 · rate-limit Modbus-записей.** Скользящее окно `RateWindow`
   (1 с, `MODBUS_MAX_WRITE_PER_SEC`) на каждое соединение. Считаются **только
   записи** FC 5/6/16; чтения не лимитируются вообще. Превышение →
   `ExceptionResponse(fc, 0x06 Device Busy)` — семантика «занят, повтори
   позже», запись не применяется. В журнал такие отказы **не пишутся**
   (флуд исчерпал бы ring buffer и вытеснил бы инциденты) — только debug-лог.

4. **TASK-505 · IP в журнале для modbus-событий.** IP пира доставляется в
   контекст через `contextvars.ContextVar` (`_PEER_IP` в `app/modbus/server.py`):
   `handle_request` хендлера устанавливает значение на время обработки запроса,
   `SlaveMapContext.async_setValues` передаёт его в `write_policy(ip=…)`.
   Сигнатуры pymodbus при этом не меняются. Успешные Modbus-записи в журнал не
   попадают (200/с захлебнули бы буфер) — фиксируются только отказы (locked)
   и действия из UI; это сложившаяся семантика TASK-203/306, зафиксированная явно.

5. **UI-лимит** `WEB_MAX_WRITE_PER_MIN_PER_IP` уже живёт на REST-слое
   (ADR-004 п. 4, `WriteRateLimiter`); TASK-503 — доведение тестами.
   `WriteRateLimiter` переиспользует `RateWindow` (единая реализация окна).

## Последствия

- Привязка к внутренним callback'ам pymodbus 3.16 (`callback_new_connection`,
  `callback_connected`/`callback_disconnected` хендлера) — при переходе на
  4.x точки enforcement пересматриваются вместе с ADR-002.
- Отказ по лимиту соединений виден клиенту как мгновенный TCP-disconnect без
  Modbus-ответа; для наладчика это «сервер закрыл соединение» — документируется
  в UI (Эпик 7).
- 0x06 штатные клиенты трактуют как «устройство занято» — корректная
  семантика временного отказа, повтор записи имеет смысл.
- Состояние лимитов — in-memory (инвариант 9): рестарт сбрасывает счётчики.

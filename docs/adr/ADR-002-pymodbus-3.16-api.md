# ADR-002: адаптация к API pymodbus 3.16

- **Статус:** принято
- **Дата:** 2026-10-29
- **Задача:** TASK-101 (и последующие FC-задачи Эпика 1)

## Контекст

ISSUES.md (TASK-101) предписывает `ModbusServerContext(single=False)`. В установленном
**pymodbus 3.16.1** классическая схема (`ModbusSlaveContext`, `ModbusDataBlock`,
`zero_mode`, `execute()`/`doException()`) **удалена/задепрекейчена**:

- `ModbusServerContext(devices=…, single=…)` — `single` помечен deprecated;
  контекст требует либо `SimDevice`-симулятор, либо `ModbusSimulatorContext`;
- протокол контекста — `async_getValues(device_id, func_code, address, count) ->
  list[int] | list[bool] | ExcCodes` и `async_setValues(...) -> ExcCodes | None`;
- адреса запросов передаются в контекст как есть (raw PDU address);
- неизвестный FC декодер отбрасывает, сервер отвечает exception 0x01;
  **известные** FC (7, 8, 12, 15, 17, 20, 21, 22, 23, 0x2B) обрабатываются
  штатными PDU-классами — их нужно блокировать явно;
- регистрация кастомных PDU (`custom_pdu`) подменяет запись в `pdu_table`,
  но для FC 8/0x2B диспетчеризация по sub-function code смотрит в `pdu_sub_table`
  и **перетирает** подменённый класс — custom_pdu непригоден как универсальный гейт.

## Решение

1. **Контекст** — подкласс `ModbusServerContext` с собственными
   `async_getValues`/`async_setValues` поверх `SlaveMap` (`app/modbus/server.py`,
   `SlaveMapContext`); `old_simulator=True`, `simdevices=[]`, `super().__init__`
   не вызывается. Все правила карты (окна → 0x02, FC 16 ≤ 2 → 0x03) живут
   в `SlaveMap`/адаптере и тестируются без pymodbus.
2. **Гейт FC** — хук `trace_pdu` (`_guard_forbidden_fcs`): входящий PDU с FC
   вне `{1,2,3,4,5,6,16}` подменяется на `RejectedRequest`, чей
   `datastore_update` возвращает `ExceptionResponse(Illegal Function, 0x01)`
   с исходным FC. Одна точка отказа для всех FC, включая те, что не доходят
   до контекста (7, 8, 12, 17, 20, 21, 0x2B).
3. **Адресация** — 1-based passthrough: PDU address = номер регистра из
   `docs/HRIR_SPEC.md` без сдвига (решение зафиксировано при проектировании
   `SlaveMap`; конфиги клиентов — TASK-742).
4. **Неизвестный unit ID** (вне 1..10) — `NoSuchIdException` → штатный ответ
   pymodbus `Gateway Target Device Failed to Respond` (0x0B).
5. **Echo-чтение FC 5/6** — pymodbus после записи перечитывает значение тем же
   FC, поэтому маппинг чтения (`_FC_GET_AREA`) включает FC 5 → cl и FC 6 → hr.

## Последствия

- Привязка к API pymodbus 3.x (при переходе на 4.x контекст-подкласс
  пересматривается; `SlaveMap` и правила карты не зависят от pymodbus).
- ТЗ-идиома `ModbusServerContext(single=False)` не воспроизводится буквально —
  вместо неё контекст-подкласс (ближе к духу: 10 unit ID, собственные данные).
- Для неизвестных FC (8, 11, …) без штатного PDU-класса pymodbus отвечает 0x01
  с FC=0x80 (ограничение фреймера); для FC со штатной обработкой наш гейт
  возвращает корректный FC | 0x80.

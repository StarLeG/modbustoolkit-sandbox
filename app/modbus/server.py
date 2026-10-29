"""Modbus-TCP сервер: pymodbus 3.16 поверх SlaveMap (TASK-101).

Архитектура — `docs/adr/ADR-002-pymodbus-3.16-api.md`:
- контекст `SlaveMapContext` — адаптер `SlaveMap` к протоколу pymodbus
  (`async_getValues`/`async_setValues`, ExcCodes);
- хук `trace_pdu` блокирует FC вне {1, 2, 3, 4, 5, 6, 16} → Illegal Function
  (инвариант 5 CLAUDE.md), включая FC, не доезжающие до контекста (7, 8, 12, 17, …);
- адресация 1-based: PDU address = номер регистра из `docs/HRIR_SPEC.md`;
- unit ID вне 1..10 → NoSuchIdException → 0x0B (Gateway Target Device Failed).

Событийный цикл не блокируется: сервер работает на asyncio, остановка —
`ModbusTcpServer.shutdown()` (закрывает слушатель и соединения).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from pymodbus.constants import ExcCodes
from pymodbus.datastore import ModbusServerContext
from pymodbus.exceptions import NoSuchIdException
from pymodbus.framer import FramerType
from pymodbus.pdu import ExceptionResponse, ModbusPDU
from pymodbus.server import ModbusTcpServer

from app.modbus.config import Config
from app.modbus.slave_map import (
    EXC_ILLEGAL_ADDRESS,
    EXC_ILLEGAL_VALUE,
    ModbusMapError,
    SlaveMap,
)
from app.modbus.types import SLAVE_SPECS, Area

log = logging.getLogger(__name__)

#: Разрешённые Modbus-функции (инвариант 5); остальные → Illegal Function (0x01).
ALLOWED_FCS: frozenset[int] = frozenset({1, 2, 3, 4, 5, 6, 16})

#: Максимум регистров за одну запись FC 16 (инвариант 4) — иначе 0x03.
FC16_MAX_REGISTERS = 2

# FC → область для чтения. FC 5/6 включены намеренно: pymodbus после записи
# перечитывает значение тем же кодом функции (echo).
_FC_GET_AREA: dict[int, Area] = {1: "cl", 2: "di", 3: "hr", 4: "ir", 5: "cl", 6: "hr"}

# FC → область для записи (FC 5 — coil, FC 6/16 — holding register).
_FC_SET_AREA: dict[int, Area] = {5: "cl", 6: "hr", 16: "hr"}

_EXC_BY_CODE: dict[int, ExcCodes] = {
    EXC_ILLEGAL_ADDRESS: ExcCodes.ILLEGAL_ADDRESS,
    EXC_ILLEGAL_VALUE: ExcCodes.ILLEGAL_VALUE,
}


class RejectedRequest(ModbusPDU):
    """Подменённый запрос для FC вне списка разрешённых.

    `datastore_update` отвечает `ExceptionResponse(Illegal Function, 0x01)`
    с исходным function code (инвариант 5).
    """

    def __init__(self, original: ModbusPDU) -> None:
        super().__init__(transaction_id=original.transaction_id, dev_id=original.dev_id)
        self.function_code = original.function_code

    async def datastore_update(
        self, context: ModbusServerContext, device_id: int
    ) -> ModbusPDU:
        _ = context, device_id
        return ExceptionResponse(self.function_code, ExcCodes.ILLEGAL_FUNCTION)


def _guard_forbidden_fcs(sending: bool, pdu: ModbusPDU) -> ModbusPDU:
    """trace_pdu-хук: подменить запрещённые FC на `RejectedRequest`."""
    if not sending and pdu.function_code not in ALLOWED_FCS:
        return RejectedRequest(pdu)
    return pdu


class SlaveMapContext(ModbusServerContext):
    """Контекст pymodbus поверх SlaveMap: unit ID 1..10, адресация 1-based.

    `super().__init__` не вызывается: он требует `devices=` симулятора и
    печатает deprecation-предупреждение; вместо симулятора — собственные карты.
    """

    def __init__(self, maps: dict[int, SlaveMap]) -> None:
        self._maps = maps
        self._devices = {}
        self.simdevices = []
        self.old_simulator = True

    def _map(self, device_id: int) -> SlaveMap:
        try:
            return self._maps[device_id]
        except KeyError:
            raise NoSuchIdException(f"device_id {device_id} вне диапазона 1..10") from None

    def device_ids(self) -> list[int]:
        return sorted(self._maps)

    async def async_getValues(
        self, device_id: int, func_code: int, address: int, count: int = 1
    ) -> list[int] | list[bool] | ExcCodes:
        area = _FC_GET_AREA.get(func_code)
        if area is None:
            return ExcCodes.ILLEGAL_FUNCTION
        smap = self._map(device_id)
        try:
            if area in ("cl", "di"):
                return smap.get_bits(area, address, count)
            return smap.get_registers(area, address, count)
        except ModbusMapError as exc:
            return _EXC_BY_CODE.get(exc.exception_code, ExcCodes.DEVICE_FAILURE)

    async def async_setValues(
        self,
        device_id: int,
        func_code: int,
        address: int,
        values: Sequence[int] | Sequence[bool],
    ) -> ExcCodes | None:
        area = _FC_SET_AREA.get(func_code)
        if area is None:
            return ExcCodes.ILLEGAL_FUNCTION
        smap = self._map(device_id)
        try:
            if func_code == 16 and len(values) > FC16_MAX_REGISTERS:
                return ExcCodes.ILLEGAL_VALUE
            if area in ("cl", "di"):
                smap.set_bits(area, address, [bool(v) for v in values])
            else:
                smap.set_registers(area, address, [int(v) for v in values])
        except ModbusMapError as exc:
            return _EXC_BY_CODE.get(exc.exception_code, ExcCodes.DEVICE_FAILURE)
        return None


def build_context(maps: dict[int, SlaveMap] | None = None) -> SlaveMapContext:
    """Контекст сервера: по умолчанию `SlaveMap` для всех 10 unit ID."""
    if maps is None:
        maps = {slave_id: SlaveMap(slave_id) for slave_id in SLAVE_SPECS}
    return SlaveMapContext(maps)


def create_server(config: Config, context: SlaveMapContext) -> ModbusTcpServer:
    """Создать Modbus-TCP сервер (хост/порт из `Config`, ещё не слушает)."""
    return ModbusTcpServer(
        context,
        framer=FramerType.SOCKET,
        address=(config.MODBUS_TCP_HOST, config.MODBUS_TCP_PORT),
        trace_pdu=_guard_forbidden_fcs,
    )


@asynccontextmanager
async def serve(
    config: Config, context: SlaveMapContext | None = None
) -> AsyncIterator[ModbusTcpServer]:
    """Запустить сервер на время блока, затем graceful shutdown."""
    server = create_server(config, context if context is not None else build_context())
    await server.serve_forever(background=True)
    log.info(
        "modbus-tcp listening on %s:%s",
        config.MODBUS_TCP_HOST,
        config.MODBUS_TCP_PORT,
    )
    try:
        yield server
    finally:
        await server.shutdown()  # type: ignore[no-untyped-call]  # pymodbus без аннотаций
        log.info("modbus-tcp stopped (graceful shutdown)")


async def run_server(config: Config, context: SlaveMapContext | None = None) -> None:
    """Запустить сервер и работать до отмены задачи (graceful shutdown)."""
    async with serve(config, context):
        await asyncio.Event().wait()

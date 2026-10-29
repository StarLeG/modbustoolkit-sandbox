"""Modbus-TCP сервер: pymodbus 3.16 поверх SlaveMap (TASK-101).

Архитектура — `docs/adr/ADR-002-pymodbus-3.16-api.md`:
- контекст `SlaveMapContext` — адаптер `SlaveMap` к протоколу pymodbus
  (`async_getValues`/`async_setValues`, ExcCodes);
- хук `trace_pdu` блокирует FC вне {1, 2, 3, 4, 5, 6, 16} → Illegal Function
  (инвариант 5 CLAUDE.md), включая FC, не доезжающие до контекста (7, 8, 12, 17, …);
- `GuardedRequestHandler.handle_request` — лимиты публичного порта (ADR-006) и
  инъекции ошибок (ADR-007): exception → ответ кодом, delay → пауза, timeout → молчание;
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
from contextvars import ContextVar
from typing import Any

from pymodbus.constants import ExcCodes
from pymodbus.datastore import ModbusServerContext
from pymodbus.exceptions import NoSuchIdException
from pymodbus.framer import FramerType
from pymodbus.pdu import ExceptionResponse, ModbusPDU
from pymodbus.server import ModbusTcpServer
from pymodbus.server.requesthandler import ServerRequestHandler

from app.modbus.behavior import WritePolicy
from app.modbus.config import Config
from app.modbus.injection import Injection, InjectionRegistry, find_injection
from app.modbus.limits import ConnectionLimiter, RateWindow
from app.modbus.metrics import ServerMetrics
from app.modbus.slave_map import (
    EXC_DEVICE_FAILURE,
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
    EXC_DEVICE_FAILURE: ExcCodes.DEVICE_FAILURE,
}

#: IP текущего Modbus-клиента для журнала (TASK-505, ADR-006 п. 4):
#: устанавливается на время обработки запроса, вне запроса — None.
_PEER_IP: ContextVar[str | None] = ContextVar("modbus_peer_ip", default=None)


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

    def __init__(
        self, maps: dict[int, SlaveMap], write_policy: WritePolicy | None = None
    ) -> None:
        self._maps = maps
        self._write_policy = write_policy
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
            if self._write_policy is not None:
                self._write_policy(device_id, area, address, values, ip=_PEER_IP.get())
            if area in ("cl", "di"):
                smap.set_bits(area, address, [bool(v) for v in values])
            else:
                smap.set_registers(area, address, [int(v) for v in values])
        except ModbusMapError as exc:
            return _EXC_BY_CODE.get(exc.exception_code, ExcCodes.DEVICE_FAILURE)
        return None


def build_context(
    maps: dict[int, SlaveMap] | None = None,
    write_policy: WritePolicy | None = None,
) -> SlaveMapContext:
    """Контекст сервера: по умолчанию `SlaveMap` для всех 10 unit ID."""
    if maps is None:
        maps = {slave_id: SlaveMap(slave_id) for slave_id in SLAVE_SPECS}
    return SlaveMapContext(maps, write_policy)


class GuardedRequestHandler(ServerRequestHandler):
    """Хендлер соединения: лимиты и инъекции публичного порта (ADR-006/007).

    Слот `ConnectionLimiter` занимается в `callback_connected` (адрес пира —
    `transport.get_extra_info("peername")`) и освобождается в
    `callback_disconnected`. Превышение `MODBUS_MAX_CONN_PER_IP` → немедленный
    close нового соединения (ADR-006 п. 2). `handle_request` лимитирует записи
    FC 5/6/16 окном `MODBUS_MAX_WRITE_PER_SEC` (ADR-006 п. 3) и применяет
    инъекции ошибок из `self.server.injections` (ADR-007).
    """

    _peer_ip: str | None = None
    #: Последний отправленный ответ — для классификации ошибок (TASK-703).
    _last_sent: ModbusPDU | None = None

    def __init__(self, owner: Any, trace_packet: Any, trace_pdu: Any, trace_connect: Any) -> None:
        super().__init__(owner, trace_packet, trace_pdu, trace_connect)  # type: ignore[no-untyped-call]
        self._write_window = RateWindow(owner.max_write_per_sec)

    def server_send(self, response: Any, addr: Any) -> None:
        self._last_sent = response
        super().server_send(response, addr)  # type: ignore[no-untyped-call]

    def callback_connected(self) -> None:
        super().callback_connected()
        self.server.metrics.connection_opened()
        peer = self.transport.get_extra_info("peername") if self.transport else None
        ip = str(peer[0]) if peer else ""
        if not self.server.conn_limiter.allow(ip):
            log.info("modbus: соединение с %s отклонено (лимит соединений на IP)", ip)
            self.close()
            return
        self._peer_ip = ip

    def callback_disconnected(self, exc: Exception | None) -> None:
        super().callback_disconnected(exc)
        self.server.metrics.connection_closed()
        if self._peer_ip is not None:
            self.server.conn_limiter.release(self._peer_ip)
            self._peer_ip = None

    def _match_injection(self, pdu: ModbusPDU | None) -> Injection | None:
        """Инъекция, задевающая запрос (ADR-007 п. 2); None — запрос штатный.

        Запрещённые FC инъекциям не подчиняются: гейт FC (ADR-002, инвариант 5)
        главнее, они и так отвечают Illegal Function.
        """
        if pdu is None or pdu.function_code not in ALLOWED_FCS:
            return None
        area = _FC_GET_AREA.get(pdu.function_code) or _FC_SET_AREA.get(pdu.function_code)
        if area is None:
            return None
        count = pdu.count or 1  # у FC 5/6 после decode count не заполнен
        return find_injection(self.server.injections.items(), pdu.dev_id, area, pdu.address, count)

    async def handle_request(self) -> None:
        """Обработка запроса: rate-limit, инъекции, ip для журнала (ADR-006/007).

        Превышение `MODBUS_MAX_WRITE_PER_SEC` → 0x06 (Device Busy), запись не
        применяется. В журнал такие отказы не пишутся — флуд исчерпал бы ring
        buffer (ADR-006 п. 3), фиксируются только debug-логом; инъекции так же
        не журналируются (ADR-007 п. 7). На время запроса в `_PEER_IP` кладётся
        IP пира — для атрибуции modbus-событий журнала. Каждый запрос учитывается
        в `server.metrics` (TASK-703): ошибкой считаются ExceptionResponse
        (включая 0x06 и инъекции exception) и поглощённые timeout-инъекцией.
        """
        pdu = self.last_pdu
        if (
            pdu is not None
            and pdu.function_code in _FC_SET_AREA
            and not self._write_window.allow()
        ):
            log.debug("modbus: превышен лимит записей с %s", self._peer_ip)
            response = ExceptionResponse(pdu.function_code, ExcCodes.DEVICE_BUSY)
            response.transaction_id = pdu.transaction_id
            response.dev_id = pdu.dev_id
            self.server.metrics.request(error=True)
            self.server_send(response, self.last_addr)
            return
        injection = self._match_injection(pdu)
        if pdu is not None and injection is not None:
            if injection.kind == "exception":
                log.debug(
                    "modbus: инъекция #%s — ответ 0x%02x вместо обработки (slave %s)",
                    injection.id,
                    injection.code or 0,
                    injection.slave,
                )
                assert injection.code is not None  # для exception code обязателен (ADR-007 п. 3)
                response = ExceptionResponse(pdu.function_code, injection.code)
                response.transaction_id = pdu.transaction_id
                response.dev_id = pdu.dev_id
                self.server.metrics.request(error=True)
                self.server_send(response, self.last_addr)
                return
            if injection.kind == "delay":
                log.debug(
                    "modbus: инъекция #%s — задержка %s мс (slave %s)",
                    injection.id,
                    injection.delay_ms,
                    injection.slave,
                )
                await asyncio.sleep((injection.delay_ms or 0) / 1000.0)  # не блокирует loop
            if injection.kind == "timeout":
                log.debug(
                    "modbus: инъекция #%s — молчание до истечения TTL (slave %s)",
                    injection.id,
                    injection.slave,
                )
                self.server.metrics.request(error=True)
                return  # запрос поглощается: без ответа и без обработки (ADR-007 п. 5)
        self._last_sent = None
        token = _PEER_IP.set(self._peer_ip)
        try:
            await super().handle_request()  # type: ignore[no-untyped-call]
        finally:
            _PEER_IP.reset(token)
        self.server.metrics.request(error=isinstance(self._last_sent, ExceptionResponse))


class LimitedTcpServer(ModbusTcpServer):
    """TCP-сервер с лимитами, инъекциями и счётчиками (ADR-006/007, TASK-703)."""

    def __init__(
        self,
        *args: Any,
        max_conn_per_ip: int = 20,
        max_write_per_sec: int = 200,
        injections: InjectionRegistry | None = None,
        metrics: ServerMetrics | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.conn_limiter = ConnectionLimiter(max_conn_per_ip)
        self.max_write_per_sec = max_write_per_sec
        self.injections = injections if injections is not None else InjectionRegistry()
        self.metrics = metrics if metrics is not None else ServerMetrics()

    def callback_new_connection(self) -> GuardedRequestHandler:
        return GuardedRequestHandler(self, self.trace_packet, self.trace_pdu, self.trace_connect)


def create_server(
    config: Config,
    context: SlaveMapContext,
    injections: InjectionRegistry | None = None,
    metrics: ServerMetrics | None = None,
) -> ModbusTcpServer:
    """Создать Modbus-TCP сервер (хост/порт из `Config`, ещё не слушает).

    `injections` — реестр инъекций ошибок (ADR-007); без него — пустой реестр
    с TTL из `INJECTION_MAX_TTL_S` (инъекции не действуют). `metrics` — счётчики
    сервера (TASK-703); без них — собственные (web-слой их не увидит).
    """
    return LimitedTcpServer(
        context,
        framer=FramerType.SOCKET,
        address=(config.MODBUS_TCP_HOST, config.MODBUS_TCP_PORT),
        trace_pdu=_guard_forbidden_fcs,
        max_conn_per_ip=config.MODBUS_MAX_CONN_PER_IP,
        max_write_per_sec=config.MODBUS_MAX_WRITE_PER_SEC,
        injections=injections
        if injections is not None
        else InjectionRegistry(max_ttl_s=config.INJECTION_MAX_TTL_S),
        metrics=metrics,
    )


@asynccontextmanager
async def serve(
    config: Config,
    context: SlaveMapContext | None = None,
    injections: InjectionRegistry | None = None,
    metrics: ServerMetrics | None = None,
) -> AsyncIterator[ModbusTcpServer]:
    """Запустить сервер на время блока, затем graceful shutdown."""
    server = create_server(
        config,
        context if context is not None else build_context(),
        injections,
        metrics,
    )
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


async def run_server(
    config: Config,
    context: SlaveMapContext | None = None,
    injections: InjectionRegistry | None = None,
    metrics: ServerMetrics | None = None,
) -> None:
    """Запустить сервер и работать до отмены задачи (graceful shutdown)."""
    async with serve(config, context, injections, metrics):
        await asyncio.Event().wait()

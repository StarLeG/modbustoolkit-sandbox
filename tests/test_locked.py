"""TASK-203: locked-поведения — внешняя запись отклоняется (0x04) + журнал.

ADR-003 п. 3: политика проверяет окно записи до применения; окно, задевающее
locked-привязку, отклоняется целиком. Движок поведений пишет мимо политики.
"""
from __future__ import annotations

import pytest
from pymodbus.client import AsyncModbusTcpClient
from pymodbus.constants import ExcCodes

from app.modbus.behavior import (
    Behavior,
    BehaviorRegistry,
    make_write_policy,
    touched_bindings,
)
from app.modbus.config import Config
from app.modbus.engine import BehaviorEngine
from app.modbus.journal import Journal, JournalEntry
from app.modbus.server import SlaveMapContext, serve
from app.modbus.slave_map import EXC_DEVICE_FAILURE, ModbusMapError, SlaveMap
from app.modbus.types import SLAVE_SPECS, Area


def _setup(slave: int, area: Area, address: int, locked: bool = True):
    registry = BehaviorRegistry()
    kind = "constant" if area in ("cl", "di") else "counter"
    registry.set(slave, area, address, Behavior(kind, locked=locked))
    journal = Journal()
    policy = make_write_policy(registry, journal)
    maps = {slave_id: SlaveMap(slave_id) for slave_id in SLAVE_SPECS}
    return registry, journal, policy, maps


# --- журнал ---------------------------------------------------------------------------


def test_journal_ring_buffer_keeps_last() -> None:
    journal = Journal(size=3)
    for i in range(5):
        journal.add(JournalEntry(action="a", slave=1, area="hr", address=i, value=i))
    assert len(journal) == 3
    assert [e.address for e in journal.entries()] == [2, 3, 4]


def test_journal_entries_limit() -> None:
    journal = Journal(size=10)
    for i in range(4):
        journal.add(JournalEntry(action="a", slave=1, area="hr", address=i))
    assert [e.address for e in journal.entries(limit=2)] == [2, 3]
    assert journal.entries(limit=0) == []
    with pytest.raises(ValueError, match="limit"):
        journal.entries(limit=-1)


def test_journal_bad_size() -> None:
    with pytest.raises(ValueError, match="размер журнала"):
        Journal(size=0)


# --- политика записи -------------------------------------------------------------------


def test_policy_allows_unlocked() -> None:
    _registry, _journal, policy, _maps = _setup(1, "hr", 5, locked=False)
    policy(1, "hr", 5, [0x1234])  # не выбрасывает


def test_policy_rejects_locked_and_journals() -> None:
    _registry, journal, policy, _maps = _setup(1, "hr", 5)
    with pytest.raises(ModbusMapError) as excinfo:
        policy(1, "hr", 5, [0x1234])
    assert excinfo.value.exception_code == EXC_DEVICE_FAILURE
    entry = journal.entries()[0]
    assert entry.action == "write_rejected_locked"
    assert (entry.slave, entry.area, entry.address, entry.value) == (1, "hr", 5, [0x1234])
    assert entry.source == "modbus"
    assert entry.ip is None


def test_policy_rejects_window_touching_locked() -> None:
    _registry, _journal, policy, _maps = _setup(1, "hr", 5)
    with pytest.raises(ModbusMapError):
        policy(1, "hr", 4, [1, 2, 3])  # окно 4..6 задевает locked-адрес 5


def test_policy_32bit_raw_window_touches_value() -> None:
    """Запись в регистр N+1 задевает значение по адресу N (слейвы 2..9)."""
    _registry, _journal, policy, _maps = _setup(2, "hr", 1)
    with pytest.raises(ModbusMapError):
        policy(2, "hr", 1, [0x1234, 0x5678])  # raw 1..2 → value 1
    with pytest.raises(ModbusMapError):
        policy(2, "hr", 2, [0x1234, 0x5678])  # raw 2..3 → value 1 и 3
    policy(2, "hr", 3, [0x1234, 0x5678])  # raw 3..4 → только value 3 — можно


def test_policy_bit_area() -> None:
    _registry, _journal, policy, _maps = _setup(1, "cl", 3)
    with pytest.raises(ModbusMapError):
        policy(1, "cl", 3, [True])
    policy(1, "cl", 4, [True])  # соседний бит не заблокирован


def test_policy_out_of_map_is_silent() -> None:
    """Окна вне карты пропускает политика — их отклонит карта (0x02)."""
    _registry, _journal, policy, _maps = _setup(1, "hr", 5)
    policy(1, "hr", 1025, [1])
    policy(11, "hr", 1, [1])  # неизвестный slave — нет привязок


def test_touched_bindings_32bit() -> None:
    assert touched_bindings(1, "hr", 5, 2) == [(1, "hr", 5), (1, "hr", 6)]
    assert touched_bindings(2, "hr", 2, 2) == [(2, "hr", 1), (2, "hr", 3)]
    assert touched_bindings(3, "di", 7, 1) == [(3, "di", 7)]


# --- контекст сервера и клиент ---------------------------------------------------------


async def test_context_set_values_locked_returns_device_failure() -> None:
    _registry, _journal, policy, maps = _setup(1, "hr", 5)
    ctx = SlaveMapContext(maps, policy)
    result = await ctx.async_setValues(1, 6, 5, [1])
    assert result == ExcCodes.DEVICE_FAILURE
    assert await ctx.async_setValues(1, 6, 6, [1]) is None  # незаблокированный — можно


async def test_client_write_to_locked_register_gets_0x04(config: Config) -> None:
    _registry, journal, policy, maps = _setup(1, "hr", 5)
    async with serve(config, SlaveMapContext(maps, policy)):
        client = AsyncModbusTcpClient("127.0.0.1", port=config.MODBUS_TCP_PORT)
        assert await client.connect()
        response = await client.write_register(5, 0x1234, device_id=1)
        assert response.isError()
        assert response.exception_code == 0x04
        ok = await client.write_register(6, 0x1234, device_id=1)
        assert not ok.isError()
        client.close()
    assert len(journal) == 1
    assert journal.entries()[0].ip == "127.0.0.1"  # ip пира модbus-соединения (TASK-505)


async def test_client_write_to_locked_coil_gets_0x04(config: Config) -> None:
    registry = BehaviorRegistry()
    registry.set(1, "cl", 3, Behavior("constant", {"value": 0}, locked=True))
    policy = make_write_policy(registry)
    maps = {slave_id: SlaveMap(slave_id) for slave_id in SLAVE_SPECS}
    async with serve(config, SlaveMapContext(maps, policy)):
        client = AsyncModbusTcpClient("127.0.0.1", port=config.MODBUS_TCP_PORT)
        assert await client.connect()
        response = await client.write_coil(3, True, device_id=1)
        assert response.isError()
        assert response.exception_code == 0x04
        client.close()


async def test_engine_writes_bypass_policy() -> None:
    """Движок пишет в locked-привязку напрямую (ADR-003 п. 3)."""
    registry, _journal, policy, maps = _setup(1, "hr", 5, locked=True)
    registry.set(1, "hr", 5, Behavior("constant", {"value": 42}, locked=True))
    with pytest.raises(ModbusMapError):
        policy(1, "hr", 5, [1])  # внешняя запись блокируется
    engine = BehaviorEngine(registry, maps)
    engine.tick(now=0.0)  # движок пишет без политики
    assert maps[1].get_registers("hr", 5, 1) == [42]

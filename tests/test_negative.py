"""TASK-116: негативные сценарии — запрещённые FC → 0x01, адреса вне карты → 0x02.

Инвариант 5 (CLAUDE.md): любые FC, кроме 1, 2, 3, 4, 5, 6, 16, отвечают
Illegal Function (0x01). Адрес/окно вне карты слейва — Illegal Data Address (0x02).
"""
from __future__ import annotations

from collections.abc import Awaitable

from pymodbus.client import AsyncModbusTcpClient

ILLEGAL_FUNCTION = 0x01
ILLEGAL_ADDRESS = 0x02


async def _assert_exc(
    response: Awaitable[object],
    code: int,
) -> None:
    result = await response
    assert result.isError()  # type: ignore[attr-defined]
    assert result.exception_code == code  # type: ignore[attr-defined]


# --- TASK-116: запрещённые FC → 0x01 ---------------------------------------------------


async def test_fc15_write_multiple_coils_rejected(client: AsyncModbusTcpClient) -> None:
    await _assert_exc(client.write_coils(1, [True, False, True], device_id=1), ILLEGAL_FUNCTION)


async def test_fc17_report_device_id_rejected(client: AsyncModbusTcpClient) -> None:
    await _assert_exc(client.report_device_id(device_id=1), ILLEGAL_FUNCTION)


async def test_fc22_mask_write_rejected(client: AsyncModbusTcpClient) -> None:
    await _assert_exc(
        client.mask_write_register(address=1, and_mask=0xFF00, or_mask=0x00FF, device_id=1),
        ILLEGAL_FUNCTION,
    )


async def test_fc23_readwrite_registers_rejected(client: AsyncModbusTcpClient) -> None:
    await _assert_exc(
        client.readwrite_registers(
            read_address=1, read_count=1, write_address=2, values=[0x1234], device_id=1
        ),
        ILLEGAL_FUNCTION,
    )


async def test_fc7_read_exception_status_rejected(client: AsyncModbusTcpClient) -> None:
    await _assert_exc(client.read_exception_status(device_id=1), ILLEGAL_FUNCTION)


async def test_fc8_diagnostics_rejected(client: AsyncModbusTcpClient) -> None:
    await _assert_exc(client.diag_query_data(b"\x00\x00", device_id=1), ILLEGAL_FUNCTION)


async def test_fc2b_device_information_rejected(client: AsyncModbusTcpClient) -> None:
    await _assert_exc(client.read_device_information(device_id=1), ILLEGAL_FUNCTION)


async def test_forbidden_fcs_rejected_on_all_slaves(client: AsyncModbusTcpClient) -> None:
    for device_id in range(1, 11):
        await _assert_exc(client.report_device_id(device_id=device_id), ILLEGAL_FUNCTION)


# --- TASK-116: невалидные адреса → 0x02 -----------------------------------------------


async def test_read_register_address_out_of_map(client: AsyncModbusTcpClient) -> None:
    await _assert_exc(client.read_holding_registers(0, count=1, device_id=1), ILLEGAL_ADDRESS)
    await _assert_exc(client.read_holding_registers(1025, count=1, device_id=1), ILLEGAL_ADDRESS)
    await _assert_exc(client.read_input_registers(0, count=1, device_id=2), ILLEGAL_ADDRESS)


async def test_read_window_crosses_map_end(client: AsyncModbusTcpClient) -> None:
    await _assert_exc(client.read_holding_registers(1024, count=2, device_id=1), ILLEGAL_ADDRESS)
    await _assert_exc(client.read_holding_registers(1024, count=2, device_id=3), ILLEGAL_ADDRESS)


async def test_write_address_out_of_map(client: AsyncModbusTcpClient) -> None:
    await _assert_exc(client.write_register(0, 1, device_id=1), ILLEGAL_ADDRESS)
    await _assert_exc(client.write_coil(65, True, device_id=1), ILLEGAL_ADDRESS)
    await _assert_exc(client.write_registers(1024, [1, 2], device_id=4), ILLEGAL_ADDRESS)


async def test_slave10_gap_and_end(client: AsyncModbusTcpClient) -> None:
    await _assert_exc(client.read_input_registers(269, count=1, device_id=10), ILLEGAL_ADDRESS)
    await _assert_exc(client.read_input_registers(260, count=12, device_id=10), ILLEGAL_ADDRESS)
    await _assert_exc(client.read_input_registers(302, count=1, device_id=10), ILLEGAL_ADDRESS)


async def test_bit_address_out_of_map(client: AsyncModbusTcpClient) -> None:
    await _assert_exc(client.read_coils(65, count=1, device_id=5), ILLEGAL_ADDRESS)
    await _assert_exc(client.read_discrete_inputs(0, count=1, device_id=5), ILLEGAL_ADDRESS)

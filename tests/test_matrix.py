"""TASK-802: матрица «каждый slave × каждая функция» эталонным клиентом.

Для каждого unit ID 1..10 и каждой разрешённой FC 1, 2, 3, 4, 5, 6, 16
проверяется ожидаемый исход: slaves 1..9 принимают все FC, slave 10 —
только FC 4 (только IR), отсутствующие области → Illegal Data Address
(0x02). Запрещённая FC (15) на любом slave → Illegal Function (0x01).
Побочные эффекты записей снимаются свежим сервером фикстуры.
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from pymodbus.client import AsyncModbusTcpClient

ILLEGAL_FUNCTION = 0x01
ILLEGAL_ADDRESS = 0x02

SUPPORTED_FC = (1, 2, 3, 4, 5, 6, 16)


async def _call(client: AsyncModbusTcpClient, fc: int, device_id: int) -> object:
    """Один запрос выбранной FC на slave (адрес 1, минимальный размер)."""
    if fc == 1:
        return await client.read_coils(1, count=1, device_id=device_id)
    if fc == 2:
        return await client.read_discrete_inputs(1, count=1, device_id=device_id)
    if fc == 3:
        return await client.read_holding_registers(1, count=1, device_id=device_id)
    if fc == 4:
        return await client.read_input_registers(1, count=1, device_id=device_id)
    if fc == 5:
        return await client.write_coil(1, True, device_id=device_id)
    if fc == 6:
        return await client.write_register(1, 0x1234, device_id=device_id)
    return await client.write_registers(1, [0x1111, 0x2222], device_id=device_id)


def _matrix_cases() -> Iterator[pytest.ParameterSet]:
    for device_id in range(1, 11):
        for fc in SUPPORTED_FC:
            expect_ok = device_id < 10 or fc == 4  # slave 10 — только IR
            yield pytest.param(device_id, fc, expect_ok, id=f"s{device_id}-fc{fc}")


@pytest.mark.parametrize(("device_id", "fc", "expect_ok"), list(_matrix_cases()))
async def test_supported_fc_matrix(
    client: AsyncModbusTcpClient,
    device_id: int,
    fc: int,
    expect_ok: bool,
) -> None:
    response = await _call(client, fc, device_id)
    if expect_ok:
        assert not response.isError(), f"slave {device_id} fc {fc}: {response}"  # type: ignore[attr-defined]
    else:
        assert response.isError(), f"slave {device_id} fc {fc}"  # type: ignore[attr-defined]
        assert response.exception_code == ILLEGAL_ADDRESS  # type: ignore[attr-defined]


@pytest.mark.parametrize("device_id", range(1, 11))
async def test_forbidden_fc15_on_each_slave(client: AsyncModbusTcpClient, device_id: int) -> None:
    response = await client.write_coils(1, [True], device_id=device_id)  # FC 15
    assert response.isError()
    assert response.exception_code == ILLEGAL_FUNCTION

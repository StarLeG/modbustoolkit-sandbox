"""Эпик 1: функции Modbus FC 1/2/3/4/5/6/16 (TASK-111…115).

Каждый FC проверяется эталонным клиентом `AsyncModbusTcpClient` поверх
реального TCP-соединения. Значения для чтения предзаписываются в карты
(`client_with_maps`) или пишутся самим клиентом.

Биты на проводе упакованы по 8 на байт, поэтому `response.bits` содержит
8 * ceil(count/8) значений — сверяются первые `count` бит (срезом).
"""
from __future__ import annotations

from pymodbus.client import AsyncModbusTcpClient

from app.modbus.slave_map import SlaveMap

# --- TASK-111: FC 1/2 — чтение CL/DI --------------------------------------------------


async def test_fc1_read_coils(
    client_with_maps: tuple[AsyncModbusTcpClient, dict[int, SlaveMap]],
) -> None:
    client, maps = client_with_maps
    maps[1].set_bits("cl", 1, [True, False, True])
    response = await client.read_coils(1, count=3, device_id=1)
    assert not response.isError()
    assert response.bits[:3] == [True, False, True]


async def test_fc2_read_discrete_inputs(
    client_with_maps: tuple[AsyncModbusTcpClient, dict[int, SlaveMap]],
) -> None:
    client, maps = client_with_maps
    maps[2].set_bits("di", 10, [True, True, False])
    response = await client.read_discrete_inputs(10, count=3, device_id=2)
    assert not response.isError()
    assert response.bits[:3] == [True, True, False]


async def test_fc1_fc2_on_slaves_1_to_9(
    client_with_maps: tuple[AsyncModbusTcpClient, dict[int, SlaveMap]],
) -> None:
    client, maps = client_with_maps
    for device_id in range(1, 10):
        maps[device_id].set_bits("cl", 64, [True])
        maps[device_id].set_bits("di", 1, [True])
        coils = await client.read_coils(64, count=1, device_id=device_id)
        assert coils.bits[:1] == [True], f"slave {device_id}"
        discrete = await client.read_discrete_inputs(1, count=1, device_id=device_id)
        assert discrete.bits[:1] == [True], f"slave {device_id}"


async def test_fc1_fc2_slave10_absent(client: AsyncModbusTcpClient) -> None:
    for reader in (client.read_coils, client.read_discrete_inputs):
        response = await reader(1, count=1, device_id=10)
        assert response.isError()
        assert response.exception_code == 0x02

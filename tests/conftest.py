"""Общие фикстуры интеграционных тестов (клиент ↔ сервер)."""
from __future__ import annotations

import socket
from collections.abc import AsyncIterator
from dataclasses import replace

import pytest
from pymodbus.client import AsyncModbusTcpClient

from app.modbus.config import Config
from app.modbus.server import SlaveMapContext, serve
from app.modbus.slave_map import SlaveMap
from app.modbus.types import SLAVE_SPECS

DeviceMaps = dict[int, SlaveMap]


def free_port() -> int:
    """Свободный TCP-порт на loopback."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def config() -> Config:
    return replace(Config(), MODBUS_TCP_HOST="127.0.0.1", MODBUS_TCP_PORT=free_port())


@pytest.fixture
async def client(config: Config) -> AsyncIterator[AsyncModbusTcpClient]:
    """Подключённый клиент к серверу с картами по умолчанию."""
    async with serve(config):
        tcp_client = AsyncModbusTcpClient("127.0.0.1", port=config.MODBUS_TCP_PORT)
        assert await tcp_client.connect()
        yield tcp_client
        tcp_client.close()


@pytest.fixture
async def client_with_maps(
    config: Config,
) -> AsyncIterator[tuple[AsyncModbusTcpClient, DeviceMaps]]:
    """Клиент + карты слейвов для предзаписи значений в обход Modbus."""
    maps: DeviceMaps = {slave_id: SlaveMap(slave_id) for slave_id in SLAVE_SPECS}
    async with serve(config, SlaveMapContext(maps)):
        tcp_client = AsyncModbusTcpClient("127.0.0.1", port=config.MODBUS_TCP_PORT)
        assert await tcp_client.connect()
        yield tcp_client, maps
        tcp_client.close()

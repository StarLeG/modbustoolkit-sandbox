"""TASK-501: лимит одновременных соединений по IP (ADR-006 п. 2).

Юнит — `ConnectionLimiter`; интеграция — третий TCP-клиент с одного IP
получает немедленный close, слот освобождается при разрыве.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from app.modbus.config import Config
from app.modbus.limits import ConnectionLimiter
from app.modbus.server import serve

# --- ConnectionLimiter (unit) ------------------------------------------------------------


def test_conn_limiter_allows_up_to_max() -> None:
    limiter = ConnectionLimiter(2)
    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("1.2.3.4") is False  # лимит исчерпан
    assert limiter.count("1.2.3.4") == 2


def test_conn_limiter_release_frees_slot() -> None:
    limiter = ConnectionLimiter(1)
    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("1.2.3.4") is False
    limiter.release("1.2.3.4")
    assert limiter.allow("1.2.3.4") is True  # слот освободился


def test_conn_limiter_release_unknown_ip_is_noop() -> None:
    limiter = ConnectionLimiter(1)
    limiter.release("9.9.9.9")  # не должно падать
    assert len(limiter) == 0
    assert limiter.count("9.9.9.9") == 0


def test_conn_limiter_ips_are_independent() -> None:
    limiter = ConnectionLimiter(1)
    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("5.6.7.8") is True  # другой IP — свой лимит
    assert limiter.allow("1.2.3.4") is False
    assert len(limiter) == 2


def test_conn_limiter_rejects_bad_max() -> None:
    with pytest.raises(ValueError, match="max_per_ip"):
        ConnectionLimiter(0)


# --- интеграция: реальный TCP -------------------------------------------------------------


async def test_conn_limit_closes_excess_connection(config: Config) -> None:
    cfg = replace(config, MODBUS_MAX_CONN_PER_IP=2)
    readers: list[asyncio.StreamReader] = []
    writers: list[asyncio.StreamWriter] = []
    async with serve(cfg):
        for _ in range(2):
            reader, writer = await asyncio.open_connection("127.0.0.1", cfg.MODBUS_TCP_PORT)
            readers.append(reader)
            writers.append(writer)
            await asyncio.sleep(0.05)  # accept обработан сервером
        # третий с того же IP — сверх лимита: сервер закрывает сразу
        r3, w3 = await asyncio.open_connection("127.0.0.1", cfg.MODBUS_TCP_PORT)
        assert await asyncio.wait_for(r3.read(), timeout=2) == b""
        # первые два живы (EOF не приходит)
        for reader in readers:
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(reader.read(1), timeout=0.2)
        # слот освобождается при разрыве — следующее соединение проходит
        writers[0].close()
        await writers[0].wait_closed()
        await asyncio.sleep(0.1)
        r4, w4 = await asyncio.open_connection("127.0.0.1", cfg.MODBUS_TCP_PORT)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(r4.read(1), timeout=0.2)
        w4.close()
        w3.close()
        for writer in writers[1:]:
            writer.close()

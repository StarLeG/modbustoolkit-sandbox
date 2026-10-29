"""TASK-501/502: лимиты публичного порта (ADR-006 п. 2–3).

Юнит — `ConnectionLimiter`/`RateWindow`; интеграция — третий TCP-клиент с
одного IP получает немедленный close; записи сверх `MODBUS_MAX_WRITE_PER_SEC`
получают 0x06 (Device Busy), чтения не лимитируются.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from pymodbus.client import AsyncModbusTcpClient

from app.modbus.config import Config
from app.modbus.limits import ConnectionLimiter, RateWindow
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


# --- RateWindow (unit, TASK-502) ----------------------------------------------------------


def test_rate_window_limits_within_window() -> None:
    now = [0.0]
    window = RateWindow(2, 1.0, clock=lambda: now[0])
    assert window.allow() is True
    assert window.allow() is True
    assert window.allow() is False  # лимит исчерпан
    now[0] = 0.5
    assert window.allow() is False  # окно ещё не прошло
    now[0] = 1.1
    assert window.allow() is True  # окно скользит


def test_rate_window_rejects_bad_params() -> None:
    with pytest.raises(ValueError, match="лимит"):
        RateWindow(0)
    with pytest.raises(ValueError, match="окно"):
        RateWindow(1, 0.0)


# --- интеграция: rate-limit записей (TASK-502) --------------------------------------------


async def test_write_rate_limit_answers_device_busy(config: Config) -> None:
    cfg = replace(config, MODBUS_MAX_WRITE_PER_SEC=2)
    async with serve(cfg):
        client = AsyncModbusTcpClient("127.0.0.1", port=cfg.MODBUS_TCP_PORT)
        assert await client.connect()
        results = [await client.write_register(1, value, device_id=1) for value in range(4)]
        read = await client.read_holding_registers(1, count=1, device_id=1)
        await asyncio.sleep(1.1)  # окно скользит
        later = await client.write_register(1, 9, device_id=1)
        client.close()
    assert not results[0].isError()  # первые две записи проходят
    assert not results[1].isError()
    assert results[2].exception_code == 0x06  # type: ignore[attr-defined]  # Device Busy
    assert results[3].exception_code == 0x06  # type: ignore[attr-defined]
    assert not read.isError()  # чтения не лимитируются
    assert not later.isError()  # после окна запись снова проходит

"""TASK-806: конкурентность — 50 параллельных клиентов, 30 с, без ошибок и утечек.

Каждый клиент непрерывно читает/пишет свой slave 1..9 и читает вычисляемые
часы slave 10. Проверки: ни одного ExceptionResponse за прогон, счётчик
операций подтверждает нагрузку, после закрытия всех клиентов
`ServerMetrics.active_connections` возвращается к нулю (слоты не утекают).
Лимит `MODBUS_MAX_CONN_PER_IP` поднят до размера пула — лимиты тестируются
отдельно в `test_limits.py`.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import replace

from pymodbus.client import AsyncModbusTcpClient

from app.modbus.config import Config
from app.modbus.metrics import ServerMetrics
from app.modbus.server import serve

CLIENTS = 50
DURATION_S = 30.0
OP_PAUSE_S = 0.05  # петля клиента: ≤ 20 итераций/с (записи ≪ лимита 200/с)


async def _worker(
    index: int, port: int, stop_at: float, errors: list[str], ops: list[int]
) -> None:
    """Один клиент: пять операций за итерацию до истечения DURATION_S."""
    client = AsyncModbusTcpClient("127.0.0.1", port=port)
    assert await client.connect(), f"client {index}: нет соединения"
    device_id = 1 + (index % 9)  # свои slave 1..9 для записи
    value = index * 0x101
    try:
        while time.monotonic() < stop_at:
            hr = await client.read_holding_registers(1, count=2, device_id=device_id)
            if hr.isError():
                errors.append(f"client {index} FC3: {hr}")
            wr = await client.write_register(1, value, device_id=device_id)
            if wr.isError():
                errors.append(f"client {index} FC6: {wr}")
            clock = await client.read_input_registers(1, count=2, device_id=10)
            if clock.isError():
                errors.append(f"client {index} FC4/10: {clock}")
            coils = await client.read_coils(1, count=8, device_id=device_id)
            if coils.isError():
                errors.append(f"client {index} FC1: {coils}")
            wc = await client.write_coil(1, bool(index % 2), device_id=device_id)
            if wc.isError():
                errors.append(f"client {index} FC5: {wc}")
            ops[index] += 1
            await asyncio.sleep(OP_PAUSE_S)
    finally:
        client.close()


async def test_fifty_clients_30_seconds_no_errors_no_leaks(config: Config) -> None:
    cfg = replace(config, MODBUS_MAX_CONN_PER_IP=CLIENTS + 10)
    metrics = ServerMetrics()
    errors: list[str] = []
    ops = [0] * CLIENTS
    async with serve(cfg, metrics=metrics):
        stop_at = time.monotonic() + DURATION_S
        await asyncio.gather(
            *(_worker(i, cfg.MODBUS_TCP_PORT, stop_at, errors, ops) for i in range(CLIENTS))
        )
        # Все клиенты закрыты: счётчик активных соединений должен вернуться к 0.
        await asyncio.sleep(0.3)
        snapshot = metrics.snapshot()
        assert snapshot.active_connections == 0, snapshot
        assert snapshot.errors_total == 0, snapshot
    assert not errors, f"{len(errors)} ошибок, первые: {errors[:5]}"
    assert sum(ops) > 1000, f"слишком мало операций: {sum(ops)}"

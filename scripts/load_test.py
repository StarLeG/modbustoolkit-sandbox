"""TASK-809: нагрузочный мини-тест Modbus-TCP сервера (собственный asyncio-скрипт).

Нагружает живой сервер N параллельными pymodbus-клиентами: каждый клиент
циклически выполняет FC 3 (чтение HR), FC 6 (запись HR) и FC 4 (чтение часов
slave 10). По итогам — ops/s и перцентили задержки ответа.

Запуск против развёрнутого сервера:
    uv run python scripts/load_test.py --host 127.0.0.1 --port 5020 \
        --clients 50 --duration 30

Самопроверка с сервером в этом же процессе (без деплоя):
    uv run python scripts/load_test.py --embedded
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymodbus.client import AsyncModbusTcpClient

from app.modbus.config import Config
from app.modbus.server import serve

OP_PAUSE_S = 0.01  # пауза между итерациями клиента


@dataclass
class LoadResult:
    """Итоги прогона: операции, ошибки и задержки по всем клиентам."""

    clients: int
    duration_s: float
    ops: int = 0
    errors: list[str] = field(default_factory=list)
    latencies_ms: list[float] = field(default_factory=list)

    @property
    def ops_per_s(self) -> float:
        return self.ops / self.duration_s if self.duration_s > 0 else 0.0

    def percentile(self, pct: float) -> float:
        if not self.latencies_ms:
            return 0.0
        ordered = sorted(self.latencies_ms)
        index = min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1)))
        return ordered[index]

    def summary(self) -> str:
        lat = self.latencies_ms
        return (
            f"клиентов: {self.clients}, длительность: {self.duration_s:.1f} с\n"
            f"операций: {self.ops} ({self.ops_per_s:.1f} ops/s), ошибок: {len(self.errors)}\n"
            f"задержка, мс: min={min(lat, default=0.0):.2f} "
            f"avg={statistics.fmean(lat) if lat else 0.0:.2f} "
            f"p95={self.percentile(95):.2f} max={max(lat, default=0.0):.2f}"
        )


async def _worker(
    index: int, host: str, port: int, stop_at: float, result: LoadResult
) -> None:
    """Один клиент: FC 3 + FC 6 + FC 4/slave 10 до истечения времени прогона."""
    client = AsyncModbusTcpClient(host, port=port)
    if not await client.connect():
        result.errors.append(f"client {index}: нет соединения")
        return
    device_id = 1 + (index % 9)
    try:
        while time.monotonic() < stop_at:
            started = time.perf_counter()
            read = await client.read_holding_registers(1, count=2, device_id=device_id)
            if read.isError():
                result.errors.append(f"client {index} FC3: {read}")
            write = await client.write_register(1, index, device_id=device_id)
            if write.isError():
                result.errors.append(f"client {index} FC6: {write}")
            clock = await client.read_input_registers(300, count=2, device_id=10)
            if clock.isError():
                result.errors.append(f"client {index} FC4/10: {clock}")
            result.latencies_ms.append((time.perf_counter() - started) * 1000)
            result.ops += 3
            await asyncio.sleep(OP_PAUSE_S)
    finally:
        client.close()


async def run_load(host: str, port: int, clients: int, duration_s: float) -> LoadResult:
    """Прогон `clients` параллельных клиентов `duration_s` секунд."""
    result = LoadResult(clients=clients, duration_s=duration_s)
    stop_at = time.monotonic() + duration_s
    await asyncio.gather(
        *(_worker(i, host, port, stop_at, result) for i in range(clients))
    )
    return result


def _free_port() -> int:
    """Свободный TCP-порт для самопроверки (--embedded)."""
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _embedded(clients: int, duration_s: float) -> LoadResult:
    """Самопроверка: сервер в этом же процессе на свободном порту."""
    config = Config(MODBUS_TCP_HOST="127.0.0.1", MODBUS_TCP_PORT=_free_port())
    async with serve(config):
        return await run_load("127.0.0.1", config.MODBUS_TCP_PORT, clients, duration_s)


def main() -> None:
    parser = argparse.ArgumentParser(description="Нагрузочный мини-тест Modbus-TCP сервера")
    parser.add_argument("--host", default="127.0.0.1", help="адрес сервера")
    parser.add_argument("--port", type=int, default=Config().MODBUS_TCP_PORT, help="TCP-порт")
    parser.add_argument("--clients", type=int, default=50, help="параллельных клиентов")
    parser.add_argument("--duration", type=float, default=30.0, help="длительность, с")
    parser.add_argument(
        "--embedded", action="store_true", help="поднять тестовый сервер в этом же процессе"
    )
    args = parser.parse_args()
    if args.embedded:
        result = asyncio.run(_embedded(args.clients, args.duration))
    else:
        result = asyncio.run(run_load(args.host, args.port, args.clients, args.duration))
    print(result.summary())
    for error in result.errors[:10]:
        print(f"  {error}", file=sys.stderr)
    sys.exit(1 if result.errors else 0)


if __name__ == "__main__":
    main()

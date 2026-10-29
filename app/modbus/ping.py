"""Серверный опрос slave для кнопки «Проверить соединение» (TASK-702).

UI не умеет говорить Modbus-TCP, поэтому опрос идёт с сервера: REST-роут
зовёт `probe_slave`, тот через `AsyncModbusTcpClient` подключается к своему же
Modbus-слушателю (loopback) и делает `attempts` контрольных чтений, замеряя
RTT каждого. Так проверяется весь стек — TCP, MBAP/PDU, контекст слейвов —
а не только память процесса.

Что читается: slave 10 — FC 4 (IR 1, зона UTC+0); слейвы 1..9 — FC 3 (HR 1).
Проба не пишет в регистры и не журналируется (анти-флуд журнала).

RTT считается только по успешным ответам; попытка со `isError()`-ответом
(например, под инъекцией exception) или исключением — ошибка. При
timeout-инъекции или delay больше `timeout_s` клиент получает таймаут —
проба честно покажет ошибки. Ошибка попытки не роняет сервер (инвариант 7).
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from pymodbus.client import AsyncModbusTcpClient

from app.modbus.config import Config

#: Таймаут одного контрольного чтения, с (худший случай пробы — attempts × timeout_s).
PROBE_TIMEOUT_S = 2.0

#: Ограничение числа попыток (UI не сможет запросить бесконечную пробу).
MAX_ATTEMPTS = 10


@dataclass(frozen=True)
class PingResult:
    """Итог пробы: счётчики успехов/ошибок и RTT по успешным ответам.

    `rtt_*_ms` — None, если успешных ответов не было. `last_error` — последняя
    ошибка попытки (для UI), None при полном успехе.
    """

    slave: int
    attempts: int
    ok: int
    errors: int
    rtt_min_ms: float | None
    rtt_avg_ms: float | None
    rtt_max_ms: float | None
    last_error: str | None


def probe_target(config: Config) -> tuple[str, int]:
    """Адрес Modbus-слушателя для пробы: wildcard-хост → loopback."""
    host = config.MODBUS_TCP_HOST
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    return host, config.MODBUS_TCP_PORT


async def probe_slave(
    config: Config,
    slave: int,
    attempts: int = 5,
    *,
    timeout_s: float = PROBE_TIMEOUT_S,
) -> PingResult:
    """Выполнить `attempts` контрольных чтений slave, замерив RTT каждого.

    Одно TCP-соединение на пробу (RTT чтения без handshake). Недоступность
    слушателя — все попытки считаются ошибками. `attempts` вне 1..`MAX_ATTEMPTS`
    → ValueError.
    """
    if not 1 <= attempts <= MAX_ATTEMPTS:
        raise ValueError(f"attempts должен быть 1..{MAX_ATTEMPTS}, получено: {attempts}")
    if slave not in range(1, 11):
        raise ValueError(f"slave должен быть 1..10, получено: {slave}")

    host, port = probe_target(config)
    client = AsyncModbusTcpClient(host, port=port, timeout=timeout_s)
    rtts_ms: list[float] = []
    errors = 0
    last_error: str | None = None
    try:
        if not await client.connect():
            return PingResult(
                slave=slave,
                attempts=attempts,
                ok=0,
                errors=attempts,
                rtt_min_ms=None,
                rtt_avg_ms=None,
                rtt_max_ms=None,
                last_error=f"не удалось подключиться к {host}:{port}",
            )
        for _ in range(attempts):
            started = time.perf_counter()
            try:
                if slave == 10:
                    response = await client.read_input_registers(1, count=1, device_id=10)
                else:
                    response = await client.read_holding_registers(1, count=1, device_id=slave)
            except Exception as exc:  # noqa: BLE001 — проба не должна ронять роут
                errors += 1
                last_error = f"{type(exc).__name__}: {exc}"
                continue
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            if response.isError():
                errors += 1
                last_error = f"ответ с ошибкой: {response}"
                continue
            rtts_ms.append(elapsed_ms)
    finally:
        client.close()

    ok = len(rtts_ms)
    return PingResult(
        slave=slave,
        attempts=attempts,
        ok=ok,
        errors=errors,
        rtt_min_ms=min(rtts_ms) if rtts_ms else None,
        rtt_avg_ms=(sum(rtts_ms) / ok) if rtts_ms else None,
        rtt_max_ms=max(rtts_ms) if rtts_ms else None,
        last_error=last_error,
    )

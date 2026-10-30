"""TASK-903: healthcheck-скрипт - живой, больной и недоступный сервер."""
from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEALTHCHECK = ROOT / "scripts" / "healthcheck.py"

_HTTP_OK = b"HTTP/1.1 200 OK\r\nContent-Length: 15\r\n\r\n{\"status\":\"ok\"}"
_HTTP_FAIL = b"HTTP/1.1 500 Internal Server Error\r\nContent-Length: 0\r\n\r\n"


async def _http_server(response: bytes) -> tuple[asyncio.Server, int]:
    """Фиктивный HTTP-сервер на свободном порту: один ответ на каждый запрос."""

    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        await reader.read(4096)
        writer.write(response)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = int(server.sockets[0].getsockname()[1])  # type: ignore[union-attr]
    return server, port


async def _run(url: str, modbus_port: int = 0) -> tuple[int, str, str]:
    """Запустить healthcheck.py, не блокируя loop (в нём живёт фиктивный сервер)."""
    command = [sys.executable, str(HEALTHCHECK), "--url", url]
    if modbus_port:
        command += ["--modbus-port", str(modbus_port)]
    process = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
    return process.returncode or 0, stdout.decode(), stderr.decode()


async def test_healthz_ok_and_modbus_open() -> None:
    server, http_port = await _http_server(_HTTP_OK)
    # Modbus-порт: слушающий сокет сообщает свой порт, конфликт исключён.
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    probe.listen(1)
    try:
        code, stdout, stderr = await _run(
            f"http://127.0.0.1:{http_port}/healthz", probe.getsockname()[1]
        )
        assert code == 0, stderr
        assert "OK" in stdout
    finally:
        probe.close()
        server.close()
        await server.wait_closed()


async def test_healthz_500_is_failure() -> None:
    server, port = await _http_server(_HTTP_FAIL)
    try:
        code, _, stderr = await _run(f"http://127.0.0.1:{port}/healthz")
        assert code == 1
        assert "FAIL" in stderr
    finally:
        server.close()
        await server.wait_closed()


def test_unreachable_is_failure() -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        dead_port = sock.getsockname()[1]
    run = subprocess.run(
        [sys.executable, str(HEALTHCHECK), "--url", f"http://127.0.0.1:{dead_port}/healthz"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert run.returncode == 1
    assert "FAIL" in run.stderr

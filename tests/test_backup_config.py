"""TASK-906: бэкап конфига поведений - экспорт переопределений и импорт пакетом."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKUP = ROOT / "scripts" / "backup_config.py"

#: Ответ GET /api/modbus/behaviors: одна дефолтная и одна переопределённая привязка.
_BEHAVIORS = {
    "items": [
        {
            "slave": 1,
            "area": "hr",
            "address": 1,
            "kind": "constant",
            "params": {"value": 0},
            "locked": False,
            "is_default": True,
        },
        {
            "slave": 2,
            "area": "ir",
            "address": 3,
            "kind": "sine",
            "params": {"amplitude": 100},
            "locked": True,
            "is_default": False,
        },
    ],
    "total": 2,
    "overrides": 1,
}

#: Тела POST-запросов, пойманных фиктивным сервером.
_batches: list[dict] = []


async def _rest_server() -> tuple[asyncio.Server, int]:
    """Фиктивный REST: GET behaviors и POST batch, как у настоящего сервера."""

    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        request_line = await reader.readline()
        headers = await reader.readuntil(b"\r\n\r\n")
        path = request_line.split()[1].decode()
        if path == "/api/modbus/behaviors":
            body = json.dumps(_BEHAVIORS).encode()
            status = b"200 OK"
        elif path == "/api/modbus/behaviors/batch":
            length = int(
                next(
                    line.split(b":")[1]
                    for line in headers.split(b"\r\n")
                    if line.lower().startswith(b"content-length")
                )
            )
            _batches.append(json.loads(await reader.readexactly(length)))
            body = b'{"applied": 1, "removed": 0}'
            status = b"200 OK"
        else:
            body = b"{}"
            status = b"404 Not Found"
        writer.write(
            b"HTTP/1.1 "
            + status
            + b"\r\nContent-Type: application/json\r\nContent-Length: "
            + str(len(body)).encode()
            + b"\r\n\r\n"
            + body
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = int(server.sockets[0].getsockname()[1])  # type: ignore[union-attr]
    return server, port


async def _run(*args: str) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(BACKUP),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
    return process.returncode or 0, stdout.decode(), stderr.decode()


async def test_save_keeps_only_overrides(tmp_path: Path) -> None:
    server, port = await _rest_server()
    backup_file = tmp_path / "backup.json"
    try:
        code, stdout, stderr = await _run(
            "save", "--file", str(backup_file), "--base-url", f"http://127.0.0.1:{port}"
        )
        assert code == 0, stderr
        assert "сохранено переопределений: 1" in stdout
        backup = json.loads(backup_file.read_text(encoding="utf-8"))
        assert backup["version"] == 1
        assert [item["address"] for item in backup["items"]] == [3]
        assert backup["items"][0]["kind"] == "sine"
    finally:
        server.close()
        await server.wait_closed()


async def test_load_sends_batch(tmp_path: Path) -> None:
    server, port = await _rest_server()
    _batches.clear()
    backup_file = tmp_path / "backup.json"
    backup_file.write_text(
        json.dumps(
            {
                "version": 1,
                "items": [
                    {
                        "slave": 2,
                        "area": "ir",
                        "address": 3,
                        "kind": "sine",
                        "params": {"amplitude": 100},
                        "locked": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    try:
        code, stdout, stderr = await _run(
            "load", "--file", str(backup_file), "--base-url", f"http://127.0.0.1:{port}"
        )
        assert code == 0, stderr
        assert "применено: 1" in stdout
        assert _batches == [
            {
                "items": [
                    {
                        "slave": 2,
                        "area": "ir",
                        "address": 3,
                        "kind": "sine",
                        "params": {"amplitude": 100},
                        "locked": True,
                    }
                ]
            }
        ]
    finally:
        server.close()
        await server.wait_closed()

"""TASK-903: проверка живости сервера (для systemd, cron и ручной диагностики).

Проверяет `GET /healthz` (web UI жив) и по желанию TCP-порт Modbus.
Код выхода 0 - всё в порядке, 1 - проверка не прошла.

    uv run python scripts/healthcheck.py
    uv run python scripts/healthcheck.py --modbus-port 5020
"""
from __future__ import annotations

import argparse
import socket
import sys
import urllib.error
import urllib.request

HEALTHZ_TIMEOUT_S = 5.0
MODBUS_TIMEOUT_S = 5.0


def check_healthz(url: str) -> str | None:
    """Вернуть текст ошибки или None, если /healthz отвечает 2xx."""
    try:
        with urllib.request.urlopen(url, timeout=HEALTHZ_TIMEOUT_S) as response:
            if 200 <= response.status < 300:
                return None
            return f"{url}: HTTP {response.status}"
    except (urllib.error.URLError, OSError) as exc:
        return f"{url}: {exc}"


def check_modbus_tcp(host: str, port: int) -> str | None:
    """Вернуть текст ошибки или None, если Modbus-порт принимает TCP."""
    try:
        with socket.create_connection((host, port), timeout=MODBUS_TIMEOUT_S):
            return None
    except OSError as exc:
        return f"modbus {host}:{port}: {exc}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Проверка живости Modbus-TCP сервера")
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8765/healthz",
        help="URL healthcheck-эндпоинта",
    )
    parser.add_argument("--modbus-host", default="127.0.0.1", help="адрес Modbus-порта")
    parser.add_argument(
        "--modbus-port", type=int, default=0, help="TCP-порт Modbus (0 - не проверять)"
    )
    args = parser.parse_args()

    errors = [error for error in [check_healthz(args.url)] if error]
    if args.modbus_port:
        error = check_modbus_tcp(args.modbus_host, args.modbus_port)
        if error:
            errors.append(error)

    if errors:
        for error in errors:
            print(f"FAIL {error}", file=sys.stderr)
        sys.exit(1)
    print("OK")


if __name__ == "__main__":
    main()

"""TASK-906: бэкап конфига поведений (ADR-010) - экспорт и импорт JSON.

Сохраняет только переопределения (привязки с `is_default=false`); демо-дефолты
воспроизводятся кодом и в файл не попадают. Значения регистров не бэкапятся:
это live-состояние сервера, а не конфигурация.

    uv run python scripts/backup_config.py save --file backup.json
    uv run python scripts/backup_config.py load --file backup.json
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

BACKUP_FORMAT_VERSION = 1
HTTP_TIMEOUT_S = 30.0


def _request(base_url: str, path: str, payload: dict | None = None) -> dict:
    """GET или POST (с JSON-телом) к REST-эндпоинту сервера."""
    url = base_url.rstrip("/") + path
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
        return json.loads(response.read())


def save(base_url: str, file: str) -> int:
    """Выгрузить переопределения поведений в JSON-файл; вернуть их число."""
    body = _request(base_url, "/api/modbus/behaviors")
    items = [
        {
            "slave": item["slave"],
            "area": item["area"],
            "address": item["address"],
            "kind": item["kind"],
            "params": item["params"],
            "locked": item["locked"],
        }
        for item in body["items"]
        if not item["is_default"]
    ]
    with open(file, "w", encoding="utf-8") as stream:
        json.dump(
            {"version": BACKUP_FORMAT_VERSION, "items": items}, stream, ensure_ascii=False, indent=2
        )
        stream.write("\n")
    return len(items)


def load(base_url: str, file: str) -> tuple[int, int]:
    """Залить переопределения из JSON-файла пакетом; (applied, removed)."""
    with open(file, encoding="utf-8") as stream:
        backup = json.load(stream)
    if backup.get("version") != BACKUP_FORMAT_VERSION:
        raise ValueError(f"неизвестная версия формата бэкапа: {backup.get('version')!r}")
    items = [
        {
            "slave": item["slave"],
            "area": item["area"],
            "address": item["address"],
            "kind": item["kind"],
            "params": item.get("params", {}),
            "locked": item.get("locked", False),
        }
        for item in backup.get("items", [])
    ]
    result = _request(base_url, "/api/modbus/behaviors/batch", {"items": items})
    return int(result["applied"]), int(result["removed"])


def main() -> None:
    # Кириллица в выводе: при пайпе Windows печатает в locale-кодировке.
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Бэкап конфига поведений Modbus-сервера")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("save", "load"):
        sub = subparsers.add_parser(name, help="выгрузить" if name == "save" else "залить")
        sub.add_argument("--file", required=True, help="JSON-файл бэкапа")
        sub.add_argument(
            "--base-url", default="http://127.0.0.1:8765", help="адрес web UI/REST"
        )
    args = parser.parse_args()

    try:
        if args.command == "save":
            count = save(args.base_url, args.file)
            print(f"сохранено переопределений: {count}")
        else:
            applied, removed = load(args.base_url, args.file)
            print(f"применено: {applied}, сброшено: {removed}")
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

"""Генератор конфигов для клиентских инструментов (TASK-742).

Собирает готовые настройки/скрипты/flow для Modbus Poll, QModMaster,
CAS Modbus Scanner, pyModbus и Node-RED по цели (slave, area, адрес,
количество значений). Чистые функции: содержимое детерминировано входом,
состояния и журнала нет.

Особенности адресации (см. `app/modbus/types.py`): адрес на проводе равен
адресу из `docs/HRIR_SPEC.md` (1-based, без пересчёта), поэтому во всех
конфигах подставляется адрес значения как есть.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from app.modbus.types import SLAVE_SPECS, Area, RegisterFormat

ConfigTool = Literal["modbus_poll", "qmodmaster", "cas_scanner", "pymodbus", "nodered"]

CONFIG_TOOLS: tuple[ConfigTool, ...] = (
    "modbus_poll",
    "qmodmaster",
    "cas_scanner",
    "pymodbus",
    "nodered",
)

TOOL_TITLES: dict[ConfigTool, str] = {
    "modbus_poll": "Modbus Poll",
    "qmodmaster": "QModMaster",
    "cas_scanner": "CAS Modbus Scanner",
    "pymodbus": "pyModbus",
    "nodered": "Node-RED",
}

TOOL_FILENAMES: dict[ConfigTool, str] = {
    "modbus_poll": "modbus-poll.txt",
    "qmodmaster": "qmodmaster.txt",
    "cas_scanner": "cas-modbus-scanner.txt",
    "pymodbus": "pymodbus_read.py",
    "nodered": "nodered-flow.json",
}

_FC_BY_AREA: dict[Area, int] = {"cl": 1, "di": 2, "hr": 3, "ir": 4}

_FC_NAMES: dict[Area, str] = {
    "cl": "Read Coils (FC 1)",
    "di": "Read Discrete Inputs (FC 2)",
    "hr": "Read Holding Registers (FC 3)",
    "ir": "Read Input Registers (FC 4)",
}

_AREA_LABELS: dict[Area, str] = {
    "cl": "Coils (cl)",
    "di": "Discrete Inputs (di)",
    "hr": "Holding Registers (hr)",
    "ir": "Input Registers (ir)",
}

#: Порядок слов/байтов по ABCD-коду формата (A — старший байт значения).
_ORDER_NOTES: dict[str, str] = {
    "ABCD": "слова big-endian (старшее слово первым), байты в словах big-endian",
    "CDAB": "слова little-endian (младшее слово первым), байты в словах big-endian",
    "BADC": "слова big-endian, байты в словах little-endian (swap bytes)",
    "DCBA": "слова little-endian, байты в словах little-endian (swap bytes + swap words)",
}

#: Сырой адрес в запросе Modbus — максимум регистров на чтение (FC 3/4).
MAX_READ_REGISTERS = 125


@dataclass(frozen=True)
class ConfigTarget:
    """Цель генерации: куда обращаться и что читать."""

    slave: int
    area: Area
    address: int
    count: int
    host: str
    port: int


@dataclass(frozen=True)
class GeneratedConfig:
    """Результат генерации: имя файла для скачивания и его содержимое."""

    filename: str
    content: str


def order_of(fmt: RegisterFormat) -> str:
    """ABCD-код формата: `FLOAT_CDAB` → «CDAB»; для uint16 — «AB»."""
    if fmt is RegisterFormat.UINT16:
        return "AB"
    return str(fmt.value).rsplit("_", 1)[1].upper()


def type_label(fmt: RegisterFormat) -> str:
    """Человеческий тип данных: «uint16» / «uint32» / «float»."""
    return "float" if fmt.is_float else ("uint32" if fmt.is_32bit else "uint16")


def wire_quantity(area: Area, fmt: RegisterFormat, count: int) -> int:
    """Число регистров/бит в Modbus-запросе: для 32-битных форматов — 2 на значение."""
    if area in ("cl", "di"):
        return count
    return count * fmt.registers_per_value


def check_target(target: ConfigTarget) -> None:
    """Проверить цель: слейв, область, сетку адресов и лимит FC 3/4 (ValueError)."""
    spec = SLAVE_SPECS.get(target.slave)
    if spec is None:
        raise ValueError(f"slave {target.slave} вне диапазона 1..10")
    if not spec.area_ranges(target.area):
        raise ValueError(f"область {_AREA_LABELS[target.area]} на slave {target.slave} отсутствует")
    if target.count < 1:
        raise ValueError("количество значений должно быть ≥ 1")
    step = spec.area_ranges(target.area)[0].step
    for offset in range(target.count):
        address = target.address + offset * step
        if not spec.has_address(target.area, address):
            raise ValueError(
                f"адрес {address} вне карты slave {target.slave} "
                f"({_AREA_LABELS[target.area]})"
            )
    if target.area not in ("cl", "di"):
        quantity = wire_quantity(target.area, spec.fmt, target.count)
        if quantity > MAX_READ_REGISTERS:
            raise ValueError(
                f"слишком большой диапазон: {quantity} регистров в запросе, максимум {MAX_READ_REGISTERS}"
            )


def _fmt_note(target: ConfigTarget) -> str:
    spec = SLAVE_SPECS[target.slave]
    if target.area in ("cl", "di"):
        return "биты: 0/1"
    if spec.fmt is RegisterFormat.UINT16:
        return "uint16 (одно слово, без перестановки)"
    order = order_of(spec.fmt)
    return f"{type_label(spec.fmt)} {order}: {_ORDER_NOTES[order]}"


def _title_block(target: ConfigTarget, title: str) -> str:
    spec = SLAVE_SPECS[target.slave]
    quantity = wire_quantity(target.area, spec.fmt, target.count)
    return (
        f"{title} — настройки для {target.host}:{target.port}\n"
        f"{'=' * 60}\n"
        f"Цель: slave {target.slave}, {_AREA_LABELS[target.area]}, "
        f"адрес {target.address}, значений: {target.count} "
        f"(регистров/бит в запросе: {quantity})\n"
        f"Формат данных: {_fmt_note(target)}\n"
    )


def _notes_block(target: ConfigTarget) -> str:
    lines = [
        "Примечания:",
        "  • Адресация сервера — как в docs/HRIR_SPEC.md: вводите адрес значения",
        "    из спецификации без пересчёта (адрес на проводе = адрес в таблице).",
        "  • Допустимые функции: 1, 2, 3, 4, 5, 6, 16; остальные → Illegal Function.",
    ]
    if target.slave == 10:
        lines.append(
            "  • Slave 10 — только Input Registers (FC 4); IR 300..301 — Unix timestamp"
            " (uint32 ABCD, старший регистр 300)."
        )
    else:
        lines.append("  • Slave 10 — только Input Registers (FC 4).")
    lines.append(
        "  • Значения общие (shared last-write-wins): другие пользователи могут их менять."
    )
    return "\n".join(lines)


def _read_registers_block(target: ConfigTarget) -> str:
    spec = SLAVE_SPECS[target.slave]
    quantity = wire_quantity(target.area, spec.fmt, target.count)
    return (
        f"Function:  {_FC_NAMES[target.area]}\n"
        f"Address:   {target.address}\n"
        f"Quantity:  {quantity}\n"
    )


def _gen_modbus_poll(target: ConfigTarget) -> str:
    spec = SLAVE_SPECS[target.slave]
    return (
        _title_block(target, "Modbus Poll")
        + "\n"
        "1. Соединение: Connection → Connect → Modbus TCP/IP\n"
        f"     IP Address: {target.host}\n"
        f"     Port:       {target.port}\n"
        f"     Slave ID:   {target.slave}\n"
        "\n"
        "2. Чтение: Setup → Read/Write Definition\n"
        + _read_registers_block(target)
        + "     Scan Rate: 1000 ms\n"
        "\n"
        "3. Формат отображения (Display):\n"
        f"     {_fmt_note(target)}\n"
        f"     Для 32-битных значений выберите вариант с порядком {order_of(spec.fmt)}"
        " в настройках float/32-bit.\n"
        "\n" + _notes_block(target) + "\n"
    )


def _gen_qmodmaster(target: ConfigTarget) -> str:
    spec = SLAVE_SPECS[target.slave]
    return (
        _title_block(target, "QModMaster")
        + "\n"
        "1. Вкладка «Modbus TCP»:\n"
        f"     Modbus Server: {target.host}\n"
        f"     Port:          {target.port}\n"
        f"     Unit ID:       {target.slave}\n"
        "\n"
        "2. Параметры запроса:\n"
        f"     Function Code: {_FC_BY_AREA[target.area]:02d} — {_FC_NAMES[target.area]}\n"
        f"     Base Address:  {target.address}   (если версия использует 0-based — вычтите 1)\n"
        f"     Number of Registers: {wire_quantity(target.area, spec.fmt, target.count)}\n"
        "     Scan Rate:     1000 ms\n"
        "\n"
        "3. Отображение данных:\n"
        f"     {_fmt_note(target)}\n"
        "\n" + _notes_block(target) + "\n"
    )


def _gen_cas_scanner(target: ConfigTarget) -> str:
    spec = SLAVE_SPECS[target.slave]
    return (
        _title_block(target, "CAS Modbus Scanner")
        + "\n"
        "1. Connection → Modbus TCP:\n"
        f"     Host:     {target.host}\n"
        f"     Port:     {target.port}\n"
        f"     Device ID: {target.slave}\n"
        "\n"
        "2. Сообщение (Message):\n"
        f"     Function: {_FC_NAMES[target.area]}\n"
        f"     Address:  {target.address}\n"
        f"     Length:   {wire_quantity(target.area, spec.fmt, target.count)}\n"
        "\n"
        "3. Формат данных:\n"
        f"     {_fmt_note(target)}\n"
        "\n" + _notes_block(target) + "\n"
    )


def _decode_snippet(fmt: RegisterFormat) -> str:
    """Функция decode_value для скрипта pyModbus (32-битные форматы)."""
    order = order_of(fmt)
    unpack = ">f" if fmt.is_float else ">I"
    if order == "ABCD":
        pack = 'struct.pack(">HH", regs[0], regs[1])'
    elif order == "CDAB":
        pack = 'struct.pack(">HH", regs[1], regs[0])'
    elif order == "BADC":
        pack = 'struct.pack(">HH", _swap16(regs[0]), _swap16(regs[1]))'
    else:  # DCBA
        pack = 'struct.pack(">HH", _swap16(regs[1]), _swap16(regs[0]))'
    swap_helper = ""
    if order in ("BADC", "DCBA"):
        swap_helper = (
            "\ndef _swap16(word: int) -> int:\n"
            '    """Поменять байты местами внутри 16-битного слова."""\n'
            "    return ((word & 0xFF) << 8) | (word >> 8)\n"
        )
    return (
        f"{swap_helper}\n"
        "def decode_value(regs: list[int]) -> "
        + ("float" if fmt.is_float else "int")
        + ":\n"
        f'    """{type_label(fmt)} {order}: {_ORDER_NOTES[order]}."""\n'
        f"    raw = {pack}\n"
        f'    return struct.unpack("{unpack}", raw)[0]\n'
    )


def _gen_pymodbus(target: ConfigTarget) -> str:
    spec = SLAVE_SPECS[target.slave]
    quantity = wire_quantity(target.area, spec.fmt, target.count)
    bits = target.area in ("cl", "di")
    reader = {
        "cl": "read_coils",
        "di": "read_discrete_inputs",
        "hr": "read_holding_registers",
        "ir": "read_input_registers",
    }[target.area]
    doc_extra = ""
    if target.slave == 10:
        doc_extra = (
            "\nВнимание: IR 300..301 slave 10 — Unix timestamp uint32 ABCD"
            "\n(старший регистр 300); скрипт печатает их как uint16."
        )
    imports = "import struct\n\n" if spec.fmt.is_32bit and not bits else ""
    decode = _decode_snippet(spec.fmt) if spec.fmt.is_32bit and not bits else ""
    step = 1 if bits else spec.fmt.registers_per_value
    if bits:
        print_loop = (
            "        for i in range(COUNT):\n"
            "            print(f'  [{ADDRESS + i}] {result.bits[i]}')\n"
        )
    elif spec.fmt.is_32bit:
        print_loop = (
            "        for i in range(COUNT):\n"
            "            addr = ADDRESS + 2 * i\n"
            "            value = decode_value(regs[2 * i : 2 * i + 2])\n"
            "            print(f'  [{addr}] {value}')\n"
        )
    else:
        print_loop = (
            "        for i in range(COUNT):\n"
            "            print(f'  [{ADDRESS + i}] {regs[i]}')\n"
        )
    regs_line = "" if bits else "        regs = result.registers\n"
    return (
        f'#!/usr/bin/env python3\n'
        f'"""Чтение с тестового Modbus-сервера {target.host}:{target.port}.\n'
        f"\n"
        f"Сгенерировано для: slave {target.slave}, {_AREA_LABELS[target.area]}, "
        f"адрес {target.address}, значений: {target.count} ({_fmt_note(target)})."
        f"{doc_extra}\n"
        f"\n"
        f"Запуск: pip install \"pymodbus>=3.6\" && python {TOOL_FILENAMES['pymodbus']}\n"
        f'"""\n'
        f"{imports}"
        "from pymodbus.client import ModbusTcpClient\n"
        "\n"
        f'HOST = "{target.host}"\n'
        f"PORT = {target.port}\n"
        f"SLAVE = {target.slave}  # unit ID\n"
        f"ADDRESS = {target.address}  # адрес из спецификации (1-based, без пересчёта)\n"
        f"COUNT = {target.count}  # количество значений\n"
        f"QUANTITY = {quantity}  # регистров/бит в запросе (COUNT × {step})\n"
        f"{decode}\n"
        "def main() -> None:\n"
        "    client = ModbusTcpClient(HOST, port=PORT)\n"
        "    if not client.connect():\n"
        '        raise SystemExit(f"Не удалось подключиться к {HOST}:{PORT}")\n'
        "    try:\n"
        f"        result = client.{reader}(ADDRESS, count=QUANTITY, device_id=SLAVE)\n"
        "        if result.isError():\n"
        '            raise SystemExit(f"Ошибка Modbus: {result}")\n'
        f"{regs_line}"
        f"{print_loop}"
        "    finally:\n"
        "        client.close()\n"
        "\n"
        '\nif __name__ == "__main__":\n'
        "    main()\n"
    )


def _gen_nodered(target: ConfigTarget) -> str:
    spec = SLAVE_SPECS[target.slave]
    quantity = wire_quantity(target.area, spec.fmt, target.count)
    payload = {
        "fc": _FC_BY_AREA[target.area],
        "unitid": target.slave,
        "address": target.address,
        "quantity": quantity,
    }
    flow = [
        {
            "id": "modbus-sandbox-client",
            "type": "modbus-client",
            "name": f"{target.host}:{target.port}",
            "client": "tcp",
            "protocol": "tcp",
            "tcpHost": target.host,
            "tcpPort": str(target.port),
            "unitId": str(target.slave),
            "commandDelay": 1,
            "clientTimeout": 2000,
            "reconnectOnTimeout": True,
            "reconnectCount": 3,
            "showStatusActivities": False,
            "showErrors": False,
        },
        {
            "id": "modbus-sandbox-comment",
            "type": "comment",
            "name": "Требуется node-red-contrib-modbus",
            "info": (
                f"slave {target.slave}, {_AREA_LABELS[target.area]}, адрес {target.address}, "
                f"значений: {target.count} ({_fmt_note(target)}). "
                "Адресация: адрес значения из docs/HRIR_SPEC.md без пересчёта. "
                "Значения общие (shared last-write-wins)."
            ),
            "wires": [],
        },
        {
            "id": "modbus-sandbox-inject",
            "type": "inject",
            "name": f"Опрос 1 с ({_FC_NAMES[target.area]})",
            "props": [{"p": "payload"}],
            "repeat": "1",
            "crontab": "",
            "once": True,
            "onceDelay": 0.5,
            "topic": "",
            "payload": json.dumps(payload, ensure_ascii=False),
            "payloadType": "json",
            "x": 160,
            "y": 80,
            "wires": [["modbus-sandbox-getter"]],
        },
        {
            "id": "modbus-sandbox-getter",
            "type": "modbus-flex-getter",
            "name": f"Read slave {target.slave} addr {target.address}",
            "server": "modbus-sandbox-client",
            "showStatusActivities": False,
            "showErrors": False,
            "x": 400,
            "y": 80,
            "wires": [["modbus-sandbox-debug"], []],
        },
        {
            "id": "modbus-sandbox-debug",
            "type": "debug",
            "name": "Ответ Modbus",
            "active": True,
            "tosidebar": True,
            "console": False,
            "complete": "payload",
            "x": 660,
            "y": 80,
            "wires": [],
        },
    ]
    return json.dumps(flow, ensure_ascii=False, indent=2) + "\n"


_GENERATORS: dict[ConfigTool, Callable[[ConfigTarget], str]] = {
    "modbus_poll": _gen_modbus_poll,
    "qmodmaster": _gen_qmodmaster,
    "cas_scanner": _gen_cas_scanner,
    "pymodbus": _gen_pymodbus,
    "nodered": _gen_nodered,
}


def generate(tool: ConfigTool, target: ConfigTarget) -> GeneratedConfig:
    """Сгенерировать конфиг `tool` для цели `target` (ValueError при плохой цели)."""
    check_target(target)
    return GeneratedConfig(filename=TOOL_FILENAMES[tool], content=_GENERATORS[tool](target))

"""Пользовательские теги регистров (TASK-718).

Тег — имя сигнала, которое пользователь задаёт поверх автоматического
(имя поведения slave 1..9 либо метрика времени slave 10). Теги общие для
всех подключений (shared last-write-wins) и живут только в памяти:
перезапуск сервера сбрасывает их к автоматическим (инвариант 9).
"""
from __future__ import annotations

from app.modbus.behavior import Binding
from app.modbus.types import SLAVE_SPECS, Area

#: Потолок длины пользовательского тега (TASK-1004: защита от раздувания).
MAX_TAG_LENGTH = 64


def validate_tag(slave: int, area: Area, address: int, tag: str) -> str:
    """Проверить привязку и текст тега; вернуть тег после `strip`.

    `ValueError`: адрес вне карты регистров, пустой тег после `strip`,
    тег длиннее `MAX_TAG_LENGTH`.
    """
    spec = SLAVE_SPECS.get(slave)
    if spec is None:
        raise ValueError(f"неизвестный slave: {slave}")
    if not spec.has_address(area, address):
        raise ValueError(f"slave {slave}: адрес {address} вне карты регистров ({area})")
    cleaned = tag.strip()
    if not cleaned:
        raise ValueError("тег не может быть пустым")
    if len(cleaned) > MAX_TAG_LENGTH:
        raise ValueError(f"тег длиннее {MAX_TAG_LENGTH} символов")
    return cleaned


class TagStore:
    """Словарь пользовательских тегов `(slave, area, address) → тег`."""

    def __init__(self) -> None:
        self._tags: dict[Binding, str] = {}

    def __len__(self) -> int:
        return len(self._tags)

    def get(self, slave: int, area: Area, address: int) -> str | None:
        """Пользовательский тег привязки либо `None` (тег не задан)."""
        return self._tags.get((slave, area, address))

    def set(self, slave: int, area: Area, address: int, tag: str) -> str:
        """Задать тег (shared last-write-wins); вернуть сохранённый текст."""
        cleaned = validate_tag(slave, area, address, tag)
        self._tags[(slave, area, address)] = cleaned
        return cleaned

    def clear(self, slave: int, area: Area, address: int) -> bool:
        """Сбросить тег к автоматическому; `True` — тег был и удалён."""
        return self._tags.pop((slave, area, address), None) is not None

    def for_slave(self, slave: int) -> dict[str, str]:
        """Теги слейва вида `{адрес: тег}` (для кадра состояния, TASK-718)."""
        return {
            str(address): tag
            for (tag_slave, _area, address), tag in self._tags.items()
            if tag_slave == slave
        }

    def snapshot(self) -> dict[str, dict[Area, dict[str, str]]]:
        """Все теги вида `{slave: {area: {адрес: тег}}}` (GET /api/modbus/tags)."""
        out: dict[str, dict[Area, dict[str, str]]] = {}
        for (slave, area, address), tag in self._tags.items():
            out.setdefault(str(slave), {}).setdefault(area, {})[str(address)] = tag
        return out

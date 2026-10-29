"""Модель поведения и реестр привязок (slave, area, address) → Behavior.

TASK-201: модель `Behavior` (kind / params / locked) и реестр переопределений.
TASK-205: дефолтные поведения slave 1..9 (материализация в реестр).
TASK-241: нормализация value-параметров по формату регистра (`normalize_params`).
Семантика движка — `docs/adr/ADR-003-behavior-engine.md`:

- привязка — value-grid адрес (`SlaveSpec.area_ranges`): для слейвов 2..9
  нечётные 1, 3, …, 1023;
- slave 10 поведений не имеет (его IR вычисляется, см. `slave10`);
- числовые kind'ы работают с hr/ir, дискретные — с cl/di.
"""
from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, get_args

from app.modbus.codec import clamp_value
from app.modbus.journal import Journal, JournalEntry
from app.modbus.slave_map import EXC_DEVICE_FAILURE, ModbusMapError
from app.modbus.types import AREAS, SLAVE_SPECS, Area, RegisterFormat

BehaviorKind = Literal[
    "constant",
    "counter",
    "sawtooth",
    "triangle",
    "meander",
    "sine",
    "random_uniform",
    "random_walk",
    "ramp_oneshot",
    "countdown",
    "step",
    "follow",
    "toggle",
    "pwm",
    "random_bit",
    "counter_bits",
    "follow_bit",
]

#: Все допустимые kind'ы (проверка на runtime — вход из REST позже).
BEHAVIOR_KINDS: frozenset[str] = frozenset(get_args(BehaviorKind))

#: Числовые поведения — области hr/ir.
NUMERIC_KINDS: frozenset[str] = frozenset(
    {
        "constant",
        "counter",
        "sawtooth",
        "triangle",
        "meander",
        "sine",
        "random_uniform",
        "random_walk",
        "ramp_oneshot",
        "countdown",
        "step",
        "follow",
    }
)

#: Дискретные поведения — области cl/di.
DISCRETE_KINDS: frozenset[str] = frozenset(
    {"constant", "toggle", "pwm", "random_bit", "counter_bits", "follow_bit"}
)

#: Привязка поведения: (slave, area, value-grid address).
Binding = tuple[int, Area, int]


@dataclass(frozen=True)
class Behavior:
    """Конфигурация поведения одного регистра/бита.

    `params` — параметры kind'а (см. `generators.py`); `locked=True` запрещает
    внешние записи в привязку (0x04 + журнал, ADR-003 п. 3).
    """

    kind: BehaviorKind
    params: dict[str, Any] = field(default_factory=dict)
    locked: bool = False

    def __post_init__(self) -> None:
        if self.kind not in BEHAVIOR_KINDS:
            raise ValueError(f"неизвестный kind поведения: {self.kind!r}")
        if not isinstance(self.params, dict):
            raise TypeError(f"params должен быть dict, получено: {type(self.params).__name__}")
        if not isinstance(self.locked, bool):
            raise TypeError(f"locked должен быть bool, получено: {type(self.locked).__name__}")


def check_binding(slave: int, area: Area, address: int) -> None:
    """Проверить привязку: slave 1..9, область из AREAS, value-grid адрес."""
    if not isinstance(slave, int) or isinstance(slave, bool):
        raise TypeError(f"slave должен быть int, получено: {type(slave).__name__}")
    if slave not in SLAVE_SPECS:
        raise ValueError(f"неизвестный slave: {slave}")
    if slave == 10:
        raise ValueError("slave 10: поведения не поддерживаются (IR вычисляются)")
    if area not in AREAS:
        raise ValueError(f"неизвестная область: {area!r}")
    if not isinstance(address, int) or isinstance(address, bool):
        raise TypeError(f"address должен быть int, получено: {type(address).__name__}")
    if not SLAVE_SPECS[slave].has_address(area, address):
        raise ValueError(f"slave {slave}: {area} адрес {address} вне карты значений")


def check_kind_for_area(kind: str, area: Area) -> None:
    """Проверить применимость kind'а к области (числовые — hr/ir, дискретные — cl/di)."""
    allowed = NUMERIC_KINDS if area in ("hr", "ir") else DISCRETE_KINDS
    if kind not in allowed:
        raise ValueError(f"kind {kind!r} не применим к области {area}")


# --- нормализация параметров (TASK-241) -------------------------------------------------
#
# Value-параметры приводятся к формату регистра слейва: uint16 — clamp 0..65535,
# uint32 — clamp 0..2^32-1 (дробная часть отбрасывается), float — любой, кроме
# NaN/Inf (заменяются на 0). Прочие параметры (step, period_s, duty, …) — не
# значения регистров и не трогаются.

#: Параметры kind'а, являющиеся значениями регистров (приводятся к формату).
_VALUE_PARAMS: dict[str, tuple[str, ...]] = {
    "constant": ("value",),
    "counter": ("start", "min", "max"),
    "sawtooth": ("min", "max"),
    "triangle": ("min", "max"),
    "meander": ("min", "max"),
    "sine": ("min", "max"),
    "random_uniform": ("min", "max"),
    "random_walk": ("min", "max"),
    "ramp_oneshot": ("from", "to", "then"),
    "countdown": ("start", "min"),
    "step": ("values",),
}


def _normalize_param(raw: Any, fmt: RegisterFormat) -> Any:
    """Привести параметр-значение (или список значений) к формату регистра."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float, list, tuple)):
        return raw  # строки ("hold"), bool и прочее — не значения регистров
    if isinstance(raw, (int, float)):
        return clamp_value(raw, fmt)
    return type(raw)([_normalize_param(item, fmt) for item in raw])


def normalize_params(behavior: Behavior, fmt: RegisterFormat) -> Behavior:
    """Привести value-параметры поведения к формату регистра (TASK-241).

    Параметры, отсутствующие в `params`, не трогаются (их значения по умолчанию
    задаёт генератор). Если нормализация ничего не меняет, возвращается исходный
    объект `Behavior` — движок сравнивает конфигурации по идентичности
    (ADR-003 п. 5).
    """
    value_params = _VALUE_PARAMS.get(behavior.kind)
    if not value_params:
        return behavior
    normalized: dict[str, Any] = {}
    changed = False
    for name, raw in behavior.params.items():
        if name in value_params:
            new = _normalize_param(raw, fmt)
            if new != raw:
                changed = True
                raw = new
        normalized[name] = raw
    if not changed:
        return behavior
    return Behavior(behavior.kind, normalized, locked=behavior.locked)


# --- дефолтные поведения (TASK-205, ADR-003 п. 5) --------------------------------------
#
# Дефолты материализуются в реестре: один экземпляр Behavior-дефолта
# разделяется многими привязками, `set()` подменяет объект, движок замечает
# смену по идентичности. Slave 10 исключён — его IR вычисляются (`slave10`).

#: CL/HR — constant 0; внешние записи разрешены (shared last-write-wins).
_DEFAULT_CONSTANT: Behavior = Behavior("constant", {"value": 0}, locked=False)

#: DI — авто-паттерн: бит address−1 счётчика секунд.
_DEFAULT_COUNTER_BITS: Behavior = Behavior("counter_bits", {}, locked=True)

#: IR slave 1 (UINT16): counter step=1 раз в interval_s, locked=True.
#: max = предел формата: иначе clamp_value «залипает» на 65535.
_DEFAULT_IR_COUNTER_16: Behavior = Behavior(
    "counter", {"step": 1, "interval_s": 1, "max": 0xFFFF}, locked=True
)

#: IR slaves 2..9 (uint32/float): counter step=1 раз в interval_s, locked=True.
_DEFAULT_IR_COUNTER_32: Behavior = Behavior(
    "counter", {"step": 1, "interval_s": 1, "max": 0xFFFF_FFFF}, locked=True
)


def _ir_default(fmt: RegisterFormat) -> Behavior:
    """IR-дефолт по формату: max = предел типа значения."""
    return _DEFAULT_IR_COUNTER_16 if fmt.registers_per_value == 1 else _DEFAULT_IR_COUNTER_32


def _default_for(slave: int, area: Area) -> Behavior:
    """Дефолт области слейва: ir — счётчик по формату, di — биты счётчика, остальное — 0."""
    if area == "ir":
        return _ir_default(SLAVE_SPECS[slave].fmt)
    if area == "di":
        return _DEFAULT_COUNTER_BITS
    return _DEFAULT_CONSTANT


def default_behavior(slave: int, area: Area, address: int) -> Behavior:
    """Дефолтное поведение привязки (ADR-003 п. 5); привязка валидируется."""
    check_binding(slave, area, address)
    return _default_for(slave, area)


def _materialize_defaults() -> dict[Binding, Behavior]:
    """Все допустимые привязки слейвов 1..9 → дефолтные поведения (общие экземпляры)."""
    defaults: dict[Binding, Behavior] = {}
    for slave, spec in SLAVE_SPECS.items():
        if slave == 10:
            continue
        for area in AREAS:
            behavior = _default_for(slave, area)
            for rng in spec.area_ranges(area):
                for address in range(rng.start, rng.stop + 1, rng.step):
                    defaults[(slave, area, address)] = behavior
    return defaults


#: Дефолты всех привязок; копия — стартовое состояние каждого BehaviorRegistry.
_DEFAULT_BINDINGS: dict[Binding, Behavior] = _materialize_defaults()


class BehaviorRegistry:
    """Реестр привязок: дефолты слейвов 1..9 материализованы, `set()` подменяет объект.

    TASK-205 (ADR-003 п. 5): при создании все допустимые привязки слейвов 1..9
    получают дефолтные поведения; один экземпляр `Behavior`-дефолта разделяется
    многими привязками. Значение из `get()`/`items()` — тот же объект, пока
    привязка не переопределена: движок сравнивает конфигурации по идентичности.
    `remove()`/`reset()` возвращают привязки к дефолтам (реестр не сжимается).
    Slave 10 в реестре не бывает.
    """

    def __init__(self) -> None:
        self._behaviors: dict[Binding, Behavior] = dict(_DEFAULT_BINDINGS)

    def get(self, slave: int, area: Area, address: int) -> Behavior | None:
        """Текущее поведение привязки (дефолт или переопределение).

        None — привязки в реестре нет (slave 10, адрес вне карты значений).
        """
        return self._behaviors.get((slave, area, address))

    def set(self, slave: int, area: Area, address: int, behavior: Behavior) -> None:
        """Установить поведение; привязка и kind/область валидируются.

        Value-параметры приводятся к формату регистра слейва (TASK-241);
        для cl/di (биты) нормализация не нужна.
        """
        if not isinstance(behavior, Behavior):
            raise TypeError(f"ожидается Behavior, получено: {type(behavior).__name__}")
        check_binding(slave, area, address)
        check_kind_for_area(behavior.kind, area)
        if area in ("hr", "ir"):
            behavior = normalize_params(behavior, SLAVE_SPECS[slave].fmt)
        self._behaviors[(slave, area, address)] = behavior

    def remove(self, slave: int, area: Area, address: int) -> Behavior | None:
        """Сбросить переопределение к дефолту; вернуть сброшенное либо None.

        None — привязка не переопределена (или отсутствует в реестре).
        """
        binding = (slave, area, address)
        prev = self._behaviors.get(binding)
        if prev is None:
            return None
        default = _default_for(slave, area)
        self._behaviors[binding] = default
        return None if prev is default else prev

    def reset(self, slave: int | None = None) -> int:
        """Сбросить переопределения к дефолтам (все или одного слейва).

        Возвращает число сброшенных переопределений (дефолтные привязки не считаются).
        """
        doomed = [
            binding
            for binding, behavior in self._behaviors.items()
            if (slave is None or binding[0] == slave)
            and behavior is not _default_for(binding[0], binding[1])
        ]
        for binding in doomed:
            self._behaviors[binding] = _default_for(binding[0], binding[1])
        return len(doomed)

    def is_locked(self, slave: int, area: Area, address: int) -> bool:
        """True, если привязка существует и заблокирована для внешних записей.

        Дефолты IR/DI — locked=True (генерируются автоматически, инвариант 2).
        """
        behavior = self._behaviors.get((slave, area, address))
        return behavior is not None and behavior.locked

    def items(self) -> Iterator[tuple[Binding, Behavior]]:
        """Итератор по привязкам (дефолты и переопределения): ((slave, area, address), Behavior)."""
        return iter(self._behaviors.items())

    def __len__(self) -> int:
        """Число привязок в реестре (все, включая дефолтные)."""
        return len(self._behaviors)


class WritePolicy(Protocol):
    """Политика внешней записи: проверка окна, при отказе — `ModbusMapError`.

    `source`/`ip` — атрибуция для журнала (по умолчанию — путь Modbus).
    """

    def __call__(
        self,
        device_id: int,
        area: Area,
        address: int,
        values: Sequence[int] | Sequence[bool],
        *,
        source: str = "modbus",
        ip: str | None = None,
    ) -> None: ...


def touched_bindings(device_id: int, area: Area, address: int, count: int) -> list[Binding]:
    """Привязки (value-grid), которые задевает окно сырых регистров/бит записи.

    Для 32-битных форматов (слейвы 2..9) запись в регистр N+1 задевает
    значение по адресу N — окно переводится в value-grid до проверки locked.
    """
    spec = SLAVE_SPECS.get(device_id)
    if spec is None:
        return []
    if area in ("cl", "di") or spec.fmt.registers_per_value == 1:
        return [(device_id, area, a) for a in range(address, address + count)]
    first = address if address % 2 == 1 else address - 1
    last_raw = address + count - 1
    last = last_raw if last_raw % 2 == 1 else last_raw - 1
    return [(device_id, area, a) for a in range(first, last + 1, 2)]


def make_write_policy(registry: BehaviorRegistry, journal: Journal | None = None) -> WritePolicy:
    """Политика внешних записей: locked-привязка → 0x04 + запись в журнал.

    Окно, задевающее хотя бы одну locked-привязку, отклоняется целиком
    (атомарность записи). Движок поведений пишет в карту напрямую, минуя
    политику (ADR-003 п. 3).
    """

    def policy(
        device_id: int,
        area: Area,
        address: int,
        values: Sequence[int] | Sequence[bool],
        *,
        source: str = "modbus",
        ip: str | None = None,
    ) -> None:
        for binding in touched_bindings(device_id, area, address, len(values)):
            if registry.is_locked(*binding):
                if journal is not None:
                    journal.add(
                        JournalEntry(
                            action="write_rejected_locked",
                            slave=device_id,
                            area=area,
                            address=address,
                            value=list(values),
                            source=source,
                            ip=ip,
                        )
                    )
                raise ModbusMapError(
                    f"slave {device_id}: {area} {address} — запись заблокирована поведением",
                    EXC_DEVICE_FAILURE,
                )

    return policy

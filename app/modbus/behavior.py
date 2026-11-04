"""Модель поведения и реестр привязок (slave, area, address) → Behavior.

TASK-201: модель `Behavior` (kind / params / locked) и реестр переопределений.
TASK-205/810: дефолтные поведения slave 1..9 (материализация в реестр, ADR-009).
TASK-241: нормализация value-параметров по формату регистра (`normalize_params`).
TASK-717: тип значения `Behavior.dtype` управляет геометрией пар N, N+1.
Семантика движка — `docs/adr/ADR-003-behavior-engine.md`:

- привязка — value-grid адрес (`SlaveSpec.area_ranges`): для слейвов 2..9
  нечётные 1, 3, …, 1023;
- slave 10 поведений не имеет (его IR вычисляется, см. `slave10`);
- числовые kind'ы работают с hr/ir, дискретные — с cl/di.
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, get_args

from app.modbus.codec import clamp_value
from app.modbus.journal import Journal, JournalEntry
from app.modbus.slave_map import EXC_DEVICE_FAILURE, ModbusMapError
from app.modbus.types import (
    AREAS,
    DTYPE_WIDTH,
    SLAVE_SPECS,
    VALUE_DTYPES,
    Area,
    RegisterFormat,
    ValueDType,
    allowed_dtypes,
    native_dtype,
    wire_format,
)

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
    внешние записи в привязку (0x04 + журнал, ADR-003 п. 3). `dtype` — тип
    значения (TASK-717): None — нативный тип слейва; ширина типа управляет
    геометрией (32-битный тип на slave 1 занимает пару регистров N, N+1).
    """

    kind: BehaviorKind
    params: dict[str, Any] = field(default_factory=dict)
    locked: bool = False
    dtype: ValueDType | None = None

    def __post_init__(self) -> None:
        if self.kind not in BEHAVIOR_KINDS:
            raise ValueError(f"неизвестный kind поведения: {self.kind!r}")
        if not isinstance(self.params, dict):
            raise TypeError(f"params должен быть dict, получено: {type(self.params).__name__}")
        if not isinstance(self.locked, bool):
            raise TypeError(f"locked должен быть bool, получено: {type(self.locked).__name__}")
        if self.dtype is not None and self.dtype not in VALUE_DTYPES:
            raise ValueError(f"неизвестный тип значения: {self.dtype!r}")


def effective_dtype(behavior: Behavior, slave: int) -> ValueDType:
    """Тип значения привязки: явный `dtype` либо нативный тип слейва (TASK-717)."""
    return behavior.dtype if behavior.dtype is not None else native_dtype(SLAVE_SPECS[slave].fmt)


def wire_format_for(behavior: Behavior, slave: int) -> RegisterFormat:
    """Формат на проводе привязки: тип значения × порядок слов слейва (TASK-717)."""
    return wire_format(effective_dtype(behavior, slave), SLAVE_SPECS[slave].fmt)


def binding_width(behavior: Behavior, slave: int, area: Area) -> int:
    """Ширина привязки в регистрах: 2 для 32-битного типа, 1 иначе (TASK-717)."""
    if area in ("cl", "di"):
        return 1
    return DTYPE_WIDTH[effective_dtype(behavior, slave)]


def check_dtype_for_area(dtype: ValueDType | None, slave: int, area: Area) -> None:
    """Тип значения задаётся только для hr/ir; на 32-битной сетке — 32-битный тип.

    cl/di — биты 0/1, тип значения не применим. 16-битная сетка (slave 1)
    принимает все типы; 32-битная (слейвы 2..9) — только uint32/int32/float32.
    """
    if area in ("cl", "di"):
        if dtype is not None:
            raise ValueError(f"для {area} тип значения не задаётся (это биты 0/1)")
        return
    if dtype is None:
        return
    spec_fmt = SLAVE_SPECS[slave].fmt
    if dtype not in allowed_dtypes(spec_fmt):
        allowed = ", ".join(allowed_dtypes(spec_fmt))
        raise ValueError(f"slave {slave}: тип {dtype} не подходит — допустимы {allowed}")


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
    return Behavior(behavior.kind, normalized, locked=behavior.locked, dtype=behavior.dtype)


# --- дефолтные поведения (TASK-205/810, ADR-003 п. 5, ADR-009) --------------------------
#
# Дефолты материализуются в реестре: один экземпляр Behavior-дефолта
# разделяется многими привязками, `set()` подменяет объект, движок замечает
# смену по идентичности. Slave 10 исключён — его IR вычисляются (`slave10`).
#
# Витрина (TASK-810, ADR-009): слоты 2..12 IR каждого слейва 1..9 показывают
# все числовые поведения, DI 1..8 переключаются случайно, последние 8 слотов HR
# несут демо-генераторы (locked, запись даёт 0x04). Остальной HR/CL — constant 0,
# запись разрешена (shared last-write-wins).

#: CL/HR — constant 0; внешние записи разрешены (shared last-write-wins).
_DEFAULT_CONSTANT: Behavior = Behavior("constant", {"value": 0}, locked=False)

#: DI 9..64 — авто-паттерн: бит address−1 счётчика секунд.
_DEFAULT_COUNTER_BITS: Behavior = Behavior("counter_bits", {}, locked=True)

#: DI 1..8 — случайный бит: 0↔1 на каждом тике с вероятностью 0.5 (TASK-810).
_DEFAULT_RANDOM_BIT: Behavior = Behavior("random_bit", {"p": 0.5}, locked=True)

#: IR slave 1 (UINT16): counter step=1 раз в interval_s, locked=True.
#: max = предел формата: иначе clamp_value «залипает» на 65535.
_DEFAULT_IR_COUNTER_16: Behavior = Behavior(
    "counter", {"step": 1, "interval_s": 1, "max": 0xFFFF}, locked=True
)

#: IR slaves 2..9 (uint32/float): counter step=1 раз в interval_s, locked=True.
_DEFAULT_IR_COUNTER_32: Behavior = Behavior(
    "counter", {"step": 1, "interval_s": 1, "max": 0xFFFF_FFFF}, locked=True
)

#: Витрина числовых поведений: IR-слоты 2..12 каждого слейва 1..9 (TASK-810).
#: Слот 1 и слоты 13+ остаются счётчиком по формату.
_IR_SHOWCASE: tuple[Behavior, ...] = (
    Behavior("sawtooth", {"min": 0, "max": 1000, "period_s": 20.0}, locked=True),
    Behavior("triangle", {"min": 0, "max": 1000, "period_s": 30.0}, locked=True),
    Behavior("meander", {"min": 0, "max": 1000, "period_s": 10.0, "duty": 0.5}, locked=True),
    Behavior("sine", {"min": 0, "max": 1000, "period_s": 40.0}, locked=True),
    Behavior("random_uniform", {"min": 0, "max": 1000}, locked=True),
    Behavior("random_walk", {"min": 0, "max": 1000, "step": 10}, locked=True),
    Behavior("step", {"values": [0, 250, 500, 750, 1000], "interval_s": 2.0}, locked=True),
    Behavior("countdown", {"start": 1000, "step": 1, "min": 0, "interval_s": 1.0}, locked=True),
    Behavior(
        "ramp_oneshot", {"from": 0, "to": 1000, "duration_s": 60.0, "then": "loop"}, locked=True
    ),
    Behavior("follow", {"source": "hr:1"}, locked=True),
    Behavior("constant", {"value": 42}, locked=True),
)

#: Демо-поведения последних слотов HR (TASK-810): counter, пила, треугольник,
#: меандр, синус, шум, блуждание, ступеньки. locked — запись даёт 0x04.
_HR_DEMO: tuple[Behavior, ...] = (
    Behavior("counter", {"step": 10, "min": 0, "max": 1000, "interval_s": 1.0}, locked=True),
    Behavior("sawtooth", {"min": 0, "max": 500, "period_s": 15.0}, locked=True),
    Behavior("triangle", {"min": 0, "max": 500, "period_s": 25.0}, locked=True),
    Behavior("meander", {"min": 0, "max": 500, "period_s": 8.0, "duty": 0.25}, locked=True),
    Behavior("sine", {"min": 0, "max": 500, "period_s": 35.0}, locked=True),
    Behavior("random_uniform", {"min": 0, "max": 500}, locked=True),
    Behavior("random_walk", {"min": 0, "max": 500, "step": 5}, locked=True),
    Behavior("step", {"values": [0, 100, 200, 300, 400, 500], "interval_s": 3.0}, locked=True),
)


def _ir_default(fmt: RegisterFormat) -> Behavior:
    """IR-дефолт по формату: max = предел типа значения."""
    return _DEFAULT_IR_COUNTER_16 if fmt.registers_per_value == 1 else _DEFAULT_IR_COUNTER_32


def _value_slot(fmt: RegisterFormat, address: int) -> int:
    """Номер слота значения (с 1): для 32-битных адрес 2k−1 — слот k."""
    return address if fmt.registers_per_value == 1 else (address + 1) // 2


def _hr_demo_bindings() -> dict[Binding, Behavior]:
    """Демо-поведения последних len(_HR_DEMO) слотов HR каждого слейва 1..9."""
    bindings: dict[Binding, Behavior] = {}
    for slave, spec in SLAVE_SPECS.items():
        if slave == 10:
            continue
        addresses = [
            address
            for rng in spec.area_ranges("hr")
            for address in range(rng.start, rng.stop + 1, rng.step)
        ]
        for address, behavior in zip(addresses[-len(_HR_DEMO) :], _HR_DEMO, strict=True):
            bindings[(slave, "hr", address)] = behavior
    return bindings


#: Демо-слоты HR: (slave, "hr", address) → Behavior.
_HR_DEMO_BINDINGS: dict[Binding, Behavior] = _hr_demo_bindings()


def _default_for(slave: int, area: Area, address: int) -> Behavior:
    """Дефолт привязки: витрина IR/HR, случайные биты DI 1..8, остальное — 0."""
    if area == "ir":
        slot = _value_slot(SLAVE_SPECS[slave].fmt, address)
        if 2 <= slot < 2 + len(_IR_SHOWCASE):
            return _IR_SHOWCASE[slot - 2]
        return _ir_default(SLAVE_SPECS[slave].fmt)
    if area == "di":
        return _DEFAULT_RANDOM_BIT if address <= 8 else _DEFAULT_COUNTER_BITS
    demo = _HR_DEMO_BINDINGS.get((slave, area, address))
    return demo if demo is not None else _DEFAULT_CONSTANT


def default_behavior(slave: int, area: Area, address: int) -> Behavior:
    """Дефолтное поведение привязки (ADR-003 п. 5, ADR-009); привязка валидируется."""
    check_binding(slave, area, address)
    return _default_for(slave, area, address)


def _materialize_defaults() -> dict[Binding, Behavior]:
    """Все допустимые привязки слейвов 1..9 → дефолтные поведения (общие экземпляры)."""
    defaults: dict[Binding, Behavior] = {}
    for slave, spec in SLAVE_SPECS.items():
        if slave == 10:
            continue
        for area in AREAS:
            for rng in spec.area_ranges(area):
                for address in range(rng.start, rng.stop + 1, rng.step):
                    defaults[(slave, area, address)] = _default_for(slave, area, address)
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

    def set(
        self,
        slave: int,
        area: Area,
        address: int,
        behavior: Behavior,
        planned: Mapping[Binding, Behavior | None] | None = None,
    ) -> None:
        """Установить поведение; привязка, kind/область и геометрия валидируются.

        Value-параметры приводятся к типу значения привязки (TASK-241/717);
        для cl/di (биты) нормализация не нужна. `planned` — изменения того же
        пакета для атомарной проверки геометрии пар (batch).
        """
        if not isinstance(behavior, Behavior):
            raise TypeError(f"ожидается Behavior, получено: {type(behavior).__name__}")
        check_binding(slave, area, address)
        check_kind_for_area(behavior.kind, area)
        check_dtype_for_area(behavior.dtype, slave, area)
        check_geometry(self, slave, area, address, behavior, planned)
        if area in ("hr", "ir"):
            behavior = normalize_params(behavior, wire_format_for(behavior, slave))
        self._behaviors[(slave, area, address)] = behavior

    def remove(self, slave: int, area: Area, address: int) -> Behavior | None:
        """Сбросить переопределение к дефолту; вернуть сброшенное либо None.

        None — привязка не переопределена (или отсутствует в реестре).
        """
        binding = (slave, area, address)
        prev = self._behaviors.get(binding)
        if prev is None:
            return None
        default = _default_for(slave, area, address)
        self._behaviors[binding] = default
        return None if prev is default else prev

    def reset(self, slave: int | None = None) -> int:
        """Сбросить переопределения к дефолтам (все или одного слейва).

        Возвращает число сброшенных переопределений (дефолтные привязки не считаются).
        """
        doomed = [
            binding
            for binding, behavior in self._behaviors.items()
            if (slave is None or binding[0] == slave) and behavior is not _default_for(*binding)
        ]
        for binding in doomed:
            self._behaviors[binding] = _default_for(*binding)
        return len(doomed)

    def is_locked(self, slave: int, area: Area, address: int) -> bool:
        """True, если привязка существует и заблокирована для внешних записей.

        Дефолты IR/DI и демо-слоты HR — locked=True (генерируются автоматически).
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


def pair_owner(registry: BehaviorRegistry, slave: int, area: Area, address: int) -> int | None:
    """Адрес пары N, N+1, которой принадлежит регистр `address` (TASK-717).

    None — регистр самостоятелен. 32-битный тип привязки N покрывает регистры
    N и N+1: адрес N+1 скрыт и настраивается только через адрес N. На 32-битной
    сетке (слейвы 2..9) пересечений нет.
    """
    if area in ("cl", "di"):
        return None
    if SLAVE_SPECS[slave].fmt.registers_per_value != 1:
        return None
    previous = registry.get(slave, area, address - 1)
    if previous is not None and binding_width(previous, slave, area) == 2:
        return address - 1
    return None


def check_geometry(
    registry: BehaviorRegistry,
    slave: int,
    area: Area,
    address: int,
    behavior: Behavior | None,
    planned: Mapping[Binding, Behavior | None] | None = None,
) -> None:
    """Геометрия пар N, N+1 (TASK-717): тип ширины 2 занимает два регистра.

    - адрес, покрытый парой (младший регистр N+1), не настраивается — его
      строка скрыта, настройка идёт по адресу N;
    - 32-битный тип требует свободный младший регистр: если N+1 переопределён,
      сначала сбросьте его к дефолту;
    - пара не должна выходить за карту регистров слейва.

    `planned` — изменения того же пакета: (slave, area, address) → Behavior
    либо None (сброс к дефолту); атомарная проверка пакета учитывает элементы
    друг друга. `behavior=None` (сброс) допустим везде.
    """
    if behavior is None:
        return
    planned = planned or {}

    def width_at(target: Binding) -> int:
        if target in planned:
            override = planned[target]
            if override is None:
                return binding_width(default_behavior(*target), target[0], target[1])
            return binding_width(override, target[0], target[1])
        existing = registry.get(*target)
        return binding_width(existing, target[0], target[1]) if existing is not None else 1

    def is_override_at(target: Binding) -> bool:
        if target in planned:
            return planned[target] is not None
        existing = registry.get(*target)
        return existing is not None and existing is not default_behavior(*target)

    previous = (slave, area, address - 1)
    if width_at(previous) == 2:
        raise ValueError(
            f"slave {slave}: {area} {address} — младший регистр пары "
            f"{address - 1}, {address}; настройка идёт по адресу {address - 1}"
        )
    if binding_width(behavior, slave, area) != 2:
        return
    spec = SLAVE_SPECS[slave]
    if not any((address + 1) in rng for rng in spec.register_ranges(area)):
        raise ValueError(
            f"slave {slave}: {area} {address}: 32-битный тип требует пару регистров "
            f"{address}, {address + 1}, но {address + 1} вне карты регистров"
        )
    neighbor = (slave, area, address + 1)
    if is_override_at(neighbor):
        raise ValueError(
            f"slave {slave}: {area} {address + 1} настроен и будет скрыт парой "
            f"{address}, {address + 1} — сначала сбросьте его к дефолту"
        )


def touched_bindings(
    registry: BehaviorRegistry | None,
    device_id: int,
    area: Area,
    address: int,
    count: int,
) -> list[Binding]:
    """Привязки (value-grid), которые задевает окно сырых регистров/бит записи.

    Для 32-битных форматов (слейвы 2..9) запись в регистр N+1 задевает
    значение по адресу N; на slave 1 запись в младший регистр пары N, N+1
    задевает значение пары (нужен `registry`; без него каждое слово — своя
    привязка). Окно переводится в value-grid до проверки locked.
    """
    spec = SLAVE_SPECS.get(device_id)
    if spec is None:
        return []
    if area in ("cl", "di"):
        return [(device_id, area, a) for a in range(address, address + count)]
    if spec.fmt.registers_per_value == 1:
        bindings: list[Binding] = []
        for raw in range(address, address + count):
            owner = (
                pair_owner(registry, device_id, area, raw) if registry is not None else None
            )
            binding = (device_id, area, owner if owner is not None else raw)
            if binding not in bindings:
                bindings.append(binding)
        return bindings
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
        for binding in touched_bindings(registry, device_id, area, address, len(values)):
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

"""HTML-страницы UI: shell + страницы разделов (Эпик 7, ADR-008).

Shell — общий каркас всех страниц: навигация по разделам, обязательная плашка
`WARNING_BANNER` (ADR-001 п. 4, TASK-504), подключение статики `/static`.
Страницы рендерятся сервером (f-строки), живые данные подтягивает JS из REST
и WS. «Обзор» (TASK-701) — параметры подключения и таблица форматов из
`SLAVE_SPECS`; остальные разделы наполняются задачами 7.2–7.6.
"""
from __future__ import annotations

import json
from html import escape

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.api.routes import ServicesDep
from app.modbus.slave10 import ZONE_COUNT, zone_offset_hours
from app.modbus.types import AREAS, SLAVE_SPECS, AddressRange, RegisterFormat

pages_router = APIRouter()

#: Обязательный текст плашки (TASK-504): многопользовательский режим и перезапись.
WARNING_BANNER = (
    "Публичный многопользовательский сервер. Значения регистров HR/CL общие для "
    "всех подключений (shared last-write-wins) и в любой момент могут быть "
    "перезаписаны другим пользователем. Настройки поведений тоже общие. "
    "Состояние хранится только в памяти: перезапуск сервера сбрасывает все "
    "значения. Аутентификации нет, действуют лимиты (число соединений с одного "
    "IP, частота записей)."
)

#: Разделы навигации (ADR-008 п. 2): (URL, заголовок).
NAV_SECTIONS: tuple[tuple[str, str], ...] = (
    ("/", "Обзор"),
    ("/registers", "Карта регистров"),
    ("/behaviors", "Поведения"),
    ("/timezones", "Часовые пояса"),
    ("/injections", "Инъекции"),
    ("/tools", "Инструменты"),
    ("/docs", "Документация"),
)


def _fmt_label(fmt: RegisterFormat) -> str:
    """Человекочитаемый формат регистра: «uint32 ABCD», «float DCBA»."""
    if fmt is RegisterFormat.UINT16:
        return "uint16"
    type_name, order = fmt.value.split("_", 1)
    return f"{type_name} {order.upper()}"


def _range_label(ranges: tuple[AddressRange, ...]) -> str:
    """Диапазоны адресов: «1..1024», «1,3,…,1023» (шаг 2); нет адресов — «—»."""
    if not ranges:
        return "—"
    parts: list[str] = []
    for rng in ranges:
        if rng.step == 1:
            parts.append(f"{rng.start}..{rng.stop}")
        else:
            parts.append(f"{rng.start},{rng.start + rng.step},…,{rng.stop}")
    return ", ".join(parts)


def _formats_table() -> str:
    """Таблица форматов и адресов по unit ID 1..10 (TASK-701, HRIR_SPEC)."""
    rows = []
    for slave_id, spec in SLAVE_SPECS.items():
        rows.append(
            "<tr>"
            f"<td>{slave_id}</td>"
            f"<td><code>{_fmt_label(spec.fmt)}</code></td>"
            f"<td>{_range_label(spec.hr)}</td>"
            f"<td>{_range_label(spec.ir)}</td>"
            f"<td>{_range_label(spec.cl)}</td>"
            f"<td>{_range_label(spec.di)}</td>"
            "</tr>"
        )
    return (
        '<table class="formats"><thead><tr>'
        "<th>Unit ID</th><th>Формат HR/IR</th><th>HR</th><th>IR</th><th>CL</th><th>DI</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


#: Панель «Проверить соединение» (TASK-702). Обычная строка (без f-префикса):
#: фигурные скобки JS остаются литеральными.
_PING_PANEL = """
<h2>Проверить соединение</h2>
<p>Сервер делает 5 контрольных чтений выбранного slave через Modbus-TCP
(loopback) и показывает RTT — так проверяются TCP, протокол и карта регистров
сразу. Проба ничего не пишет и не попадает в журнал.</p>
<p>
  <label>Unit ID
    <select id="ping-slave">
      <option>1</option><option>2</option><option>3</option><option>4</option>
      <option>5</option><option>6</option><option>7</option><option>8</option>
      <option>9</option><option>10</option>
    </select>
  </label>
  <button id="ping-run" type="button">Проверить соединение</button>
</p>
<p id="ping-result" role="status"></p>
<script>
document.getElementById("ping-run").addEventListener("click", async function () {
  var result = document.getElementById("ping-result");
  var slave = document.getElementById("ping-slave").value;
  result.textContent = "Проба…";
  try {
    var response = await fetch("/api/modbus/ping", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({slave: Number(slave), count: 5})
    });
    var body = await response.json();
    if (!response.ok) {
      result.textContent = "Ошибка " + response.status + ": " + (body.detail || "");
      return;
    }
    var text = "Успехов: " + body.ok + ", ошибок: " + body.errors + ".";
    if (body.ok > 0) {
      text += " RTT min/avg/max: "
        + body.rtt_min_ms.toFixed(2) + " / "
        + body.rtt_avg_ms.toFixed(2) + " / "
        + body.rtt_max_ms.toFixed(2) + " мс.";
    }
    if (body.last_error) {
      text += " Последняя ошибка: " + body.last_error;
    }
    result.textContent = text;
  } catch (error) {
    result.textContent = "Ошибка: " + error;
  }
});
</script>
"""


#: Панель «Счётчики сервера» (TASK-703). Обычная строка (без f-префикса):
#: фигурные скобки JS остаются литеральными.
_METRICS_PANEL = """
<h2>Счётчики сервера</h2>
<dl>
  <dt>Активные соединения</dt><dd id="metrics-conn">—</dd>
  <dt>Запросы / с</dt><dd id="metrics-req">—</dd>
  <dt>Ошибки / с</dt><dd id="metrics-err">—</dd>
  <dt>Всего запросов</dt><dd id="metrics-req-total">—</dd>
  <dt>Всего ошибок</dt><dd id="metrics-err-total">—</dd>
  <dt>Uptime</dt><dd id="metrics-uptime">—</dd>
</dl>
<p class="note">Скорости — усреднение за скользящее окно; ошибками считаются
ответы с exception-кодом и запросы, поглощённые timeout-инъекциями.</p>
<script>
async function refreshMetrics() {
  try {
    var response = await fetch("/api/modbus/metrics");
    if (!response.ok) return;
    var body = await response.json();
    document.getElementById("metrics-conn").textContent = body.active_connections;
    document.getElementById("metrics-req").textContent = body.req_per_s.toFixed(2);
    document.getElementById("metrics-err").textContent = body.err_per_s.toFixed(2);
    document.getElementById("metrics-req-total").textContent = body.requests_total;
    document.getElementById("metrics-err-total").textContent = body.errors_total;
    var uptime = Math.floor(body.uptime_s);
    var h = Math.floor(uptime / 3600);
    var m = Math.floor((uptime % 3600) / 60);
    var s = uptime % 60;
    document.getElementById("metrics-uptime").textContent = h + " ч " + m + " мин " + s + " с";
  } catch (error) {
    /* сбой поллинга — следующий тик повторит запрос */
  }
}
refreshMetrics();
setInterval(refreshMetrics, 1000);
</script>
"""


def _index_body(host: str, port: int) -> str:
    return f"""
<h2>Как подключиться</h2>
<dl>
  <dt>Хост</dt><dd><code>{escape(host)}</code></dd>
  <dt>Порт</dt><dd><code>{port}</code></dd>
  <dt>Unit ID</dt><dd><code>1..10</code> (slave 10 — только input registers)</dd>
  <dt>Функции</dt><dd><code>1, 2, 3, 4, 5, 6, 16</code> — остальные отклоняются</dd>
</dl>
{_PING_PANEL}
{_METRICS_PANEL}

<h2>Форматы данных по слейвам</h2>
{_formats_table()}
<p class="note">Slave 10: IR 300..301 — Unix timestamp (<code>uint32 ABCD</code>),
остальной IR — <code>uint16</code>; метрики часовых поясов — в разделе
«Часовые пояса». Для 32-битных форматов значение занимает два регистра
(адреса значений 1, 3, …, 1023).</p>

<p>Live-состояние регистров, настройка поведений, инъекции ошибок и журнал —
в разделах выше.</p>
"""


def _stub_body(title: str) -> str:
    return f"<h2>{escape(title)}</h2><p>Раздел в разработке.</p>"


def _tools_body() -> str:
    """Инструменты наладчика: конвертер байт/слов (TASK-741) + генератор конфигов (TASK-742).

    Конвертер: значение ↔ пара регистров во всех 4 порядках (ABCD/CDAB/BADC/DCBA)
    для uint32 и float; пересчёт — серверный (`POST /api/modbus/convert`, тот же
    кодек, что и у слейвов 2..9). Генератор: готовые настройки/скрипты/flow для
    Modbus Poll, QModMaster, CAS Scanner, pyModbus и Node-RED
    (`POST /api/modbus/configgen`). Наполняют `static/tools.js` и
    `static/configgen.js`.
    """
    slave_options = "".join(f"<option>{slave_id}</option>" for slave_id in SLAVE_SPECS)
    return (
        "<h2>Конвертер байт/слов</h2>\n"
        '<p class="note">Показывает, в какие два регистра кодируется значение и '
        "какое значение читается из двух регистров — во всех четырёх порядках: "
        "<code>ABCD</code>, <code>CDAB</code>, <code>BADC</code>, <code>DCBA</code>. "
        "Пересчёт выполняет сервер тем же кодеком, что и слейвы 2..9 "
        "(<code>docs/HRIR_SPEC.md</code>).</p>\n"
        "<p>\n"
        '  <label>Тип <select id="tool-kind">\n'
        '    <option value="uint32">uint32</option>\n'
        '    <option value="float">float</option>\n'
        "  </select></label>\n"
        '  <label>Что вводим <select id="tool-source">\n'
        '    <option value="value">Значение</option>\n'
        '    <option value="regs">Два регистра</option>\n'
        "  </select></label>\n"
        "</p>\n"
        "<p>\n"
        '  <label>Значение <input id="tool-value" type="text" '
        'placeholder="например 3.5 или 0x12345678"></label>\n'
        '  <label>Регистр 1 <input id="tool-reg-hi" type="text" placeholder="0x1234"></label>\n'
        '  <label>Регистр 2 <input id="tool-reg-lo" type="text" placeholder="0x5678"></label>\n'
        "</p>\n"
        "<p>\n"
        '  <button id="tool-convert" type="button">Конвертировать</button>\n'
        '  <span id="tool-status" role="status"></span>\n'
        "</p>\n"
        '<table class="values">\n'
        "  <thead><tr><th>Порядок</th><th>Регистры (hex)</th><th>Регистры (dec)</th>"
        "<th>Значение</th></tr></thead>\n"
        '  <tbody id="tool-rows"></tbody>\n'
        "</table>\n"
        "\n"
        "<h2>Генератор конфигов</h2>\n"
        '<p class="note">Готовые настройки для Modbus Poll, QModMaster, '
        "CAS Modbus Scanner, скрипт для pyModbus и flow для Node-RED: "
        "подключение, функция, адрес и формат данных выбранного слейва. "
        "Адрес — из <code>docs/HRIR_SPEC.md</code>, без пересчёта; количество — "
        "в значениях (для 32-битных слейвов значение занимает 2 регистра).</p>\n"
        "<p>\n"
        '  <label>Инструмент <select id="cfg-tool">\n'
        '    <option value="modbus_poll">Modbus Poll</option>\n'
        '    <option value="qmodmaster">QModMaster</option>\n'
        '    <option value="cas_scanner">CAS Modbus Scanner</option>\n'
        '    <option value="pymodbus">pyModbus (скрипт)</option>\n'
        '    <option value="nodered">Node-RED (flow)</option>\n'
        "  </select></label>\n"
        f'  <label>Unit ID <select id="cfg-slave">{slave_options}</select></label>\n'
        '  <label>Область <select id="cfg-area">\n'
        '    <option value="hr">hr — Holding Registers</option>\n'
        '    <option value="ir">ir — Input Registers</option>\n'
        '    <option value="cl">cl — Coils</option>\n'
        '    <option value="di">di — Discrete Inputs</option>\n'
        "  </select></label>\n"
        '  <label>Адрес <input id="cfg-address" type="text" value="1"></label>\n'
        '  <label>Значений <input id="cfg-count" type="text" value="1"></label>\n'
        "</p>\n"
        "<p>\n"
        '  <button id="cfg-generate" type="button">Сгенерировать</button>\n'
        '  <button id="cfg-copy" type="button">Копировать</button>\n'
        '  <a id="cfg-download" href="#" download>Скачать</a>\n'
        '  <span id="cfg-status" role="status"></span>\n'
        "</p>\n"
        '<pre id="cfg-output"></pre>\n'
        '<script type="module" src="/static/tools.js"></script>\n'
        '<script type="module" src="/static/configgen.js"></script>'
    )


def _register_map_json() -> str:
    """Геометрия карты для JS (TASK-711): slave → fmt + диапазоны областей.

    Диапазоны — тройки `[start, stop, step]` сетки значений (1,3,…,1023 для
    32-битных форматов), из `SLAVE_SPECS` — единственный источник истины.
    """
    data: dict[str, dict[str, object]] = {}
    for slave_id, spec in SLAVE_SPECS.items():
        areas: dict[str, list[list[int]]] = {
            area: [[rng.start, rng.stop, rng.step] for rng in spec.area_ranges(area)]
            for area in AREAS
            if spec.area_ranges(area)
        }
        data[str(slave_id)] = {"fmt": spec.fmt.value, "areas": areas}
    return json.dumps(data)


def _registers_body() -> str:
    """Каркас «Карты регистров» (TASK-711): выбор слейва/области, таблица, пагинация.

    Значения наполняет `static/registers.js` (WS `/ws/state` + REST-фолбэк),
    поэтому body собирается конкатенацией: JSON геометрии содержит фигурные
    скобки, f-строки тут только мешали бы.
    """
    slave_options = "".join(f"<option>{slave_id}</option>" for slave_id in SLAVE_SPECS)
    return (
        "<h2>Карта регистров</h2>\n"
        '<p class="note">Значения живые (WS <code>/ws/state</code>), общие для всех '
        "подключений: запись любого пользователя сразу видна остальным (shared "
        "last-write-wins).</p>\n"
        '<div class="banner" role="status">⚠ Редактирование CL/HR доступно прямо в '
        "таблице (клик по значению). Значения общие: запись применяется по правилу "
        "shared last-write-wins и в любой момент может быть перезаписана другим "
        "пользователем.</div>\n"
        "<p>\n"
        f'  <label>Unit ID <select id="reg-slave">{slave_options}</select></label>\n'
        '  <label>Область <select id="reg-area"></select></label>\n'
        '  <label>Формат <select id="reg-format"></select></label>\n'
        '  <span class="note" id="reg-fmt"></span>\n'
        "</p>\n"
        '<table class="values">\n'
        "  <thead><tr><th>Адрес</th><th>Значение</th><th></th></tr></thead>\n"
        '  <tbody id="reg-body"></tbody>\n'
        "</table>\n"
        '<p id="reg-status" role="status"></p>\n'
        "<p>\n"
        '  <button id="reg-prev" type="button">← Назад</button>\n'
        '  <span id="reg-page" role="status"></span>\n'
        '  <button id="reg-next" type="button">Вперёд →</button>\n'
        "</p>\n"
        "<h2>Избранное</h2>\n"
        '<p class="note">Watch list регистров под наблюдением хранится в вашем браузере '
        "(localStorage) и сохраняется между визитами. Добавить — кнопкой ★ в таблице "
        "выше; значения обновляются по WS-подписке.</p>\n"
        '<table class="values">\n'
        "  <thead><tr><th>Unit ID</th><th>Область</th><th>Адрес</th><th>Значение</th>"
        "<th></th></tr></thead>\n"
        '  <tbody id="watch-body"></tbody>\n'
        "</table>\n"
        "<p>\n"
        '  <button id="watch-clear" type="button">Очистить список</button>\n'
        '  <span id="watch-status" role="status"></span>\n'
        "</p>\n"
        "<h2>График</h2>\n"
        "<p>\n"
        "  <label>Окно\n"
        '    <select id="chart-window">\n'
        "      <option>60</option><option>120</option><option>300</option>\n"
        "    </select>\n"
        "  </label> с — live-график регистров из избранного\n"
        "</p>\n"
        '<canvas id="chart-canvas" width="800" height="240"></canvas>\n'
        '<script type="module" src="/static/chart.js"></script>\n'
        "<script>window.REGISTER_MAP = " + _register_map_json() + ";</script>\n"
        '<script type="module" src="/static/registers.js"></script>\n'
        '<script type="module" src="/static/watchlist.js"></script>'
    )


def _behaviors_body() -> str:
    """Каркас «Поведений» (TASK-721…723): форма привязки и таблица переопределений.

    Форма: slave 1..9 (slave 10 поведений не имеет), область, адрес/диапазон,
    kind (фильтруется по области: числовые — hr/ir, дискретные — cl/di),
    параметры по kind и `locked`. Пресеты (TASK-722) заполняют форму одним
    нажатием. Наполняет `static/behaviors.js`: одна привязка — PUT
    `/api/modbus/behaviors/{slave}/{area}/{address}`, диапазон
    («Применить к диапазону» / «Сбросить») — POST `/api/modbus/behaviors/batch`,
    список переопределений — GET `/api/modbus/behaviors?only_overrides=true`.
    """
    slave_options = "".join(f"<option>{slave_id}</option>" for slave_id in range(1, 10))
    area_options = "".join(f'<option value="{area}">{area}</option>' for area in AREAS)
    return (
        "<h2>Поведения</h2>\n"
        '<p class="note">Поведение задаёт, как значение регистра/бита меняется во '
        "времени (счётчик, пила, шум…). Привязки общие для всех пользователей: "
        "поведение, установленное вами, сразу действует для остальных. Slave 10 "
        "поведений не имеет — его IR вычисляются (часовые пояса и Unix "
        "timestamp).</p>\n"
        "<p>\n"
        f'  <label>Unit ID <select id="bh-slave">{slave_options}</select></label>\n'
        f'  <label>Область <select id="bh-area">{area_options}</select></label>\n'
        '  <label>Адрес от <input id="bh-address" type="number" min="1" value="1"></label>\n'
        '  <label>Адрес по <input id="bh-address-to" type="number" min="1" value="1"></label>\n'
        "</p>\n"
        "<h2>Пресеты</h2>\n"
        '<p class="note">Быстрая настройка формы ниже: заполняет поведение и '
        "параметры. Пресеты работают для числовых областей <code>hr</code>/<code>ir</code>; "
        "«Сброс» — константа 0.</p>\n"
        '<p id="bh-presets">\n'
        '  <button type="button" data-preset="counter">Счётчик</button>\n'
        '  <button type="button" data-preset="sawtooth">Пила</button>\n'
        '  <button type="button" data-preset="triangle">Треугольник</button>\n'
        '  <button type="button" data-preset="meander">Меандр</button>\n'
        '  <button type="button" data-preset="noise">Шум</button>\n'
        '  <button type="button" data-preset="sine">Синус</button>\n'
        '  <button type="button" data-preset="step">Ступеньки</button>\n'
        '  <button type="button" data-preset="reset">Сброс</button>\n'
        "</p>\n"
        "<p>\n"
        '  <label>Поведение <select id="bh-kind"></select></label>\n'
        '  <label><input id="bh-locked" type="checkbox"> Заблокировать '
        "(<code>locked</code>: внешние записи игнорируются)</label>\n"
        "</p>\n"
        '<div id="bh-params"></div>\n'
        "<p>\n"
        '  <button id="bh-apply" type="button">Установить</button>\n'
        '  <button id="bh-apply-range" type="button">Применить к диапазону</button>\n'
        '  <button id="bh-reset-range" type="button">Сбросить</button>\n'
        '  <span id="bh-status" role="status"></span>\n'
        "</p>\n"
        "<h2>Текущие переопределения</h2>\n"
        '<p class="note">Привязки, отличающиеся от поведений по умолчанию '
        "(HR/CL — константа 0, IR — счётчик, DI — биты счётчика секунд).</p>\n"
        "<p>\n"
        '  <button id="bh-refresh" type="button">Обновить</button>\n'
        "</p>\n"
        '<table class="values">\n'
        "  <thead><tr><th>Unit ID</th><th>Область</th><th>Адрес</th><th>Поведение</th>"
        "<th>Параметры</th><th>Locked</th></tr></thead>\n"
        '  <tbody id="bh-list"></tbody>\n'
        "</table>\n"
        "<h2>Мини-график</h2>\n"
        '<p class="note">Значение выбранного регистра (Unit ID / Область / Адрес '
        "выше) за последние 60 с; подписка по WS <code>/ws/state</code>.</p>\n"
        '<canvas id="bh-chart" width="800" height="160"></canvas>\n'
        "<script>window.REGISTER_MAP = " + _register_map_json() + ";</script>\n"
        '<script type="module" src="/static/behaviors.js"></script>\n'
        '<script type="module" src="/static/minichart.js"></script>'
    )


def _tz_cards() -> str:
    """27 карточек зон Z=0..26 (TASK-731): смещение UTC + поля даты/времени.

    Значения наполняет `static/timezones.js` из IR slave 10 (блок зоны:
    год, месяц, день, час, минута, секунда, день недели, смещение).
    """
    cards = []
    for zone in range(ZONE_COUNT):
        offset = zone_offset_hours(zone)
        sign = "+" if offset >= 0 else "−"
        cards.append(
            f'<div class="zone-card">'
            f'<h3>Z={zone} · UTC{sign}{abs(offset):02d}:00</h3>'
            f'<dl>'
            f'<dt>Дата</dt><dd id="tz-{zone}-date">—</dd>'
            f'<dt>Время</dt><dd id="tz-{zone}-time">—</dd>'
            f'<dt>День недели</dt><dd id="tz-{zone}-weekday">—</dd>'
            f'<dt>Смещение</dt><dd id="tz-{zone}-offset">—</dd>'
            f"</dl></div>"
        )
    return '<div class="zone-grid">' + "".join(cards) + "</div>"


def _timezones_body() -> str:
    """Страница «Часовые пояса» (TASK-731…732): 27 зон + Unix timestamp.

    Slave 10 отдаёт вычисляемые IR: блоки зон 1..268 и Unix timestamp
    (IR 300..301, `uint32 ABCD`). Расхождение — разница серверного timestamp
    с системным временем браузера.
    """
    return (
        "<h2>Часовые пояса (slave 10)</h2>\n"
        '<p class="note">Каждая зона Z=0..26 — блок из 8 метрик в IR slave 10 '
        "(дата, время, день недели, смещение UTC); метрики вычисляются сервером "
        "при чтении. Z=0..14 — UTC+00..UTC+14, Z=15..26 — UTC−01..UTC−12. "
        "Значения живые (WS <code>/ws/state</code>).</p>\n"
        "<h2>Unix timestamp</h2>\n"
        "<dl>\n"
        '  <dt>IR 300..301 (сервер)</dt><dd id="tz-unix">—</dd>\n'
        '  <dt>Системное время</dt><dd id="tz-system">—</dd>\n'
        '  <dt>Расхождение</dt><dd id="tz-delta">—</dd>\n'
        "</dl>\n"
        '<p class="note">IR 300..301 — Unix timestamp (<code>uint32 ABCD</code>, '
        "точность ±0.5 с); расхождение — разница серверного timestamp с "
        "системным временем вашего браузера.</p>\n"
        + _tz_cards()
        + '\n<script type="module" src="/static/timezones.js"></script>'
    )


#: Панель «Инъекции ошибок» с кнопкой сброса (TASK-605). Обычная строка (без
#: f-префикса): фигурные скобки JS остаются литеральными.
_INJECTIONS_PANEL = """
<p>Инъекции (exception / delay / timeout) регистрируются через REST
<code>POST /api/modbus/inject</code> и действуют не дольше
<code>INJECTION_MAX_TTL_S</code>. Кнопка снимает все активные инъекции:</p>
<p><button id="clear-injections" type="button">Сбросить инъекции</button></p>
<p id="clear-result" role="status"></p>
<script>
document.getElementById("clear-injections").addEventListener("click", async function () {
  var result = document.getElementById("clear-result");
  result.textContent = "Снятие…";
  try {
    var response = await fetch("/api/modbus/inject/clear", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: "{}"
    });
    var body = await response.json();
    result.textContent = response.ok
      ? "Снято инъекций: " + body.cleared
      : "Ошибка " + response.status;
  } catch (error) {
    result.textContent = "Ошибка: " + error;
  }
});
</script>
"""


def _shell(title: str, active: str, body: str) -> str:
    nav = "".join(
        f'<a href="{href}" class="nav-link{" active" if href == active else ""}">{label}</a>'
        for href, label in NAV_SECTIONS
    )
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} — Публичный тестовый Modbus-TCP сервер</title>
<link rel="stylesheet" href="/static/app.css">
</head>
<body>
<header>
<h1>Публичный тестовый Modbus-TCP сервер</h1>
<p class="subtitle">modbustoolkit.electroman.by</p>
<nav>{nav}</nav>
</header>
<main>
<div class="banner" role="alert">
  <strong>⚠ {escape(WARNING_BANNER)}</strong>
</div>
{body}
</main>
</body>
</html>
"""


@pages_router.get("/", response_class=HTMLResponse)
def get_index(services: ServicesDep) -> HTMLResponse:
    """Обзор: параметры подключения и таблица форматов (TASK-701)."""
    config = services.config
    body = _index_body(config.MODBUS_TCP_HOST, config.MODBUS_TCP_PORT)
    return HTMLResponse(_shell("Обзор", "/", body))


@pages_router.get("/registers", response_class=HTMLResponse)
def get_registers() -> HTMLResponse:
    """Карта регистров: live-таблица адресов по слейву (TASK-711…715)."""
    return HTMLResponse(_shell("Карта регистров", "/registers", _registers_body()))


@pages_router.get("/behaviors", response_class=HTMLResponse)
def get_behaviors_page() -> HTMLResponse:
    """Поведения: форма привязки и таблица переопределений (TASK-721)."""
    return HTMLResponse(_shell("Поведения", "/behaviors", _behaviors_body()))


@pages_router.get("/timezones", response_class=HTMLResponse)
def get_timezones() -> HTMLResponse:
    """Часовые пояса, slave 10: 27 зон + Unix timestamp (TASK-731…732)."""
    return HTMLResponse(_shell("Часовые пояса", "/timezones", _timezones_body()))


@pages_router.get("/injections", response_class=HTMLResponse)
def get_injections_page() -> HTMLResponse:
    """Инъекции ошибок: кнопка сброса (TASK-605); полная страница — TASK-743."""
    body = f"<h2>Инъекции ошибок</h2>{_INJECTIONS_PANEL}"
    return HTMLResponse(_shell("Инъекции", "/injections", body))


@pages_router.get("/tools", response_class=HTMLResponse)
def get_tools() -> HTMLResponse:
    """Инструменты наладчика: конвертер байт/слов (TASK-741) и генератор конфигов (TASK-742)."""
    return HTMLResponse(_shell("Инструменты", "/tools", _tools_body()))


@pages_router.get("/docs", response_class=HTMLResponse)
def get_docs() -> HTMLResponse:
    """Документация (TASK-751…752) — заглушка до наполнения."""
    return HTMLResponse(_shell("Документация", "/docs", _stub_body("Документация")))

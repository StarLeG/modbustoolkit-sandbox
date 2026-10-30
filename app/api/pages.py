"""HTML-страницы UI: shell + страницы разделов (Эпик 7, ADR-008).

Shell - общий каркас в фирменном стиле modbustoolkit: шапка с брендом
`MODBUS·TOOLKIT`, навигация по разделам, обязательная плашка `WARNING_BANNER`
(ADR-001 п. 4, TASK-504), блок поддержки проекта и футер с промо-ссылками на
продукт. Тексты RU/EN идут через `t()`, язык выбирается запросом или cookie.
Страницы рендерятся сервером (f-строки), живые данные подтягивает JS из REST
и WS. «Обзор» (TASK-701) - параметры подключения и таблица форматов из
`SLAVE_SPECS`; остальные разделы - задачи 7.2-7.6.
"""
from __future__ import annotations

import json
from html import escape
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.api.i18n import COOKIE_NAME, Lang, lang_links, resolve_lang, t
from app.api.routes import ServicesDep
from app.modbus.config import Config
from app.modbus.slave10 import ZONE_COUNT, zone_offset_hours
from app.modbus.types import AREAS, SLAVE_SPECS, AddressRange, RegisterFormat

pages_router = APIRouter()

#: Обязательный текст плашки (TASK-504): многопользовательский режим и перезапись.
WARNING_BANNER = (
    "Публичный многопользовательский сервер. Значения регистров HR/CL общие для "
    "всех подключений (shared last-write-wins) и в любой момент могут быть "
    "перезаписаны другим пользователем. Настройки поведений тоже общие. "
    "Состояние живёт только в памяти. Перезапуск сервера сбрасывает все "
    "значения. Аутентификации нет, действуют лимиты на число соединений с "
    "одного IP и частоту записей."
)
WARNING_BANNER_EN = (
    "Public multi-user server. HR/CL register values are shared across all "
    "connections (shared last-write-wins) and can be overwritten by another "
    "user at any time. Behavior settings are shared too. State lives in memory "
    "only. A server restart resets all values. There is no authentication. "
    "Limits apply to connection counts per IP and write rates."
)

#: Разделы навигации (ADR-008 п. 2): (URL, заголовок RU, заголовок EN).
NAV_SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("/", "Обзор", "Overview"),
    ("/registers", "Карта регистров", "Register map"),
    ("/behaviors", "Поведения", "Behaviors"),
    ("/timezones", "Часовые пояса", "Time zones"),
    ("/injections", "Инъекции", "Injections"),
    ("/journal", "Журнал", "Journal"),
    ("/tools", "Инструменты", "Tools"),
    ("/docs", "Документация", "Docs"),
)

#: Ссылки продукта modbustoolkit (промо в футере и примерах).
PRODUCT_URL = "https://modbustoolkit.electroman.by/"
PRODUCT_DOCS_URL = "https://modbustoolkit.electroman.by/docs.html"
SUPPORT_URL = "https://modbustoolkit.electroman.by/support.html"
TELEGRAM_URL = "https://t.me/modbustoolkit"

#: Публичная точка подключения, которая фигурирует в примерах.
PUBLIC_ENDPOINT = "modbustoolkit.electroman.by:5020"


def _project_version() -> str:
    """Версия проекта из метаданных пакета (источник - pyproject.toml)."""
    try:
        return package_version("modbus-test-server")
    except PackageNotFoundError:
        return "dev"


def _fmt_label(fmt: RegisterFormat) -> str:
    """Человекочитаемый формат регистра: «uint32 ABCD», «float DCBA»."""
    if fmt is RegisterFormat.UINT16:
        return "uint16"
    type_name, order = fmt.value.split("_", 1)
    return f"{type_name} {order.upper()}"


def _range_label(ranges: tuple[AddressRange, ...]) -> str:
    """Диапазоны адресов: «1..1024», «1,3,…,1023» (шаг 2); нет адресов - прочерк."""
    if not ranges:
        return "-"
    parts: list[str] = []
    for rng in ranges:
        if rng.step == 1:
            parts.append(f"{rng.start}..{rng.stop}")
        else:
            parts.append(f"{rng.start},{rng.start + rng.step},…,{rng.stop}")
    return ", ".join(parts)


def _formats_table(lang: Lang) -> str:
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
        f"<th>Unit ID</th><th>{t(lang, 'Формат HR/IR', 'HR/IR format')}</th>"
        "<th>HR</th><th>IR</th><th>CL</th><th>DI</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


#: JS панели пробы (TASK-702): обычная строка, фигурные скобки остаются
#: литеральными; строки интерфейса идут через window.t (i18n.js).
_PING_JS = """\
document.getElementById("ping-run").addEventListener("click", async function () {
  var result = document.getElementById("ping-result");
  var slave = document.getElementById("ping-slave").value;
  result.textContent = window.t("Проба…");
  try {
    var response = await fetch("/api/modbus/ping", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({slave: Number(slave), count: 5})
    });
    var body = await response.json();
    if (!response.ok) {
      result.textContent = window.t("Ошибка {status}: {detail}", {status: response.status, detail: body.detail || ""});
      return;
    }
    var text = window.t("Успехов: {ok}, ошибок: {errors}.", {ok: body.ok, errors: body.errors});
    if (body.ok > 0) {
      text += window.t(" RTT min/avg/max: {min} / {avg} / {max} мс.", {
        min: body.rtt_min_ms.toFixed(2),
        avg: body.rtt_avg_ms.toFixed(2),
        max: body.rtt_max_ms.toFixed(2)
      });
    }
    if (body.last_error) {
      text += window.t(" Последняя ошибка: {text}", {text: body.last_error});
    }
    result.textContent = text;
  } catch (error) {
    result.textContent = window.t("Ошибка: {text}", {text: error});
  }
});
"""

#: JS панели счётчиков (TASK-703).
_METRICS_JS = """\
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
    document.getElementById("metrics-uptime").textContent =
      window.t("{h} ч {m} мин {s} с", {h: h, m: m, s: s});
  } catch (error) {
    /* сбой поллинга: следующий тик повторит запрос */
  }
}
refreshMetrics();
setInterval(refreshMetrics, 1000);
"""

_PING_DESC_RU = (
    "Сервер делает 5 контрольных чтений выбранного slave через Modbus-TCP "
    "(loopback) и показывает RTT. Заодно видно, в порядке ли TCP, протокол и "
    "карта регистров. Проба ничего не пишет и не попадает в журнал."
)
_PING_DESC_EN = (
    "The server runs 5 control reads of the selected slave over Modbus-TCP "
    "(loopback) and reports RTT. This also checks TCP, the protocol and the "
    "register map. The probe writes nothing and never hits the journal."
)

_METRICS_NOTE_RU = (
    "Скорости усреднены по скользящему окну. Ошибками считаются ответы с "
    "exception-кодом и запросы, поглощённые timeout-инъекциями."
)
_METRICS_NOTE_EN = (
    "Rates are averaged over a sliding window. Errors include exception-code "
    "responses and requests swallowed by timeout injections."
)


def _ping_panel(lang: Lang) -> str:
    """Панель «Проверить соединение» с пробой через REST (TASK-702)."""
    options = "".join(f"<option>{n}</option>" for n in range(1, 11))
    return (
        f"<h2>{t(lang, 'Проверить соединение', 'Check connection')}</h2>\n"
        f"<p>{t(lang, _PING_DESC_RU, _PING_DESC_EN)}</p>\n"
        '<div class="form-row">\n'
        "  <label>Unit ID\n"
        f'    <select id="ping-slave">\n      {options}\n    </select>\n'
        "  </label>\n"
        "</div>\n"
        '<p class="form-actions">\n'
        f'  <button id="ping-run" type="button">'
        f"{t(lang, 'Проверить соединение', 'Check connection')}</button>\n"
        "</p>\n"
        '<p id="ping-result" role="status"></p>\n'
        "<script>\n" + _PING_JS + "</script>\n"
    )


def _metrics_panel(lang: Lang) -> str:
    """Панель «Счётчики сервера» с поллингом REST (TASK-703)."""
    placeholder = "-"
    return (
        f"<h2>{t(lang, 'Счётчики сервера', 'Server counters')}</h2>\n"
        "<dl>\n"
        f'  <dt>{t(lang, "Активные соединения", "Active connections")}</dt>'
        f'<dd id="metrics-conn">{placeholder}</dd>\n'
        f'  <dt>{t(lang, "Запросы / с", "Requests / s")}</dt>'
        f'<dd id="metrics-req">{placeholder}</dd>\n'
        f'  <dt>{t(lang, "Ошибки / с", "Errors / s")}</dt>'
        f'<dd id="metrics-err">{placeholder}</dd>\n'
        f'  <dt>{t(lang, "Всего запросов", "Total requests")}</dt>'
        f'<dd id="metrics-req-total">{placeholder}</dd>\n'
        f'  <dt>{t(lang, "Всего ошибок", "Total errors")}</dt>'
        f'<dd id="metrics-err-total">{placeholder}</dd>\n'
        f'  <dt>Uptime</dt><dd id="metrics-uptime">{placeholder}</dd>\n'
        "</dl>\n"
        f'<p class="note">{t(lang, _METRICS_NOTE_RU, _METRICS_NOTE_EN)}</p>\n'
        "<script>\n" + _METRICS_JS + "</script>\n"
    )


_INDEX_FMT_NOTE_RU = (
    "Slave 10 отдаёт только input registers. Адреса 300..301 - Unix timestamp "
    "(<code>uint32 ABCD</code>), остальной IR - <code>uint16</code>. Метрики "
    "часовых поясов смотрите в разделе «Часовые пояса». Для 32-битных форматов "
    "значение занимает два регистра (адреса значений 1, 3, …, 1023)."
)
_INDEX_FMT_NOTE_EN = (
    "Slave 10 serves input registers only. Addresses 300..301 carry the Unix "
    "timestamp (<code>uint32 ABCD</code>), the rest of its IR is "
    "<code>uint16</code>. Time zone metrics live in the «Time zones» section. "
    "A 32-bit value occupies two registers (value addresses 1, 3, …, 1023)."
)
_INDEX_TAIL_RU = (
    "Live-состояние регистров, настройка поведений, инъекции ошибок и журнал "
    "живут в соседних разделах."
)
_INDEX_TAIL_EN = (
    "Live register state, behavior setup, error injections and the journal "
    "live in the neighbouring sections."
)

_PROMO_RU = (
    "Десктопный ModbusToolkit подключается к этому серверу по Modbus-TCP и "
    "помогает проверить клиентский код на живых данных до выхода на реальный "
    "стенд."
)
_PROMO_EN = (
    "Desktop ModbusToolkit connects to this server over Modbus-TCP and helps "
    "you test client code against live data before the real bench."
)

_SUPPORT_RU = (
    "Сервер бесплатный и останется таким. Сейчас собираем на "
    "<b>испытательный стенд с реальными приборами</b>, на котором будет "
    "проверяться каждая версия ModbusToolkit."
)
_SUPPORT_EN = (
    "The server is free and stays that way. We are raising money for "
    "<b>a test bench with real instruments</b> to run every ModbusToolkit "
    "build against."
)


def _promo_block(lang: Lang) -> str:
    """Промо ModbusToolkit с кнопками на продукт (лендинг modbustoolkit)."""
    return (
        '<div class="docs-banner">\n'
        "  <div>\n"
        '    <span class="eyebrow">ModbusToolkit</span>\n'
        f"    <h2>{t(lang, 'Проверяйте клиенты в ModbusToolkit', 'Test clients in ModbusToolkit')}</h2>\n"
        f"    <p>{t(lang, _PROMO_RU, _PROMO_EN)}</p>\n"
        "  </div>\n"
        '  <div class="cta-row">\n'
        f'    <a class="btn" href="{PRODUCT_URL}">{t(lang, "Скачать", "Download")}</a>\n'
        f'    <a class="btn ghost" href="{PRODUCT_DOCS_URL}">{t(lang, "Документация", "Docs")}</a>\n'
        f'    <a class="btn ghost" href="{TELEGRAM_URL}">Telegram</a>\n'
        "  </div>\n"
        "</div>\n"
    )


def _support_block(lang: Lang) -> str:
    """Явный призыв поддержать проект: ссылка на страницу доната."""
    return (
        '<div class="hero-support">\n'
        f'  <a class="btn donate" href="{SUPPORT_URL}">'
        f'{t(lang, "Поддержать проект", "Support the project")}</a>\n'
        f"  <p>{t(lang, _SUPPORT_RU, _SUPPORT_EN)}</p>\n"
        "</div>\n"
    )


def _index_body(host: str, port: int, lang: Lang) -> str:
    """Обзор: параметры подключения, проба, счётчики, таблица форматов."""
    return f"""
<h2>{t(lang, "Как подключиться", "How to connect")}</h2>
<dl>
  <dt>{t(lang, "Хост", "Host")}</dt><dd><code>{escape(host)}</code></dd>
  <dt>{t(lang, "Порт", "Port")}</dt><dd><code>{port}</code></dd>
  <dt>Unit ID</dt><dd><code>1..10</code> {t(lang, "(slave 10 - только input registers)", "(slave 10 - input registers only)")}</dd>
  <dt>{t(lang, "Функции", "Functions")}</dt><dd><code>1, 2, 3, 4, 5, 6, 16</code> {t(lang, "- остальные отклоняются", "- anything else is rejected")}</dd>
</dl>
{_ping_panel(lang)}
{_metrics_panel(lang)}
<h2>{t(lang, "Форматы данных по слейвам", "Data formats per slave")}</h2>
{_formats_table(lang)}
<p class="note">{t(lang, _INDEX_FMT_NOTE_RU, _INDEX_FMT_NOTE_EN)}</p>
<p>{t(lang, _INDEX_TAIL_RU, _INDEX_TAIL_EN)}</p>
{_promo_block(lang)}
"""


#: Порядки байт/слов (TASK-751): раскрытие ABCD/CDAB/BADC/DCBA.
_SPEC_INTRO_RU = (
    "Каждый unit ID эмулирует свой формат данных для HR/IR. Значение "
    "32-битного формата занимает два регистра <code>N</code> и <code>N+1</code>, "
    "адрес значения - адрес старшего регистра (сетка 1, 3, …, 1023). Буквы "
    "ABCD - это байты значения от старшего (A) к младшему (D)."
)
_SPEC_INTRO_EN = (
    "Each unit ID emulates its own HR/IR data format. A 32-bit value occupies "
    "two registers <code>N</code> and <code>N+1</code>; the value address is "
    "the address of the high register (grid 1, 3, …, 1023). ABCD letters are "
    "the value bytes from the high byte (A) to the low byte (D)."
)
_SPEC_NOTE_RU = (
    "Slave 10 отдаёт только IR. Адреса 1..268 - метрики 27 часовых поясов "
    "(8 регистров на зону, незанятые регистры блока равны нулю), адреса "
    "300..301 - Unix timestamp (<code>uint32 ABCD</code>, погрешность 0.5 с). "
    "Полная спецификация лежит в <code>docs/HRIR_SPEC.md</code> репозитория."
)
_SPEC_NOTE_EN = (
    "Slave 10 serves IR only. Addresses 1..268 hold metrics for 27 time zones "
    "(8 registers per zone, unused block registers are zero), addresses "
    "300..301 hold the Unix timestamp (<code>uint32 ABCD</code>, 0.5 s "
    "accuracy). The full specification lives in <code>docs/HRIR_SPEC.md</code> "
    "of the repository."
)
_API_RU = (
    "REST-интерфейс описан в Swagger UI по адресу <code>/api/docs</code> "
    "(префикс <code>/api/modbus/</code>). WebSocket <code>/ws/state</code> "
    "отдаёт live-состояние слейва, <code>/ws/events</code> - события журнала. "
    "Примеры подключения собраны ниже."
)
_API_EN = (
    "The REST API is described in Swagger UI at <code>/api/docs</code> "
    "(prefix <code>/api/modbus/</code>). WebSocket <code>/ws/state</code> "
    "streams live slave state, <code>/ws/events</code> streams journal events. "
    "Connection examples are collected below."
)


def _api_links(lang: Lang) -> str:
    """Кнопки-ссылки на Swagger и схему OpenAPI для блока «API» (TASK-751)."""
    return (
        '<p class="api-links">\n'
        f'  <a class="btn" href="/api/docs">{t(lang, "Открыть Swagger UI", "Open Swagger UI")}</a>\n'
        f'  <a class="btn docs" href="/api/openapi.json">'
        f'{t(lang, "Схема OpenAPI (JSON)", "OpenAPI schema (JSON)")}</a>\n'
        "</p>\n"
    )


def _docs_body(config: Config, lang: Lang) -> str:
    """Страница «Документация» (TASK-751): форматы, ограничения, примеры.

    Спецификация форматов - та же таблица, что на «Обзоре» (`SLAVE_SPECS` -
    единственный источник истины); лимиты подставляются из `Config`, чтобы
    страница не расходилась с реальным окружением. Примеры кода (TASK-752)
    собраны на этой же странице.
    """
    specs = (
        f"  <li><code>ABCD</code> {t(lang, '- слова big-endian (старшее первым), байты в словах big-endian', '- big-endian words (high first), big-endian bytes in words')};</li>\n"
        f"  <li><code>CDAB</code> {t(lang, '- слова little-endian (младшее первым), байты в словах big-endian', '- little-endian words (low first), big-endian bytes in words')};</li>\n"
        f"  <li><code>BADC</code> {t(lang, '- слова big-endian, байты в словах little-endian (swap bytes)', '- big-endian words, little-endian bytes in words (swap bytes)')};</li>\n"
        f"  <li><code>DCBA</code> {t(lang, '- слова little-endian, байты в словах little-endian (swap bytes и swap words)', '- little-endian words, little-endian bytes in words (swap bytes and swap words)')}.</li>\n"
    )
    limits = (
        f"  <li>{t(lang, 'Допустимы функции', 'Allowed functions')} "
        f"<code>1, 2, 3, 4, 5, 6, 16</code>. "
        f"{t(lang, 'Любая другая отклоняется как Illegal Function (0x01).', 'Anything else is rejected as Illegal Function (0x01).')}</li>\n"
        f"  <li>{t(lang, 'FC 5 и FC 6 пишут один CL/HR, FC 16 пишет HR и принимает не более 2 регистров за запрос (иначе Illegal Data Value, 0x03).', 'FC 5 and FC 6 write a single CL/HR, FC 16 writes HR and accepts no more than 2 registers per request (otherwise Illegal Data Value, 0x03).')}</li>\n"
        f"  <li>{t(lang, 'Slave 10 отдаёт только input registers. Чтение CL/DI/HR возвращает Illegal Data Address (0x02).', 'Slave 10 serves input registers only. Reading CL/DI/HR returns Illegal Data Address (0x02).')}</li>\n"
        f"  <li>{t(lang, 'Адресация совпадает с docs/HRIR_SPEC.md, пересчёт не нужен. Адрес значения равен адресу на проводе.', 'Addressing follows docs/HRIR_SPEC.md with no conversion. The value address equals the wire address.')}</li>\n"
        f"  <li>{t(lang, 'Ограничения окружения: соединений с одного IP', 'Environment limits: connections per IP')} - {config.MODBUS_MAX_CONN_PER_IP}, "
        f"{t(lang, 'Modbus-записей в секунду на соединение', 'Modbus writes per second per connection')} - {config.MODBUS_MAX_WRITE_PER_SEC}, "
        f"{t(lang, 'записей из UI в минуту на IP', 'UI writes per minute per IP')} - {config.WEB_MAX_WRITE_PER_MIN_PER_IP}.</li>\n"
        f"  <li>{t(lang, 'Инъекции ошибок действуют не дольше', 'Error injections last at most')} "
        f"{config.INJECTION_MAX_TTL_S:g} {t(lang, 'с (INJECTION_MAX_TTL_S).', 's (INJECTION_MAX_TTL_S).')}</li>\n"
        f"  <li>{t(lang, 'Журнал хранит последние', 'The journal keeps the last')} "
        f"{config.JOURNAL_SIZE} {t(lang, 'событий. Значения регистров и вся история живут только в памяти, перезапуск сервера сбрасывает всё.', 'events. Register values and the whole history live in memory only, a server restart wipes everything.')}</li>\n"
    )
    rules = (
        f"  <li><strong>{t(lang, 'HR и CL общие (shared last-write-wins).', 'HR and CL are shared (shared last-write-wins).')}</strong> "
        f"{t(lang, 'Запись любого пользователя сразу видна остальным и в любой момент может быть перезаписана кем-то ещё. Это принцип работы сервера, а не ошибка.', 'Any user write is immediately visible to everyone else and can be overwritten by anyone at any time. This is how the server works, not a bug.')}</li>\n"
        f"  <li><strong>{t(lang, 'IR и DI генерируются автоматически', 'IR and DI are generated automatically')}</strong> "
        f"{t(lang, 'движком поведений (счётчики, паттерны) и доступны только для чтения.', 'by the behavior engine (counters, patterns) and are read-only.')}</li>\n"
        f"  <li>{t(lang, 'Настройки поведений и инъекции ошибок тоже общие для всех подключений.', 'Behavior settings and error injections are shared across all connections too.')}</li>\n"
        f"  <li>{t(lang, 'Аутентификации нет. Не передавайте через сервер чувствительные данные и не используйте его как стенд с реальным оборудованием.', 'There is no authentication. Do not pass sensitive data through the server and do not use it as a bench for real equipment.')}</li>\n"
    )
    return (
        f"<h3>{t(lang, 'Спецификация форматов', 'Format specification')}</h3>\n"
        f"<p>{t(lang, _SPEC_INTRO_RU, _SPEC_INTRO_EN)}</p>\n"
        "<ul>\n" + specs + "</ul>\n"
        + _formats_table(lang)
        + "\n"
        f'<p class="note">{t(lang, _SPEC_NOTE_RU, _SPEC_NOTE_EN)}</p>\n'
        "\n"
        f"<h3>{t(lang, 'Ограничения', 'Limits')}</h3>\n"
        "<ul>\n" + limits + "</ul>\n"
        "\n"
        f"<h3>{t(lang, 'Правила многопользовательскости', 'Multiplayer rules')}</h3>\n"
        "<ul>\n" + rules + "</ul>\n"
        "\n"
        "<h3>API</h3>\n"
        f"<p>{t(lang, _API_RU, _API_EN)}</p>\n"
        + _api_links(lang)
        + _examples_section(lang)
    )


#: Пример Python (pymodbus) для «Документации» (TASK-752). Обычная строка:
#: фигурные скобки f-строк остаются литеральными. RU-текст не править:
#: пины тестов смотрят его дословно (tests/test_docs_page.py).
_EXAMPLE_PYTHON_RU = """\
# pip install "pymodbus>=3.6"
from pymodbus.client import ModbusTcpClient

client = ModbusTcpClient("modbustoolkit.electroman.by", port=5020)
if not client.connect():
    raise SystemExit("нет соединения с сервером")

# FC 3: читаем 10 регистров HR слейва 1 (uint16) с адреса 1
result = client.read_holding_registers(1, count=10, device_id=1)
if result.isError():
    raise SystemExit(f"ошибка Modbus: {result}")
print(result.registers)

# FC 4: input registers (на slave 10 только они)
result = client.read_input_registers(1, count=8, device_id=10)

# FC 5 / FC 6 / FC 16: запись CL и HR (значения общие для всех!)
client.write_coil(1, True, device_id=1)
client.write_register(1, 42, device_id=1)
client.write_registers(1, [1, 2], device_id=1)  # FC 16, не более 2 регистров

client.close()
"""

#: Тот же пример с английскими комментариями и строками (EN-страница).
_EXAMPLE_PYTHON_EN = """\
# pip install "pymodbus>=3.6"
from pymodbus.client import ModbusTcpClient

client = ModbusTcpClient("modbustoolkit.electroman.by", port=5020)
if not client.connect():
    raise SystemExit("no connection to the server")

# FC 3: read 10 holding registers of slave 1 (uint16) from address 1
result = client.read_holding_registers(1, count=10, device_id=1)
if result.isError():
    raise SystemExit(f"Modbus error: {result}")
print(result.registers)

# FC 4: input registers (slave 10 has only these)
result = client.read_input_registers(1, count=8, device_id=10)

# FC 5 / FC 6 / FC 16: write coils and holding registers (values are shared!)
client.write_coil(1, True, device_id=1)
client.write_register(1, 42, device_id=1)
client.write_registers(1, [1, 2], device_id=1)  # FC 16, max 2 registers per write

client.close()
"""

#: Пример Node.js (modbus-serial) для «Документации» (TASK-752).
_EXAMPLE_NODEJS_RU = """\
// npm install modbus-serial
const ModbusRTU = require("modbus-serial");

async function main() {
  const client = new ModbusRTU();
  await client.connectTCP("modbustoolkit.electroman.by", { port: 5020 });
  client.setID(1); // unit ID 1..10

  // FC 3: 10 регистров HR с адреса 1
  const result = await client.readHoldingRegisters(1, 10);
  console.log(result.data);

  // FC 4: input registers (на slave 10 только они)
  client.setID(10);
  console.log((await client.readInputRegisters(1, 8)).data);

  // FC 5 / FC 6: запись CL и HR (значения общие для всех!)
  client.setID(1);
  await client.writeCoil(1, true);
  await client.writeRegister(1, 42);

  client.close();
}

main().catch(console.error);
"""

#: Тот же пример с английскими комментариями (EN-страница).
_EXAMPLE_NODEJS_EN = """\
// npm install modbus-serial
const ModbusRTU = require("modbus-serial");

async function main() {
  const client = new ModbusRTU();
  await client.connectTCP("modbustoolkit.electroman.by", { port: 5020 });
  client.setID(1); // unit ID 1..10

  // FC 3: 10 holding registers from address 1
  const result = await client.readHoldingRegisters(1, 10);
  console.log(result.data);

  // FC 4: input registers (slave 10 has only these)
  client.setID(10);
  console.log((await client.readInputRegisters(1, 8)).data);

  // FC 5 / FC 6: write coils and holding registers (values are shared!)
  client.setID(1);
  await client.writeCoil(1, true);
  await client.writeRegister(1, 42);

  client.close();
}

main().catch(console.error);
"""

#: Пример C# (NModbus) для «Документации» (TASK-752).
_EXAMPLE_CSHARP_RU = """\
// dotnet add package NModbus
using System.Net.Sockets;
using NModbus;

var tcp = new TcpClient("modbustoolkit.electroman.by", 5020);
var client = new ModbusFactory().CreateMaster(tcp);

// FC 3: 10 регистров HR слейва 1 (uint16) с адреса 1
ushort[] regs = client.ReadHoldingRegisters(slaveId: 1, startAddress: 1, numberOfPoints: 10);
Console.WriteLine(string.Join(", ", regs));

// FC 4: input registers (на slave 10 только они)
ushort[] ir = client.ReadInputRegisters(1, 1, 8);

// FC 5 / FC 6: запись CL и HR (значения общие для всех!)
client.WriteSingleCoil(1, 1, true);
client.WriteSingleRegister(1, 1, 42);
"""

#: Тот же пример с английскими комментариями (EN-страница).
_EXAMPLE_CSHARP_EN = """\
// dotnet add package NModbus
using System.Net.Sockets;
using NModbus;

var tcp = new TcpClient("modbustoolkit.electroman.by", 5020);
var client = new ModbusFactory().CreateMaster(tcp);

// FC 3: read 10 holding registers of slave 1 (uint16) from address 1
ushort[] regs = client.ReadHoldingRegisters(slaveId: 1, startAddress: 1, numberOfPoints: 10);
Console.WriteLine(string.Join(", ", regs));

// FC 4: input registers (slave 10 has only these)
ushort[] ir = client.ReadInputRegisters(1, 1, 8);

// FC 5 / FC 6: write coils and holding registers (values are shared!)
client.WriteSingleCoil(1, 1, true);
client.WriteSingleRegister(1, 1, 42);
"""

_MT_INTRO_RU = (
    "Десктопный клиент того же проекта, что и этот сервер. Создайте Modbus TCP "
    "соединение к стенду и проверьте клиентский код на живых данных."
)
_MT_INTRO_EN = (
    "The desktop client of the same project as this server. Create a Modbus "
    "TCP connection to the bench and test your client code against live data."
)
_MT_LINKS_RU = (
    f'Скачать ModbusToolkit можно на <a href="{PRODUCT_URL}">modbustoolkit.electroman.by</a>. '
    f'Документация продукта: <a href="{PRODUCT_DOCS_URL}">docs.html</a>. '
    f'Новости и поддержка: <a href="{TELEGRAM_URL}">t.me/modbustoolkit</a>.'
)
_MT_LINKS_EN = (
    f'Download ModbusToolkit at <a href="{PRODUCT_URL}">modbustoolkit.electroman.by</a>. '
    f'Product docs: <a href="{PRODUCT_DOCS_URL}">docs.html</a>. '
    f'News and support: <a href="{TELEGRAM_URL}">t.me/modbustoolkit</a>.'
)
_EXAMPLES_NOTE_RU = (
    "Готовые сниппеты для pymodbus, modbus-serial и NModbus. Адресация - как "
    "в <code>docs/HRIR_SPEC.md</code>, без пересчёта; unit ID - 1..10. HR/CL "
    "общие для всех подключений (shared last-write-wins)."
)
_EXAMPLES_NOTE_EN = (
    "Ready snippets for pymodbus, modbus-serial and NModbus. Addressing "
    "follows <code>docs/HRIR_SPEC.md</code> with no conversion; unit ID is "
    "1..10. HR/CL are shared across all connections (shared last-write-wins)."
)


def _example_block(title: str, code_id: str, code: str, lang: Lang) -> str:
    """Один пример: заголовок, блок кода и кнопка копирования (TASK-752)."""
    return (
        f"<h4>{escape(title)}</h4>\n"
        f'<pre><code id="{code_id}">{escape(code)}</code></pre>\n'
        f'<p><button type="button" class="copy-btn" data-copy="{code_id}">'
        f'{t(lang, "Копировать", "Copy")}</button></p>\n'
    )


def _examples_section(lang: Lang) -> str:
    """Секция примеров на «Документации» (TASK-752).

    В первую очередь - подключение из десктопного ModbusToolkit (тот же
    продукт, что и сервер), затем сниппеты кода для pymodbus, modbus-serial
    и NModbus; кнопки копирования обслуживает `static/examples.js`.
    """
    return (
        f"<h3>{t(lang, 'Подключение из ModbusToolkit', 'Connect from ModbusToolkit')}</h3>\n"
        f"<p>{t(lang, _MT_INTRO_RU, _MT_INTRO_EN)}</p>\n"
        "<ol>\n"
        f"  <li>{t(lang, 'Создайте соединение Modbus TCP: хост', 'Create a Modbus TCP connection: host')} "
        f"<code>modbustoolkit.electroman.by</code>, "
        f"{t(lang, 'порт', 'port')} <code>5020</code>, Unit ID 1..10.</li>\n"
        f"  <li>{t(lang, 'Прочитайте 10 регистров HR слейва 1 функцией 3 с адреса 1. Формат значений slave 1 - uint16.', 'Read 10 holding registers of slave 1 with function 3 from address 1. Slave 1 values use the uint16 format.')}</li>\n"
        f"  <li>{t(lang, 'Запишите значение функцией 6 и убедитесь, что данные общие. Запись видна всем подключённым клиентам (shared last-write-wins).', 'Write a value with function 6 and see that the data is shared. Every connected client sees the write (shared last-write-wins).')}</li>\n"
        "</ol>\n"
        f'<p class="note">{t(lang, _MT_LINKS_RU, _MT_LINKS_EN)}</p>\n'
        f"<h3>{t(lang, 'Примеры кода', 'Code examples')}</h3>\n"
        f'<p class="note">{t(lang, _EXAMPLES_NOTE_RU, _EXAMPLES_NOTE_EN)}</p>\n'
        + _example_block(
            "Python · pymodbus", "example-python", t(lang, _EXAMPLE_PYTHON_RU, _EXAMPLE_PYTHON_EN), lang
        )
        + _example_block(
            "Node.js · modbus-serial",
            "example-nodejs",
            t(lang, _EXAMPLE_NODEJS_RU, _EXAMPLE_NODEJS_EN),
            lang,
        )
        + _example_block(
            "C# · NModbus", "example-csharp", t(lang, _EXAMPLE_CSHARP_RU, _EXAMPLE_CSHARP_EN), lang
        )
        + '<p id="example-status" role="status"></p>\n'
        + '<script type="module" src="/static/examples.js"></script>'
    )


_CONVERTER_NOTE_RU = (
    "Показывает, в какие два регистра кодируется значение и какое значение "
    "читается из двух регистров во всех четырёх порядках: <code>ABCD</code>, "
    "<code>CDAB</code>, <code>BADC</code>, <code>DCBA</code>. Пересчёт делает "
    "сервер тем же кодеком, что и слейвы 2..9 (<code>docs/HRIR_SPEC.md</code>)."
)
_CONVERTER_NOTE_EN = (
    "Shows how a value maps to two registers and which value two registers "
    "decode to, in all four orders: <code>ABCD</code>, <code>CDAB</code>, "
    "<code>BADC</code>, <code>DCBA</code>. The server does the conversion "
    "with the same codec as slaves 2..9 (<code>docs/HRIR_SPEC.md</code>)."
)
_CFG_NOTE_RU = (
    "Готовые настройки для Modbus Poll, QModMaster, CAS Modbus Scanner, "
    "скрипт для pyModbus и flow для Node-RED: подключение, функция, адрес и "
    "формат данных выбранного слейва. Адрес берут из "
    "<code>docs/HRIR_SPEC.md</code> без пересчёта, количество задают в "
    "значениях (для 32-битных слейвов значение занимает 2 регистра)."
)
_CFG_NOTE_EN = (
    "Ready configs for Modbus Poll, QModMaster, CAS Modbus Scanner, a script "
    "for pyModbus and a flow for Node-RED: connection, function, address and "
    "the data format of the selected slave. The address comes straight from "
    "<code>docs/HRIR_SPEC.md</code> with no conversion, the count is given in "
    "values (a 32-bit slave value takes 2 registers)."
)
_DIFF_NOTE_RU = (
    "Сравнивает два снимка состояния (<code>POST /api/modbus/snapshot</code>) "
    "и показывает только различия: изменённые, добавленные и пропавшие "
    "значения. Прочерк в ячейке означает, что записи в этом снимке нет. "
    "Снимок можно снять кнопкой ниже, файлы-версии сравниваются между собой."
)
_DIFF_NOTE_EN = (
    "Compares two state snapshots (<code>POST /api/modbus/snapshot</code>) "
    "and shows only the differences: changed, added and missing values. A "
    "dash in a cell means the snapshot has no such record. Take a snapshot "
    "with the button below; saved files compare against each other."
)


def _tools_body(lang: Lang) -> str:
    """Инструменты наладчика: конвертер (TASK-741), генератор конфигов (TASK-742),
    дифф снимков (TASK-744).

    Конвертер: значение ↔ пара регистров во всех 4 порядках (ABCD/CDAB/BADC/DCBA)
    для uint32 и float; пересчёт серверный (`POST /api/modbus/convert`, тот же
    кодек, что и у слейвов 2..9). Генератор: готовые настройки/скрипты/flow для
    Modbus Poll, QModMaster, CAS Scanner, pyModbus и Node-RED
    (`POST /api/modbus/configgen`). Дифф: сравнение двух снимков состояния
    (`POST /api/modbus/snapshot/diff`). Наполняют `static/tools.js`,
    `static/configgen.js` и `static/snapshot-diff.js`.
    """
    slave_options = "".join(f"<option>{slave_id}</option>" for slave_id in SLAVE_SPECS)
    return (
        f"<h2>{t(lang, 'Конвертер байт/слов', 'Byte/word converter')}</h2>\n"
        f'<p class="note">{t(lang, _CONVERTER_NOTE_RU, _CONVERTER_NOTE_EN)}</p>\n'
        '<div class="form-row">\n'
        f'  <label>{t(lang, "Тип", "Type")} <select id="tool-kind">\n'
        '    <option value="uint32">uint32</option>\n'
        '    <option value="float">float</option>\n'
        "  </select></label>\n"
        f'  <label>{t(lang, "Что вводим", "Input")} <select id="tool-source">\n'
        f'    <option value="value">{t(lang, "Значение", "Value")}</option>\n'
        f'    <option value="regs">{t(lang, "Два регистра", "Two registers")}</option>\n'
        "  </select></label>\n"
        "</div>\n"
        '<div class="form-row">\n'
        f'  <label>{t(lang, "Значение", "Value")} <input id="tool-value" type="text" '
        f'placeholder="{t(lang, "например 3.5 или 0x12345678", "for example 3.5 or 0x12345678")}"></label>\n'
        f'  <label>{t(lang, "Регистр 1", "Register 1")} <input id="tool-reg-hi" type="text" placeholder="0x1234"></label>\n'
        f'  <label>{t(lang, "Регистр 2", "Register 2")} <input id="tool-reg-lo" type="text" placeholder="0x5678"></label>\n'
        "</div>\n"
        '<p class="form-actions">\n'
        f'  <button id="tool-convert" type="button">{t(lang, "Конвертировать", "Convert")}</button>\n'
        '  <span id="tool-status" role="status"></span>\n'
        "</p>\n"
        '<table class="values">\n'
        f"  <thead><tr><th>{t(lang, 'Порядок', 'Order')}</th>"
        f"<th>{t(lang, 'Регистры (hex)', 'Registers (hex)')}</th>"
        f"<th>{t(lang, 'Регистры (dec)', 'Registers (dec)')}</th>"
        f"<th>{t(lang, 'Значение', 'Value')}</th></tr></thead>\n"
        '  <tbody id="tool-rows"></tbody>\n'
        "</table>\n"
        "\n"
        f"<h2>{t(lang, 'Генератор конфигов', 'Config generator')}</h2>\n"
        f'<p class="note">{t(lang, _CFG_NOTE_RU, _CFG_NOTE_EN)}</p>\n'
        '<div class="form-row">\n'
        f'  <label>{t(lang, "Инструмент", "Tool")} <select id="cfg-tool">\n'
        '    <option value="modbus_poll">Modbus Poll</option>\n'
        '    <option value="qmodmaster">QModMaster</option>\n'
        '    <option value="cas_scanner">CAS Modbus Scanner</option>\n'
        f'    <option value="pymodbus">pyModbus ({t(lang, "скрипт", "script")})</option>\n'
        f'    <option value="nodered">Node-RED (flow)</option>\n'
        "  </select></label>\n"
        f'  <label>Unit ID <select id="cfg-slave">{slave_options}</select></label>\n'
        "</div>\n"
        '<div class="form-row">\n'
        f'  <label>{t(lang, "Область", "Area")} <select id="cfg-area">\n'
        '    <option value="hr">hr · Holding Registers</option>\n'
        '    <option value="ir">ir · Input Registers</option>\n'
        '    <option value="cl">cl · Coils</option>\n'
        '    <option value="di">di · Discrete Inputs</option>\n'
        "  </select></label>\n"
        f'  <label>{t(lang, "Адрес", "Address")} <input id="cfg-address" type="text" value="1"></label>\n'
        f'  <label>{t(lang, "Значений", "Count")} <input id="cfg-count" type="text" value="1"></label>\n'
        "</div>\n"
        '<p class="form-actions">\n'
        f'  <button id="cfg-generate" type="button">{t(lang, "Сгенерировать", "Generate")}</button>\n'
        f'  <button id="cfg-copy" type="button">{t(lang, "Копировать", "Copy")}</button>\n'
        f'  <a id="cfg-download" href="#" download>{t(lang, "Скачать", "Download")}</a>\n'
        '  <span id="cfg-status" role="status"></span>\n'
        "</p>\n"
        '<pre id="cfg-output"></pre>\n'
        "\n"
        f"<h2>{t(lang, 'Дифф двух снимков', 'Snapshot diff')}</h2>\n"
        f'<p class="note">{t(lang, _DIFF_NOTE_RU, _DIFF_NOTE_EN)}</p>\n'
        '<div class="form-row">\n'
        f'  <label>{t(lang, "Снимок A", "Snapshot A")} <input id="diff-file-a" type="file" '
        'accept=".json,application/json"></label>\n'
        f'  <label>{t(lang, "Снимок B", "Snapshot B")} <input id="diff-file-b" type="file" '
        'accept=".json,application/json"></label>\n'
        "</div>\n"
        '<p class="form-actions">\n'
        f'  <button id="diff-compare" type="button">{t(lang, "Сравнить", "Compare")}</button>\n'
        f'  <button id="diff-snapshot" type="button">{t(lang, "Снять текущий снимок", "Take current snapshot")}</button>\n'
        '  <span id="diff-status" role="status"></span>\n'
        "</p>\n"
        '<p id="diff-summary"></p>\n'
        '<table class="values">\n'
        f"  <thead><tr><th>Unit ID</th><th>{t(lang, 'Область', 'Area')}</th>"
        f"<th>{t(lang, 'Адрес', 'Address')}</th>"
        "<th>A</th><th>B</th></tr></thead>\n"
        '  <tbody id="diff-rows"></tbody>\n'
        "</table>\n"
        '<script type="module" src="/static/tools.js"></script>\n'
        '<script type="module" src="/static/configgen.js"></script>\n'
        '<script type="module" src="/static/snapshot-diff.js"></script>'
    )


def _register_map_json() -> str:
    """Геометрия карты для JS (TASK-711): slave и его fmt, диапазоны областей.

    Диапазоны - тройки `[start, stop, step]` сетки значений (1,3,…,1023 для
    32-битных форматов), из `SLAVE_SPECS` - единственный источник истины.
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


_REG_NOTE_RU = (
    "Значения живые (WS <code>/ws/state</code>) и общие для всех подключений: "
    "запись любого пользователя сразу видна остальным (shared last-write-wins)."
)
_REG_NOTE_EN = (
    "Values are live (WS <code>/ws/state</code>) and shared across all "
    "connections: any user write is immediately visible to everyone else "
    "(shared last-write-wins)."
)
_REG_EDIT_RU = (
    "Редактирование CL/HR доступно прямо в таблице (клик по значению). "
    "Значения общие, запись применяется по правилу shared last-write-wins и "
    "в любой момент может быть перезаписана другим пользователем."
)
_REG_EDIT_EN = (
    "You can edit CL/HR right in the table (click a value). Values are shared: "
    "a write follows the shared last-write-wins rule and can be overwritten "
    "by another user at any time."
)
_WATCH_NOTE_RU = (
    "Watch list регистров под наблюдением хранится в браузере (localStorage) "
    "и сохраняется между визитами. Добавить регистр можно кнопкой со "
    "звёздочкой в таблице выше, значения обновляются по WS-подписке."
)
_WATCH_NOTE_EN = (
    "The watch list of registers lives in the browser (localStorage) and "
    "survives between visits. Add a register with the star button in the "
    "table above; values update over the WS subscription."
)


def _registers_body(lang: Lang) -> str:
    """Каркас «Карты регистров» (TASK-711): выбор слейва/области, таблица, пагинация.

    Значения наполняет `static/registers.js` (WS `/ws/state` + REST-фолбэк),
    поэтому body собирается конкатенацией: JSON геометрии содержит фигурные
    скобки, f-строки тут только мешали бы.
    """
    slave_options = "".join(f"<option>{slave_id}</option>" for slave_id in SLAVE_SPECS)
    return (
        f'<p class="note">{t(lang, _REG_NOTE_RU, _REG_NOTE_EN)}</p>\n'
        f'<div class="banner" role="status">{t(lang, _REG_EDIT_RU, _REG_EDIT_EN)}</div>\n'
        '<div class="form-row">\n'
        f'  <label>Unit ID <select id="reg-slave">{slave_options}</select></label>\n'
        f'  <label>{t(lang, "Область", "Area")} <select id="reg-area"></select></label>\n'
        f'  <label>{t(lang, "Формат", "Format")} <select id="reg-format"></select></label>\n'
        "</div>\n"
        '<p class="note" id="reg-fmt"></p>\n'
        '<table class="values">\n'
        f"  <thead><tr><th>{t(lang, 'Адрес', 'Address')}</th>"
        f"<th>{t(lang, 'Значение', 'Value')}</th><th></th></tr></thead>\n"
        '  <tbody id="reg-body"></tbody>\n'
        "</table>\n"
        '<p id="reg-status" role="status"></p>\n'
        "<p>\n"
        f'  <button id="reg-prev" type="button">{t(lang, "Назад", "Back")}</button>\n'
        '  <span id="reg-page" role="status"></span>\n'
        f'  <button id="reg-next" type="button">{t(lang, "Вперёд", "Next")}</button>\n'
        "</p>\n"
        f"<h2>{t(lang, 'Избранное', 'Favorites')}</h2>\n"
        f'<p class="note">{t(lang, _WATCH_NOTE_RU, _WATCH_NOTE_EN)}</p>\n'
        '<table class="values">\n'
        f"  <thead><tr><th>Unit ID</th><th>{t(lang, 'Область', 'Area')}</th>"
        f"<th>{t(lang, 'Адрес', 'Address')}</th><th>{t(lang, 'Значение', 'Value')}</th>"
        "<th></th></tr></thead>\n"
        '  <tbody id="watch-body"></tbody>\n'
        "</table>\n"
        "<p>\n"
        f'  <button id="watch-clear" type="button">{t(lang, "Очистить список", "Clear list")}</button>\n'
        '  <span id="watch-status" role="status"></span>\n'
        "</p>\n"
        f"<h2>{t(lang, 'График', 'Chart')}</h2>\n"
        '<div class="form-row">\n'
        f"  <label>{t(lang, 'Окно, секунды', 'Window, seconds')}\n"
        '    <select id="chart-window">\n'
        "      <option>60</option><option>120</option><option>300</option>\n"
        "    </select>\n"
        "  </label>\n"
        "</div>\n"
        f'<p class="note">{t(lang, "Live-график регистров из избранного.", "Live chart of the favorite registers.")}</p>\n'
        '<canvas id="chart-canvas" width="800" height="240"></canvas>\n'
        '<script type="module" src="/static/chart.js"></script>\n'
        "<script>window.REGISTER_MAP = " + _register_map_json() + ";</script>\n"
        '<script type="module" src="/static/registers.js"></script>\n'
        '<script type="module" src="/static/watchlist.js"></script>'
    )


_BH_NOTE_RU = (
    "Поведение задаёт, как значение регистра или бита меняется во времени "
    "(счётчик, пила, шум и так далее). Привязки общие для всех пользователей, "
    "любое изменение сразу действует для остальных. Slave 10 поведений не "
    "имеет, его IR вычисляются (часовые пояса и Unix timestamp)."
)
_BH_NOTE_EN = (
    "A behavior drives how a register or bit value changes over time (counter, "
    "sawtooth, noise and so on). Bindings are shared across all users, any "
    "change immediately applies to everyone. Slave 10 has no behaviors; its "
    "IR are computed (time zones and the Unix timestamp)."
)
_BH_PRESETS_NOTE_RU = (
    "Быстрая настройка формы ниже: заполняет поведение и параметры. Пресеты "
    "работают для числовых областей <code>hr</code>/<code>ir</code>, "
    "«Сброс» ставит константу 0."
)
_BH_PRESETS_NOTE_EN = (
    "Quick setup for the form below: fills in the behavior and its parameters. "
    "Presets work for numeric areas <code>hr</code>/<code>ir</code>, "
    "«Reset» sets constant 0."
)
_BH_OVERRIDES_NOTE_RU = (
    "Привязки, отличающиеся от поведений по умолчанию (HR/CL - константа 0, "
    "IR - счётчик и витрина генераторов, DI - случайные биты и биты счётчика "
    "секунд, верхние слоты HR - демо-генераторы)."
)
_BH_OVERRIDES_NOTE_EN = (
    "Bindings that differ from the default behaviors (HR/CL - constant 0, "
    "IR - counter and the generator showcase, DI - random bits and second-"
    "counter bits, top HR slots - demo generators)."
)
_BH_MINICHART_NOTE_RU = (
    "Значение выбранного регистра (Unit ID / Область / Адрес выше) за "
    "последние 60 с, подписка по WS <code>/ws/state</code>."
)
_BH_MINICHART_NOTE_EN = (
    "Value of the selected register (Unit ID / Area / Address above) for the "
    "last 60 s, subscribed over WS <code>/ws/state</code>."
)


def _behaviors_body(lang: Lang) -> str:
    """Каркас «Поведений» (TASK-721…723): форма привязки и таблица переопределений.

    Форма: slave 1..9 (slave 10 поведений не имеет), область, адрес/диапазон,
    kind (фильтруется по области: числовые - hr/ir, дискретные - cl/di),
    параметры по kind и `locked`. Пресеты (TASK-722) заполняют форму одним
    нажатием. Наполняет `static/behaviors.js`: одна привязка - PUT
    `/api/modbus/behaviors/{slave}/{area}/{address}`, диапазон
    («Применить к диапазону» / «Сбросить») - POST `/api/modbus/behaviors/batch`,
    список переопределений - GET `/api/modbus/behaviors?only_overrides=true`.
    """
    slave_options = "".join(f"<option>{slave_id}</option>" for slave_id in range(1, 10))
    area_options = "".join(f'<option value="{area}">{area}</option>' for area in AREAS)
    return (
        f'<p class="note">{t(lang, _BH_NOTE_RU, _BH_NOTE_EN)}</p>\n'
        '<div class="form-row">\n'
        f'  <label>Unit ID <select id="bh-slave">{slave_options}</select></label>\n'
        f'  <label>{t(lang, "Область", "Area")} <select id="bh-area">{area_options}</select></label>\n'
        f'  <label>{t(lang, "Адрес от", "Address from")} <input id="bh-address" type="number" min="1" value="1"></label>\n'
        f'  <label>{t(lang, "Адрес по", "Address to")} <input id="bh-address-to" type="number" min="1" value="1"></label>\n'
        "</div>\n"
        f"<h2>{t(lang, 'Пресеты', 'Presets')}</h2>\n"
        f'<p class="note">{t(lang, _BH_PRESETS_NOTE_RU, _BH_PRESETS_NOTE_EN)}</p>\n'
        '<p id="bh-presets">\n'
        f'  <button type="button" data-preset="counter">{t(lang, "Счётчик", "Counter")}</button>\n'
        f'  <button type="button" data-preset="sawtooth">{t(lang, "Пила", "Sawtooth")}</button>\n'
        f'  <button type="button" data-preset="triangle">{t(lang, "Треугольник", "Triangle")}</button>\n'
        f'  <button type="button" data-preset="meander">{t(lang, "Меандр", "Meander")}</button>\n'
        f'  <button type="button" data-preset="noise">{t(lang, "Шум", "Noise")}</button>\n'
        f'  <button type="button" data-preset="sine">{t(lang, "Синус", "Sine")}</button>\n'
        f'  <button type="button" data-preset="step">{t(lang, "Ступеньки", "Steps")}</button>\n'
        f'  <button type="button" data-preset="reset">{t(lang, "Сброс", "Reset")}</button>\n'
        "</p>\n"
        '<div class="form-row">\n'
        f'  <label>{t(lang, "Поведение", "Behavior")} <select id="bh-kind"></select></label>\n'
        f'  <label class="check"><input id="bh-locked" type="checkbox"> '
        f"{t(lang, 'Заблокировать (locked: внешние записи игнорируются)', 'Lock (locked: external writes are ignored)')}</label>\n"
        "</div>\n"
        '<div id="bh-params"></div>\n'
        '<p class="form-actions">\n'
        f'  <button id="bh-apply" type="button">{t(lang, "Установить", "Set")}</button>\n'
        f'  <button id="bh-apply-range" type="button">{t(lang, "Применить к диапазону", "Apply to range")}</button>\n'
        f'  <button id="bh-reset-range" type="button">{t(lang, "Сбросить", "Reset")}</button>\n'
        '  <span id="bh-status" role="status"></span>\n'
        "</p>\n"
        f"<h2>{t(lang, 'Текущие переопределения', 'Current overrides')}</h2>\n"
        f'<p class="note">{t(lang, _BH_OVERRIDES_NOTE_RU, _BH_OVERRIDES_NOTE_EN)}</p>\n'
        "<p>\n"
        f'  <button id="bh-refresh" type="button">{t(lang, "Обновить", "Refresh")}</button>\n'
        "</p>\n"
        '<table class="values">\n'
        "  <thead><tr><th>Unit ID</th>"
        f"<th>{t(lang, 'Область', 'Area')}</th><th>{t(lang, 'Адрес', 'Address')}</th>"
        f"<th>{t(lang, 'Поведение', 'Behavior')}</th>"
        f"<th>{t(lang, 'Параметры', 'Params')}</th><th>Locked</th></tr></thead>\n"
        '  <tbody id="bh-list"></tbody>\n'
        "</table>\n"
        f"<h2>{t(lang, 'Мини-график', 'Mini chart')}</h2>\n"
        f'<p class="note">{t(lang, _BH_MINICHART_NOTE_RU, _BH_MINICHART_NOTE_EN)}</p>\n'
        '<canvas id="bh-chart" width="800" height="160"></canvas>\n'
        "<script>window.REGISTER_MAP = " + _register_map_json() + ";</script>\n"
        '<script type="module" src="/static/behaviors.js"></script>\n'
        '<script type="module" src="/static/minichart.js"></script>'
    )


def _tz_cards(lang: Lang) -> str:
    """27 карточек зон Z=0..26 (TASK-731): смещение UTC + поля даты/времени.

    Значения наполняет `static/timezones.js` из IR slave 10 (блок зоны:
    год, месяц, день, час, минута, секунда, день недели, смещение).
    """
    cards = []
    for zone in range(ZONE_COUNT):
        offset = zone_offset_hours(zone)
        sign = "+" if offset >= 0 else "−"
        placeholder = "-"
        cards.append(
            f'<div class="zone-card">'
            f'<h3>Z={zone} · UTC{sign}{abs(offset):02d}:00</h3>'
            f'<dl>'
            f'<dt>{t(lang, "Дата", "Date")}</dt><dd id="tz-{zone}-date">{placeholder}</dd>'
            f'<dt>{t(lang, "Время", "Time")}</dt><dd id="tz-{zone}-time">{placeholder}</dd>'
            f'<dt>{t(lang, "День недели", "Weekday")}</dt><dd id="tz-{zone}-weekday">{placeholder}</dd>'
            f'<dt>{t(lang, "Смещение", "Offset")}</dt><dd id="tz-{zone}-offset">{placeholder}</dd>'
            f"</dl></div>"
        )
    return '<div class="zone-grid">' + "".join(cards) + "</div>"


_TZ_NOTE_RU = (
    "Каждая зона Z=0..26 - блок из 8 метрик в IR slave 10 (дата, время, день "
    "недели, смещение UTC). Метрики вычисляет сервер при чтении. Z=0..14 даёт "
    "UTC+00..UTC+14, Z=15..26 даёт UTC−01..UTC−12. Значения живые (WS "
    "<code>/ws/state</code>)."
)
_TZ_NOTE_EN = (
    "Each zone Z=0..26 is a block of 8 metrics in IR of slave 10 (date, time, "
    "weekday, UTC offset). The server computes the metrics on read. Z=0..14 "
    "gives UTC+00..UTC+14, Z=15..26 gives UTC−01..UTC−12. Values are live "
    "(WS <code>/ws/state</code>)."
)
_TZ_TS_NOTE_RU = (
    "IR 300..301 - Unix timestamp (<code>uint32 ABCD</code>, погрешность "
    "0.5 с). Расхождение - разница серверного timestamp с системным временем "
    "браузера."
)
_TZ_TS_NOTE_EN = (
    "IR 300..301 carry the Unix timestamp (<code>uint32 ABCD</code>, 0.5 s "
    "accuracy). Drift is the difference between the server timestamp and the "
    "browser system time."
)


def _timezones_body(lang: Lang) -> str:
    """Страница «Часовые пояса» (TASK-731…732): 27 зон + Unix timestamp.

    Slave 10 отдаёт вычисляемые IR: блоки зон 1..268 и Unix timestamp
    (IR 300..301, `uint32 ABCD`). Расхождение - разница серверного timestamp
    с системным временем браузера.
    """
    placeholder = "-"
    return (
        f'<p class="note">{t(lang, _TZ_NOTE_RU, _TZ_NOTE_EN)}</p>\n'
        f"<h2>Unix timestamp</h2>\n"
        "<dl>\n"
        f'  <dt>IR 300..301 ({t(lang, "сервер", "server")})</dt><dd id="tz-unix">{placeholder}</dd>\n'
        f'  <dt>{t(lang, "Системное время", "System time")}</dt><dd id="tz-system">{placeholder}</dd>\n'
        f'  <dt>{t(lang, "Расхождение", "Drift")}</dt><dd id="tz-delta">{placeholder}</dd>\n'
        "</dl>\n"
        f'<p class="note">{t(lang, _TZ_TS_NOTE_RU, _TZ_TS_NOTE_EN)}</p>\n'
        + _tz_cards(lang)
        + '\n<script type="module" src="/static/timezones.js"></script>'
    )


_INJ_NOTE_RU = (
    "Инъекции (<code>exception</code> / <code>delay</code> / "
    "<code>timeout</code>) влияют на Modbus-запросы выбранного слейва или "
    "диапазона адресов и держатся не дольше "
    "<code>INJECTION_MAX_TTL_S</code>. Инъекции общие для всех пользователей "
    "сервера. REST: <code>POST /api/modbus/inject</code>, снятие "
    "<code>POST /api/modbus/inject/clear</code>."
)
_INJ_NOTE_EN = (
    "Injections (<code>exception</code> / <code>delay</code> / "
    "<code>timeout</code>) affect Modbus requests of the selected slave or "
    "address range and stay active no longer than "
    "<code>INJECTION_MAX_TTL_S</code>. Injections are shared across all "
    "server users. REST: <code>POST /api/modbus/inject</code>, clearing "
    "<code>POST /api/modbus/inject/clear</code>."
)


def _injections_body(lang: Lang) -> str:
    """Страница «Инъекции ошибок» (TASK-743): форма добавления и список активных.

    Типы exception / delay / timeout; цель - весь слейв или диапазон адресов
    по сетке значений. Список наполняет `static/injections.js`
    (REST `GET/POST /api/modbus/inject`, снятие - `POST …/inject/clear`).
    """
    slave_options = "".join(f"<option>{slave_id}</option>" for slave_id in SLAVE_SPECS)
    return (
        f'<p class="note">{t(lang, _INJ_NOTE_RU, _INJ_NOTE_EN)}</p>\n'
        f"<h3>{t(lang, 'Добавить инъекцию', 'Add injection')}</h3>\n"
        '<div class="form-row">\n'
        f'  <label>{t(lang, "Тип", "Type")} <select id="inj-kind">\n'
        f'    <option value="exception">exception · {t(lang, "ответ кодом ошибки", "error-code response")}</option>\n'
        f'    <option value="delay">delay · {t(lang, "задержка ответа", "response delay")}</option>\n'
        f'    <option value="timeout">timeout · {t(lang, "не отвечать", "no response")}</option>\n'
        "  </select></label>\n"
        f'  <label>Unit ID <select id="inj-slave">{slave_options}</select></label>\n'
        f'  <label>{t(lang, "Цель", "Target")} <select id="inj-scope">\n'
        f'    <option value="slave">{t(lang, "весь слейв", "whole slave")}</option>\n'
        f'    <option value="range">{t(lang, "диапазон адресов", "address range")}</option>\n'
        "  </select></label>\n"
        "</div>\n"
        '<div class="form-row">\n'
        f'  <label>{t(lang, "Область", "Area")} <select id="inj-area">\n'
        '    <option value="hr">hr · Holding Registers</option>\n'
        '    <option value="ir">ir · Input Registers</option>\n'
        '    <option value="cl">cl · Coils</option>\n'
        '    <option value="di">di · Discrete Inputs</option>\n'
        "  </select></label>\n"
        f'  <label>{t(lang, "Адрес", "Address")} <input id="inj-address" type="text" value="1"></label>\n'
        f'  <label>{t(lang, "Значений", "Count")} <input id="inj-count" type="text" value="1"></label>\n'
        "</div>\n"
        '<div class="form-row">\n'
        f'  <label>{t(lang, "Код ошибки", "Error code")} <select id="inj-code">\n'
        '    <option value="1">0x01 Illegal Function</option>\n'
        '    <option value="2">0x02 Illegal Data Address</option>\n'
        '    <option value="3">0x03 Illegal Data Value</option>\n'
        '    <option value="4">0x04 Server Device Failure</option>\n'
        '    <option value="6">0x06 Server Device Busy</option>\n'
        "  </select></label>\n"
        f'  <label>{t(lang, "Задержка, мс", "Delay, ms")} <input id="inj-delay" type="text" value="500"></label>\n'
        f'  <label>{t(lang, "TTL, с", "TTL, s")} <input id="inj-ttl" type="text" placeholder="{t(lang, "максимум", "maximum")}"></label>\n'
        "</div>\n"
        '<p class="form-actions">\n'
        f'  <button id="inj-add" type="button">{t(lang, "Добавить инъекцию", "Add injection")}</button>\n'
        f'  <button id="inj-clear-all" type="button">{t(lang, "Сбросить инъекции", "Clear injections")}</button>\n'
        '  <span id="inj-status" role="status"></span>\n'
        "</p>\n"
        f"<h3>{t(lang, 'Активные инъекции', 'Active injections')}</h3>\n"
        '<table class="values">\n'
        f"  <thead><tr><th>ID</th><th>{t(lang, 'Тип', 'Type')}</th><th>Unit ID</th>"
        f"<th>{t(lang, 'Цель', 'Target')}</th>"
        f"<th>{t(lang, 'Параметры', 'Params')}</th><th>{t(lang, 'TTL, с', 'TTL, s')}</th>"
        f"<th>{t(lang, 'Осталось, с', 'Left, s')}</th><th></th></tr></thead>\n"
        '  <tbody id="inj-rows"></tbody>\n'
        "</table>\n"
        '<script type="module" src="/static/injections.js"></script>'
    )


_JOURNAL_NOTE_RU = (
    "Последние <code>JOURNAL_SIZE</code> (1000) записей: записи в регистры, "
    "смена поведений, инъекции и отказы locked-поведений. Журнал живёт в "
    "памяти (перезапуск сервера сбрасывает его) и общий для всех "
    "пользователей. Новые события приходят live по <code>/ws/events</code>, "
    "снимок - <code>GET /api/modbus/journal</code>."
)
_JOURNAL_NOTE_EN = (
    "The last <code>JOURNAL_SIZE</code> (1000) events: register writes, "
    "behavior changes, injections and locked-behavior rejections. The journal "
    "lives in memory (a server restart resets it) and is shared across all "
    "users. New events arrive live over <code>/ws/events</code>, the snapshot "
    "is <code>GET /api/modbus/journal</code>."
)


def _journal_body(lang: Lang) -> str:
    """Страница «Журнал событий» (TASK-745): последние 1000 записей + live.

    Наполняет `static/journal.js`: стартовая загрузка через
    `GET /api/modbus/journal?limit=1000`, новые события - кадрами `/ws/events`
    (TASK-404). Записи общие для всех пользователей, хранение - в памяти.
    """
    return (
        f'<p class="note">{t(lang, _JOURNAL_NOTE_RU, _JOURNAL_NOTE_EN)}</p>\n'
        '<p class="form-actions">\n'
        f'  <button id="journal-refresh" type="button">{t(lang, "Обновить", "Refresh")}</button>\n'
        '  <span id="journal-status" role="status"></span>\n'
        "</p>\n"
        '<table class="values">\n'
        f"  <thead><tr><th>{t(lang, 'Время', 'Time')}</th>"
        f"<th>{t(lang, 'Источник', 'Source')}</th><th>IP</th>"
        f"<th>{t(lang, 'Действие', 'Action')}</th>"
        "<th>Unit ID</th>"
        f"<th>{t(lang, 'Область', 'Area')}</th><th>{t(lang, 'Адрес', 'Address')}</th>"
        f"<th>{t(lang, 'Значение', 'Value')}</th></tr></thead>\n"
        '  <tbody id="journal-rows"></tbody>\n'
        "</table>\n"
        '<script type="module" src="/static/journal.js"></script>'
    )


def _footer(lang: Lang) -> str:
    """Футер в фирменном стиле: продукт, поддержка, ссылки на API сервера."""
    ver = escape(_project_version())
    return (
        "<footer>\n<div class=\"wrap\">\n"
        '  <div class="foot-top">\n'
        '    <div class="foot-brand">MODBUS<span class="dot">·</span>TOOLKIT</div>\n'
        f'    <div class="foot-version">v{ver}</div>\n'
        "  </div>\n"
        '  <div class="foot-grid">\n'
        '    <div class="foot-group">\n'
        f'      <span class="eyebrow">{t(lang, "Продукт", "Product")}</span>\n'
        "      <ul>\n"
        f'        <li><a href="{PRODUCT_URL}">{t(lang, "ModbusToolkit для Windows и Linux", "ModbusToolkit for Windows and Linux")}</a></li>\n'
        f'        <li><a href="{PRODUCT_DOCS_URL}">{t(lang, "Документация продукта", "Product docs")}</a></li>\n'
        f'        <li><a href="{TELEGRAM_URL}">{t(lang, "Telegram-канал", "Telegram channel")}</a></li>\n'
        "      </ul>\n"
        "    </div>\n"
        '    <div class="foot-group">\n'
        f'      <span class="eyebrow">{t(lang, "Поддержка", "Support")}</span>\n'
        "      <ul>\n"
        f'        <li><a href="{SUPPORT_URL}">{t(lang, "Поддержать проект", "Support the project")}</a></li>\n'
        f'        <li>{t(lang, "Донаты идут на испытательный стенд", "Donations fund the test bench")}</li>\n'
        "      </ul>\n"
        "    </div>\n"
        '    <div class="foot-group">\n'
        f'      <span class="eyebrow">{t(lang, "Этот сервер", "This server")}</span>\n'
        "      <ul>\n"
        f'        <li><a href="/api/docs">REST API (Swagger)</a></li>\n'
        f'        <li><a href="/docs">{t(lang, "Документация сервера", "Server docs")}</a></li>\n'
        "      </ul>\n"
        "    </div>\n"
        "  </div>\n"
        "</div>\n</footer>"
    )


def _shell(title: str, active: str, body: str, lang: Lang) -> str:
    """Каркас страницы: topbar с брендом, nav, плашка, контент, донат, футер."""
    nav = "".join(
        f'<a href="{href}" class="nav-link{" active" if href == active else ""}">'
        f"{t(lang, ru, en)}</a>"
        for href, ru, en in NAV_SECTIONS
    )
    site = t(lang, "Публичный тестовый Modbus-TCP сервер", "Public Modbus-TCP test server")
    warning = escape(WARNING_BANNER if lang == "ru" else WARNING_BANNER_EN)
    return f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} · {escape(site)}</title>
<link rel="icon" href="/static/favicon.ico" sizes="any">
<link rel="stylesheet" href="/static/app.css">
<script>window.LANG = "{lang}";</script>
<script src="/static/i18n.js"></script>
</head>
<body>
<header class="topbar">
  <div class="wrap topbar-row">
    <a class="brand" href="/">MODBUS<span class="dot">·</span>TOOLKIT</a>
    <span class="topbar-note">{PUBLIC_ENDPOINT}</span>
    <p class="lang-switch">{lang_links(lang, active)}</p>
  </div>
</header>
<nav>
  <div class="wrap">{nav}</div>
</nav>
<main class="wrap">
<h1>{escape(title)}</h1>
<div class="banner" role="alert">
  <strong>{warning}</strong>
</div>
{body}
{_support_block(lang)}
</main>
{_footer(lang)}
</body>
</html>
"""


def _page(request: Request, title: str, active: str, body: str, lang: Lang) -> HTMLResponse:
    """Ответ страницы: язык из запроса, cookie запоминает выбор."""
    response = HTMLResponse(_shell(title, active, body, lang))
    if "lang" in request.query_params:
        response.set_cookie(COOKIE_NAME, lang, max_age=365 * 24 * 3600, samesite="lax")
    return response


@pages_router.get("/", response_class=HTMLResponse)
def get_index(request: Request, services: ServicesDep) -> HTMLResponse:
    """Обзор: параметры подключения и таблица форматов (TASK-701)."""
    lang = resolve_lang(request)
    config = services.config
    body = _index_body(config.MODBUS_TCP_HOST, config.MODBUS_TCP_PORT, lang)
    return _page(request, t(lang, "Обзор", "Overview"), "/", body, lang)


@pages_router.get("/registers", response_class=HTMLResponse)
def get_registers(request: Request) -> HTMLResponse:
    """Карта регистров: live-таблица адресов по слейву (TASK-711…715)."""
    lang = resolve_lang(request)
    return _page(
        request,
        t(lang, "Карта регистров", "Register map"),
        "/registers",
        _registers_body(lang),
        lang,
    )


@pages_router.get("/behaviors", response_class=HTMLResponse)
def get_behaviors_page(request: Request) -> HTMLResponse:
    """Поведения: форма привязки и таблица переопределений (TASK-721)."""
    lang = resolve_lang(request)
    return _page(
        request,
        t(lang, "Поведения", "Behaviors"),
        "/behaviors",
        _behaviors_body(lang),
        lang,
    )


@pages_router.get("/timezones", response_class=HTMLResponse)
def get_timezones(request: Request) -> HTMLResponse:
    """Часовые пояса, slave 10: 27 зон + Unix timestamp (TASK-731…732)."""
    lang = resolve_lang(request)
    return _page(
        request,
        t(lang, "Часовые пояса", "Time zones"),
        "/timezones",
        _timezones_body(lang),
        lang,
    )


@pages_router.get("/injections", response_class=HTMLResponse)
def get_injections_page(request: Request) -> HTMLResponse:
    """Инъекции ошибок: форма добавления и список активных (TASK-605, TASK-743)."""
    lang = resolve_lang(request)
    return _page(
        request,
        t(lang, "Инъекции ошибок", "Error injections"),
        "/injections",
        _injections_body(lang),
        lang,
    )


@pages_router.get("/journal", response_class=HTMLResponse)
def get_journal_page(request: Request) -> HTMLResponse:
    """Журнал событий: последние 1000 записей + live (TASK-745)."""
    lang = resolve_lang(request)
    return _page(
        request,
        t(lang, "Журнал событий", "Event journal"),
        "/journal",
        _journal_body(lang),
        lang,
    )


@pages_router.get("/tools", response_class=HTMLResponse)
def get_tools(request: Request) -> HTMLResponse:
    """Инструменты наладчика: конвертер байт/слов (TASK-741), генератор (TASK-742)."""
    lang = resolve_lang(request)
    return _page(
        request,
        t(lang, "Инструменты", "Tools"),
        "/tools",
        _tools_body(lang),
        lang,
    )


@pages_router.get("/docs", response_class=HTMLResponse)
def get_docs(request: Request, services: ServicesDep) -> HTMLResponse:
    """Документация: форматы, ограничения, многопользовательность (TASK-751)."""
    lang = resolve_lang(request)
    return _page(
        request,
        t(lang, "Документация", "Docs"),
        "/docs",
        _docs_body(services.config, lang),
        lang,
    )

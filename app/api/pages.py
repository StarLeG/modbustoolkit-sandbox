"""HTML-страницы UI: shell + страницы разделов (Эпик 7, ADR-008).

Shell — общий каркас всех страниц: навигация по разделам, обязательная плашка
`WARNING_BANNER` (ADR-001 п. 4, TASK-504), подключение статики `/static`.
Страницы рендерятся сервером (f-строки), живые данные подтягивает JS из REST
и WS. «Обзор» (TASK-701) — параметры подключения и таблица форматов из
`SLAVE_SPECS`; остальные разделы наполняются задачами 7.2–7.6.
"""
from __future__ import annotations

from html import escape

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.api.routes import ServicesDep
from app.modbus.types import SLAVE_SPECS, AddressRange, RegisterFormat

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


def _index_body(host: str, port: int) -> str:
    return f"""
<h2>Как подключиться</h2>
<dl>
  <dt>Хост</dt><dd><code>{escape(host)}</code></dd>
  <dt>Порт</dt><dd><code>{port}</code></dd>
  <dt>Unit ID</dt><dd><code>1..10</code> (slave 10 — только input registers)</dd>
  <dt>Функции</dt><dd><code>1, 2, 3, 4, 5, 6, 16</code> — остальные отклоняются</dd>
</dl>

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
    """Карта регистров (TASK-711…715) — заглушка до наполнения."""
    return HTMLResponse(_shell("Карта регистров", "/registers", _stub_body("Карта регистров")))


@pages_router.get("/behaviors", response_class=HTMLResponse)
def get_behaviors_page() -> HTMLResponse:
    """Поведения (TASK-721…724) — заглушка до наполнения."""
    return HTMLResponse(_shell("Поведения", "/behaviors", _stub_body("Поведения")))


@pages_router.get("/timezones", response_class=HTMLResponse)
def get_timezones() -> HTMLResponse:
    """Часовые пояса, slave 10 (TASK-731…732) — заглушка до наполнения."""
    return HTMLResponse(_shell("Часовые пояса", "/timezones", _stub_body("Часовые пояса")))


@pages_router.get("/injections", response_class=HTMLResponse)
def get_injections_page() -> HTMLResponse:
    """Инъекции ошибок: кнопка сброса (TASK-605); полная страница — TASK-743."""
    body = f"<h2>Инъекции ошибок</h2>{_INJECTIONS_PANEL}"
    return HTMLResponse(_shell("Инъекции", "/injections", body))


@pages_router.get("/tools", response_class=HTMLResponse)
def get_tools() -> HTMLResponse:
    """Инструменты наладчика (TASK-741…742) — заглушка до наполнения."""
    return HTMLResponse(_shell("Инструменты", "/tools", _stub_body("Инструменты наладчика")))


@pages_router.get("/docs", response_class=HTMLResponse)
def get_docs() -> HTMLResponse:
    """Документация (TASK-751…752) — заглушка до наполнения."""
    return HTMLResponse(_shell("Документация", "/docs", _stub_body("Документация")))

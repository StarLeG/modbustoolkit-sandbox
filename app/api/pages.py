"""Стартовая страница с обязательной плашкой «публичный сервер» (TASK-504).

Плашка предупреждает о многопользовательском режиме и перезаписи значений
(инвариант 1 CLAUDE.md, ADR-001 п. 4, DoD 10). Текст — константа
`WARNING_BANNER`; её переиспользует UI (Эпик 7). Страница минимальна и
служит заглушкой до полного UI.
"""
from __future__ import annotations

from html import escape

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.api.routes import ServicesDep

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


#: Панель «Инъекции ошибок» с кнопкой сброса (TASK-605). Обычная строка (без
#: f-префикса): фигурные скобки JS остаются литеральными.
_INJECTIONS_PANEL = """
<h2>Инъекции ошибок</h2>
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


def _page(host: str, port: int) -> str:
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Публичный тестовый Modbus-TCP сервер</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 46rem;
         padding: 0 1rem; line-height: 1.5; color: #1a1a1a; }}
  h1 {{ font-size: 1.5rem; margin-bottom: 0; }}
  .subtitle {{ color: #666; margin-top: 0.25rem; }}
  .banner {{ border: 2px solid #b45309; background: #fef3c7; border-radius: 8px;
             padding: 1rem 1.25rem; margin: 1.5rem 0; }}
  .banner strong {{ color: #92400e; }}
  dl {{ display: grid; grid-template-columns: max-content 1fr; gap: 0.25rem 1rem; }}
  dt {{ font-weight: 600; }}
  code {{ background: #f3f4f6; padding: 0.1rem 0.35rem; border-radius: 4px; }}
</style>
</head>
<body>
<h1>Публичный тестовый Modbus-TCP сервер</h1>
<p class="subtitle">modbustoolkit.electroman.by</p>

<div class="banner" role="alert">
  <strong>⚠ {escape(WARNING_BANNER)}</strong>
</div>

<h2>Как подключиться</h2>
<dl>
  <dt>Хост</dt><dd><code>{escape(host)}</code></dd>
  <dt>Порт</dt><dd><code>{port}</code></dd>
  <dt>Unit ID</dt><dd><code>1..10</code> (slave 10 — только input registers)</dd>
  <dt>Функции</dt><dd><code>1, 2, 3, 4, 5, 6, 16</code> — остальные отклоняются</dd>
</dl>

{_INJECTIONS_PANEL}

<p>Поведения регистров, инъекции ошибок и live-состояние — в web-интерфейсе.</p>
</body>
</html>
"""


@pages_router.get("/", response_class=HTMLResponse)
def get_index(services: ServicesDep) -> HTMLResponse:
    """Стартовая страница с плашкой «публичный сервер» (TASK-504)."""
    config = services.config
    return HTMLResponse(_page(config.MODBUS_TCP_HOST, config.MODBUS_TCP_PORT))

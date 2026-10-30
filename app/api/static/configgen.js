// Инструменты наладчика (TASK-742): генератор конфигов клиентских инструментов.
// Modbus Poll / QModMaster / CAS Scanner — карточки настроек, pyModbus — скрипт,
// Node-RED — flow. Генерация серверная (POST /api/modbus/configgen): цель
// проверяется по SLAVE_SPECS, формат данных берётся из спеки слейва.

function el(id) {
  return document.getElementById(id);
}

function parseNumber(text) {
  const trimmed = text.trim();
  if (trimmed === "") return NaN;
  return Number(trimmed);
}

function collectPayload() {
  const address = parseNumber(el("cfg-address").value);
  const count = parseNumber(el("cfg-count").value);
  if (!Number.isInteger(address) || address < 1) {
    return { error: window.t("Адрес - целое число от 1 и больше (из docs/HRIR_SPEC.md).") };
  }
  if (!Number.isInteger(count) || count < 1) {
    return { error: window.t("Количество значений - целое число от 1 и больше.") };
  }
  return {
    payload: {
      tool: el("cfg-tool").value,
      slave: Number(el("cfg-slave").value),
      area: el("cfg-area").value,
      address,
      count,
    },
  };
}

let downloadUrl = null;

function renderResult(result) {
  el("cfg-output").textContent = result.content;
  if (downloadUrl) URL.revokeObjectURL(downloadUrl);
  downloadUrl = URL.createObjectURL(new Blob([result.content], { type: "text/plain" }));
  const link = el("cfg-download");
  link.href = downloadUrl;
  link.download = result.filename;
}

async function generate() {
  const status = el("cfg-status");
  const { payload, error } = collectPayload();
  if (error) {
    status.textContent = error;
    return;
  }
  const response = await fetch("/api/modbus/configgen", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    status.textContent =
      (body && body.detail) || window.t("Ошибка генерации ({status}).", { status: response.status });
    return;
  }
  renderResult(await response.json());
  status.textContent = window.t("Готово: конфиг можно копировать или скачать.");
}

async function copyOutput() {
  const status = el("cfg-status");
  const content = el("cfg-output").textContent;
  if (!content) {
    status.textContent = window.t("Сначала сгенерируйте конфиг.");
    return;
  }
  await navigator.clipboard.writeText(content);
  status.textContent = window.t("Скопировано в буфер обмена.");
}

function init() {
  el("cfg-generate").addEventListener("click", generate);
  el("cfg-copy").addEventListener("click", copyOutput);
  for (const id of ["cfg-address", "cfg-count"]) {
    el(id).addEventListener("keydown", (event) => {
      if (event.key === "Enter") generate();
    });
  }
}

init();

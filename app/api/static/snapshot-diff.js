// Инструменты наладчика (TASK-744): дифф двух снимков состояния.
// Два JSON-файла снимков (POST /api/modbus/snapshot) → POST /api/modbus/snapshot/diff;
// в таблицу попадают только различия. «Снять текущий снимок» скачивает
// свежий снимок сервера файлом.

function el(id) {
  return document.getElementById(id);
}

async function readSnapshot(inputId) {
  const input = el(inputId);
  if (!input.files || input.files.length === 0) {
    throw new Error("Выберите файл снимка: " + inputId.replace("diff-file-", "") + ".");
  }
  const text = await input.files[0].text();
  try {
    return JSON.parse(text);
  } catch (error) {
    throw new Error("Файл не разбирается как JSON: " + error);
  }
}

function fmtValue(value) {
  return value === null || value === undefined ? "—" : String(value);
}

function renderRows(rows) {
  const body = el("diff-rows");
  body.textContent = "";
  for (const row of rows) {
    const tr = document.createElement("tr");
    for (const cell of [
      String(row.slave),
      row.area,
      String(row.address),
      fmtValue(row.a),
      fmtValue(row.b),
    ]) {
      const td = document.createElement("td");
      td.textContent = cell;
      tr.appendChild(td);
    }
    body.appendChild(tr);
  }
}

async function compare() {
  const status = el("diff-status");
  const summary = el("diff-summary");
  let a;
  let b;
  try {
    a = await readSnapshot("diff-file-a");
    b = await readSnapshot("diff-file-b");
  } catch (error) {
    status.textContent = String(error.message || error);
    return;
  }
  const response = await fetch("/api/modbus/snapshot/diff", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ a, b }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    status.textContent =
      (body && body.detail) || "Ошибка сравнения (" + response.status + ").";
    return;
  }
  const result = await response.json();
  renderRows(result.rows);
  summary.textContent =
    "Различий: " + result.rows.length + ", совпадений: " + result.same + ".";
  status.textContent = "Готово.";
}

async function downloadSnapshot() {
  const status = el("diff-status");
  const response = await fetch("/api/modbus/snapshot", { method: "POST" });
  if (!response.ok) {
    status.textContent = "Ошибка снимка (" + response.status + ").";
    return;
  }
  const snapshot = await response.json();
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(snapshot, null, 2)], { type: "application/json" })
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = "snapshot-" + new Date().toISOString().replace(/[:.]/g, "-") + ".json";
  link.click();
  URL.revokeObjectURL(url);
  status.textContent = "Снимок скачан.";
}

function init() {
  el("diff-compare").addEventListener("click", compare);
  el("diff-snapshot").addEventListener("click", downloadSnapshot);
}

init();

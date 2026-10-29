// Мини-график выбранного регистра за 60 с (TASK-724): canvas, без библиотек.
// Источник — WS /ws/state с подпиской на адрес из формы поведений
// (bh-slave / bh-area / bh-address); при смене адреса подписка обновляется.
// История — кольцевой буфер 60 с, перерисовка раз в 500 мс.

const WINDOW_S = 60;
const HISTORY_S = 60;

const history = []; // [{t, v}]
let ws = null;
let reconnectTimer = null;

function el(id) {
  return document.getElementById(id);
}

function target() {
  return {
    slave: Number(el("bh-slave").value),
    area: el("bh-area").value,
    address: Number(el("bh-address").value),
  };
}

// Адрес должен быть в сетке значений (spec.has_address): подписка на чужой
// адрес сервер отклоняет (error + close 1008) — не шлём такую.
function validTarget(current) {
  const spec = window.REGISTER_MAP[String(current.slave)];
  const ranges = (spec && spec.areas[current.area]) || [];
  for (const [start, stop, step] of ranges) {
    if (current.address >= start && current.address <= stop && (current.address - start) % step === 0) {
      return true;
    }
  }
  return false;
}

function subscribe() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  const current = target();
  if (!validTarget(current)) return;
  ws.send(JSON.stringify({ slave: current.slave, areas: [current.area], addresses: [current.address] }));
}

function connect() {
  if (ws) {
    ws.onclose = null;
    ws.close();
    ws = null;
  }
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws/state");
  ws.onopen = subscribe;
  ws.onmessage = function (event) {
    const frame = JSON.parse(event.data);
    if (frame.type !== "state") return;
    const current = target();
    if (frame.slave !== current.slave) return;
    const area = frame.areas && frame.areas[current.area];
    if (!area) return;
    const raw = area[String(current.address)];
    if (raw === undefined) return;
    const value = raw === true ? 1 : raw === false ? 0 : Number(raw);
    const now = Date.now();
    history.push({ t: now, v: value });
    while (history.length > 0 && history[0].t < now - HISTORY_S * 1000) {
      history.shift();
    }
  };
  ws.onclose = function () {
    // сеть/сервер недоступны — повтор через 3 с
    reconnectTimer = setTimeout(connect, 3000);
  };
}

function draw() {
  const canvas = el("bh-chart");
  const ctx = canvas.getContext("2d");
  const width = canvas.clientWidth || canvas.width;
  const height = canvas.height;
  if (canvas.width !== width) canvas.width = width;
  ctx.clearRect(0, 0, width, height);

  const now = Date.now();
  const from = now - WINDOW_S * 1000;
  const points = history.filter((point) => point.t >= from);

  ctx.font = "13px system-ui, sans-serif";
  const current = target();
  const label = "slave " + current.slave + " " + current.area + " " + current.address;
  if (points.length === 0) {
    ctx.fillStyle = "#666";
    ctx.fillText("Нет данных за " + WINDOW_S + " с: " + label, 12, 24);
    return;
  }

  let min = Infinity;
  let max = -Infinity;
  for (const point of points) {
    if (point.v < min) min = point.v;
    if (point.v > max) max = point.v;
  }
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const pad = (max - min) * 0.1;
  min -= pad;
  max += pad;

  const plot = { left: 48, top: 12, right: width - 12, bottom: height - 28 };
  ctx.strokeStyle = "#d1d5db";
  ctx.strokeRect(plot.left, plot.top, plot.right - plot.left, plot.bottom - plot.top);

  ctx.fillStyle = "#666";
  ctx.font = "11px system-ui, sans-serif";
  ctx.fillText(max.toFixed(1), 4, plot.top + 10);
  ctx.fillText(min.toFixed(1), 4, plot.bottom);
  ctx.fillText("−" + WINDOW_S + " с", plot.left, height - 8);
  ctx.fillText("сейчас", plot.right - 38, height - 8);

  ctx.strokeStyle = "#1d4ed8";
  ctx.beginPath();
  for (let i = 0; i < points.length; i++) {
    const point = points[i];
    const x = plot.left + ((point.t - from) / (now - from)) * (plot.right - plot.left);
    const y = plot.bottom - ((point.v - min) / (max - min)) * (plot.bottom - plot.top);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.stroke();

  ctx.fillStyle = "#1a1a1a";
  ctx.font = "12px system-ui, sans-serif";
  ctx.fillText(label + " — " + points[points.length - 1].v, plot.left + 8, plot.top + 14);
}

function init() {
  for (const id of ["bh-slave", "bh-area", "bh-address"]) {
    el(id).addEventListener("change", function () {
      history.length = 0;
      subscribe();
    });
  }
  connect();
  setInterval(draw, 500);
  draw();
}

init();

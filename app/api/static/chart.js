// Live-график регистров из избранного (TASK-715): canvas, без библиотек.
// История копится из событий "watch-value" (watchlist.js, WS /ws/state):
// кольцевой буфер на 300 с на регистр, окно показа — 60/120/300 с.

const COLORS = [
  "#1d4ed8",
  "#b45309",
  "#047857",
  "#b91c1c",
  "#6d28d9",
  "#0e7490",
  "#be185d",
  "#4d7c0f",
];
const HISTORY_S = 300;

const history = new Map(); // key -> [{t, v}]
const state = { window_s: 60 };

function el(id) {
  return document.getElementById(id);
}

function colorFor(key) {
  const keys = [...history.keys()];
  return COLORS[Math.max(0, keys.indexOf(key)) % COLORS.length];
}

document.addEventListener("watch-value", function (event) {
  const detail = event.detail;
  const key = detail.slave + "/" + detail.area + "/" + detail.address;
  const numeric = detail.value === true ? 1 : detail.value === false ? 0 : Number(detail.value);
  if (!history.has(key)) history.set(key, []);
  const series = history.get(key);
  const now = Date.now();
  series.push({ t: now, v: numeric });
  while (series.length > 0 && series[0].t < now - HISTORY_S * 1000) {
    series.shift();
  }
});

function draw() {
  const canvas = el("chart-canvas");
  const ctx = canvas.getContext("2d");
  const width = canvas.clientWidth || canvas.width;
  const height = canvas.height;
  if (canvas.width !== width) canvas.width = width;
  ctx.clearRect(0, 0, width, height);

  const now = Date.now();
  const from = now - state.window_s * 1000;
  const visible = [];
  for (const [key, series] of history) {
    const points = series.filter((point) => point.t >= from);
    if (points.length > 0) visible.push({ key: key, points: points });
  }

  ctx.font = "13px system-ui, sans-serif";
  if (visible.length === 0) {
    ctx.fillStyle = "#666";
    ctx.fillText(
      window.t("Нет данных: добавьте регистры ★ в избранное - график накопит значения по WS"),
      12,
      24
    );
    return;
  }

  let min = Infinity;
  let max = -Infinity;
  for (const entry of visible) {
    for (const point of entry.points) {
      if (point.v < min) min = point.v;
      if (point.v > max) max = point.v;
    }
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
  ctx.fillText(window.t("{v} с", { v: "−" + state.window_s }), plot.left, height - 8);
  ctx.fillText(window.t("сейчас"), plot.right - 38, height - 8);

  for (const entry of visible) {
    ctx.strokeStyle = colorFor(entry.key);
    ctx.beginPath();
    for (let i = 0; i < entry.points.length; i++) {
      const point = entry.points[i];
      const x = plot.left + ((point.t - from) / (now - from)) * (plot.right - plot.left);
      const y = plot.bottom - ((point.v - min) / (max - min)) * (plot.bottom - plot.top);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
  }

  let legendY = plot.top + 14;
  ctx.font = "12px system-ui, sans-serif";
  for (const entry of visible) {
    ctx.fillStyle = colorFor(entry.key);
    ctx.fillRect(plot.left + 8, legendY - 9, 10, 10);
    ctx.fillStyle = "#1a1a1a";
    ctx.fillText(entry.key, plot.left + 24, legendY);
    legendY += 16;
  }
}

function init() {
  el("chart-window").addEventListener("change", function () {
    state.window_s = Number(this.value);
  });
  setInterval(draw, 500);
  draw();
}

init();

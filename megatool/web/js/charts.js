/* Собственная библиотека графиков на canvas: без внешних зависимостей.
   Стиль — классический чёрно-белый: серии различаются оттенками серого и пунктиром.
   Тема читается из CSS-переменных, так что переключение светлой/тёмной схемы
   мгновенно отражается на графиках. */

function theme() {
  const styles = getComputedStyle(document.documentElement);
  const value = (name, fallback) => (styles.getPropertyValue(name) || "").trim() || fallback;
  return {
    fg: value("--chart-fg", "#111"),
    muted: value("--chart-muted", "#888"),
    grid: value("--chart-grid", "#e2e2e2"),
    panel: value("--panel", "#fff"),
    border: value("--border", "#111"),
    series: [1, 2, 3, 4, 5, 6].map((index) => value(`--chart-${index}`, "#333")),
    dashes: [
      [],
      [7, 4],
      [2, 3],
      [10, 4, 2, 4],
      [4, 2, 1, 2],
      [1, 3],
    ],
  };
}

function fitCanvas(canvas) {
  const ratio = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(60, Math.floor(rect.width));
  const height = Math.max(40, Math.floor(rect.height));
  if (canvas.width !== Math.floor(width * ratio) || canvas.height !== Math.floor(height * ratio)) {
    canvas.width = Math.floor(width * ratio);
    canvas.height = Math.floor(height * ratio);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { ctx, width, height };
}

/** Подписи оси Y «круглыми» числами. */
function niceScale(min, max, ticks = 4) {
  if (!isFinite(min) || !isFinite(max)) return { min: 0, max: 1, step: 0.25 };
  if (min === max) {
    if (min === 0) return { min: 0, max: 1, step: 0.25 };
    min = Math.min(0, min);
    max = Math.max(min * 1.2, max * 1.2);
  }
  const span = max - min;
  const rawStep = span / ticks;
  const magnitude = 10 ** Math.floor(Math.log10(rawStep));
  const candidates = [1, 2, 2.5, 5, 10].map((factor) => factor * magnitude);
  const step = candidates.find((item) => item >= rawStep) || candidates[candidates.length - 1];
  const niceMin = Math.floor(min / step) * step;
  const niceMax = Math.ceil(max / step) * step;
  return { min: niceMin, max: niceMax, step };
}

function drawLine(ctx, points, color, dash, alpha = 1) {
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.6;
  ctx.setLineDash(dash || []);
  ctx.beginPath();
  let started = false;
  for (const item of points) {
    if (item === null) {
      started = false;
      continue;
    }
    if (!started) {
      ctx.moveTo(item[0], item[1]);
      started = true;
    } else {
      ctx.lineTo(item[0], item[1]);
    }
  }
  ctx.stroke();
  ctx.restore();
}

function fmtClock(seconds) {
  const date = new Date(seconds * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

/* --------------------------------------------------------------------- */
/*  Линейный график с несколькими сериями, наведением и перекрестием      */
/* --------------------------------------------------------------------- */

export class LineChart {
  constructor(canvas, options = {}) {
    this.canvas = canvas;
    this.options = Object.assign(
      {
        height: 170,
        format: (value) => (value >= 100 ? String(Math.round(value)) : String(Math.round(value * 10) / 10)),
        tooltip: true,
        legend: true,
        zero: true,
        maxPoints: 720,
        yMax: null,
        area: true,
      },
      options
    );
    this.data = { ts: [], series: [], labels: [] };
    this.hover = null;
    this._bind();
    new ResizeObserver(() => this.draw()).observe(canvas);
    window.addEventListener("megatool:theme", () => this.draw());
  }

  _bind() {
    this.canvas.addEventListener("mousemove", (event) => {
      const rect = this.canvas.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const plot = this._plot(rect.width);
      if (x < plot.left || x > plot.right || !this.data.ts.length) {
        return;
      }
      const ratio = (x - plot.left) / Math.max(1, plot.right - plot.left);
      const index = Math.round(ratio * (this.data.ts.length - 1));
      this.hover = { x, index: Math.max(0, Math.min(this.data.ts.length - 1, index)) };
      this.draw();
    });
    this.canvas.addEventListener("mouseleave", () => {
      this.hover = null;
      this.draw();
    });
  }

  setTitle(text) {
    this.title = text;
    this.draw();
  }

  /** payload: {ts: number[], series: {key: number|null[]}, labels: {key: string}} */
  setData(payload, keys = null) {
    const ts = (payload && payload.ts) || [];
    const seriesData = (payload && payload.series) || {};
    const labels = (payload && payload.labels) || {};
    const active = keys || this.options.keys || Object.keys(seriesData);
    const start = Math.max(0, ts.length - this.options.maxPoints);
    this.data = {
      ts: ts.slice(start),
      labels: active.map((key) => labels[key] || key),
      series: active.map((key) => (seriesData[key] || []).slice(start)),
    };
    this.draw();
  }

  _plot(width) {
    const left = 46;
    const right = width - 8;
    return { left, right, top: 10, bottomLeft: left };
  }

  draw() {
    const { ctx, width, height } = fitCanvas(this.canvas);
    const t = theme();
    ctx.clearRect(0, 0, width, height);
    const plot = this._plot(width);
    const top = this.title ? 22 : 6;
    const bottom = height - 16;
    const { ts, series, labels } = this.data;

    if (this.title) {
      ctx.fillStyle = t.muted;
      ctx.font = "11px system-ui, sans-serif";
      ctx.textAlign = "left";
      ctx.textBaseline = "top";
      ctx.fillText(this.title, 8, 5);
    }

    if (!ts.length || !series.length) {
      ctx.fillStyle = t.muted;
      ctx.font = "12px system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText("нет данных", width / 2, height / 2);
      return;
    }

    let min = Infinity;
    let max = -Infinity;
    for (const values of series) {
      for (const value of values) {
        if (value === null || value === undefined || !isFinite(value)) continue;
        if (value < min) min = value;
        if (value > max) max = value;
      }
    }
    if (min === Infinity) {
      min = 0;
      max = 1;
    }
    if (this.options.zero) min = Math.min(0, min);
    const scale = niceScale(min, this.options.yMax !== null ? this.options.yMax : max);

    const yAt = (value) => bottom - ((value - scale.min) / Math.max(1e-9, scale.max - scale.min)) * (bottom - top);
    const xAt = (index) => plot.left + (index / Math.max(1, ts.length - 1)) * (plot.right - plot.left);

    // сетка и подписи Y
    ctx.font = "10px system-ui, sans-serif";
    ctx.fillStyle = t.muted;
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    for (let value = scale.min; value <= scale.max + 1e-9; value += scale.step) {
      const y = yAt(value);
      ctx.strokeStyle = t.grid;
      ctx.lineWidth = 1;
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.moveTo(plot.left, y);
      ctx.lineTo(plot.right, y);
      ctx.stroke();
      ctx.fillText(this.options.format(value), plot.left - 6, y);
    }

    // подписи X
    ctx.textAlign = "center";
    ctx.textBaseline = "bottom";
    const steps = Math.max(2, Math.min(6, Math.floor((plot.right - plot.left) / 110)));
    for (let step = 0; step <= steps; step += 1) {
      const index = Math.round((step / steps) * (ts.length - 1));
      ctx.fillText(fmtClock(ts[index]), xAt(index), height - 3);
    }

    // область и линии
    series.forEach((values, seriesIndex) => {
      const color = t.series[seriesIndex % t.series.length];
      const dash = t.dashes[seriesIndex % t.dashes.length];
      const points = values.map((value, index) =>
        value === null || value === undefined || !isFinite(value) ? null : [xAt(index), yAt(value)]
      );
      if (this.options.area && series.length <= 2) {
        ctx.save();
        ctx.globalAlpha = seriesIndex === 0 ? 0.08 : 0.05;
        ctx.fillStyle = color;
        ctx.beginPath();
        let open = false;
        points.forEach((point) => {
          if (!point) return;
          if (!open) {
            ctx.moveTo(point[0], bottom);
            ctx.lineTo(point[0], point[1]);
            open = true;
          } else {
            ctx.lineTo(point[0], point[1]);
          }
        });
        if (open) {
          const last = points.filter(Boolean).pop();
          if (last) {
            ctx.lineTo(last[0], bottom);
            ctx.closePath();
            ctx.fill();
          }
        }
        ctx.restore();
      }
      drawLine(ctx, points, color, dash);
    });

    // легенда
    if (this.options.legend && labels.length > 1) {
      let offset = 0;
      ctx.font = "10px system-ui, sans-serif";
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      const items = labels.map((label, index) => ({ label, index, width: ctx.measureText(label).width + 26 }));
      let totalWidth = items.reduce((sum, item) => sum + item.width, 0);
      let startX = Math.max(plot.left, plot.right - totalWidth);
      if (totalWidth > plot.right - plot.left) startX = plot.left;
      for (const item of items) {
        const y = top + 4;
        ctx.strokeStyle = t.series[item.index % t.series.length];
        ctx.lineWidth = 1.6;
        ctx.setLineDash(t.dashes[item.index % t.dashes.length]);
        ctx.beginPath();
        ctx.moveTo(startX + offset, y);
        ctx.lineTo(startX + offset + 16, y);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = t.muted;
        ctx.fillText(item.label, startX + offset + 20, y);
        offset += item.width;
      }
    }

    // наведение
    if (this.hover && this.hover.index < ts.length) {
      const index = this.hover.index;
      const x = xAt(index);
      ctx.save();
      ctx.strokeStyle = t.fg;
      ctx.globalAlpha = 0.45;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(x, top);
      ctx.lineTo(x, bottom);
      ctx.stroke();
      ctx.restore();

      if (this.options.tooltip) {
        const rows = labels.map((label, seriesIndex) => ({
          label,
          value: series[seriesIndex][index],
          color: t.series[seriesIndex % t.series.length],
        }));
        const lines = [fmtClock(ts[index]), ...rows.map((row) => `${row.label}: ${row.value === null || row.value === undefined ? "—" : this.options.format(row.value)}`)];
        ctx.font = "11px system-ui, sans-serif";
        const boxWidth = Math.max(...lines.map((line) => ctx.measureText(line).width)) + 16;
        const boxHeight = lines.length * 14 + 8;
        let boxX = x + 10;
        if (boxX + boxWidth > plot.right) boxX = x - boxWidth - 10;
        const boxY = top + 4;
        ctx.fillStyle = t.panel;
        ctx.globalAlpha = 0.95;
        ctx.fillRect(boxX, boxY, boxWidth, boxHeight);
        ctx.globalAlpha = 1;
        ctx.strokeStyle = t.border;
        ctx.lineWidth = 1;
        ctx.strokeRect(boxX + 0.5, boxY + 0.5, boxWidth - 1, boxHeight - 1);
        ctx.textAlign = "left";
        ctx.textBaseline = "top";
        lines.forEach((line, lineIndex) => {
          ctx.fillStyle = lineIndex === 0 ? t.muted : t.fg;
          ctx.fillText(line, boxX + 8, boxY + 5 + lineIndex * 14);
          if (lineIndex > 0) {
            ctx.fillStyle = rows[lineIndex - 1].color;
            ctx.fillRect(boxX + 1, boxY + 6 + lineIndex * 14 + 4, 3, 6);
          }
        });
      }
    }
  }
}

/* --------------------------------------------------------------------- */
/*  Полукруглый индикатор (загрузка CPU, памяти, диска)                    */
/* --------------------------------------------------------------------- */

export class Gauge {
  constructor(canvas, options = {}) {
    this.canvas = canvas;
    this.options = Object.assign({ max: 100, label: "", unit: "%", format: (v) => Math.round(v) }, options);
    this.value = null;
    new ResizeObserver(() => this.draw()).observe(canvas);
    window.addEventListener("megatool:theme", () => this.draw());
  }

  setValue(value, { detail = "" } = {}) {
    this.value = value;
    this.detail = detail;
    this.draw();
  }

  draw() {
    const { ctx, width, height } = fitCanvas(this.canvas);
    const t = theme();
    ctx.clearRect(0, 0, width, height);
    const centerX = width / 2;
    const centerY = height * 0.86;
    const radius = Math.min(width / 2 - 10, centerY - 12);
    const start = Math.PI;
    const end = 2 * Math.PI;

    ctx.lineWidth = 9;
    ctx.lineCap = "butt";
    ctx.strokeStyle = t.grid;
    ctx.beginPath();
    ctx.arc(centerX, centerY, radius, start, end);
    ctx.stroke();

    if (this.value !== null && isFinite(this.value)) {
      const ratio = Math.max(0, Math.min(1, this.value / this.options.max));
      ctx.strokeStyle = t.fg;
      ctx.beginPath();
      ctx.arc(centerX, centerY, radius, start, start + ratio * (end - start));
      ctx.stroke();
    }

    // деления
    ctx.strokeStyle = t.muted;
    ctx.lineWidth = 1;
    for (let step = 0; step <= 4; step += 1) {
      const angle = start + (step / 4) * (end - start);
      const x1 = centerX + Math.cos(angle) * (radius - 12);
      const y1 = centerY + Math.sin(angle) * (radius - 12);
      const x2 = centerX + Math.cos(angle) * (radius - 6);
      const y2 = centerY + Math.sin(angle) * (radius - 6);
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y2);
      ctx.stroke();
    }

    ctx.textAlign = "center";
    ctx.fillStyle = t.fg;
    ctx.font = "600 22px ui-monospace, Consolas, monospace";
    ctx.textBaseline = "alphabetic";
    const text = this.value === null || !isFinite(this.value) ? "—" : `${this.options.format(this.value)}${this.options.unit}`;
    ctx.fillText(text, centerX, centerY - 6);
    ctx.fillStyle = t.muted;
    ctx.font = "11px system-ui, sans-serif";
    ctx.fillText(this.options.label, centerX, centerY + 12);
    if (this.detail) {
      ctx.fillStyle = t.muted;
      ctx.fillText(this.detail, centerX, centerY + 26);
    }
  }
}

/* --------------------------------------------------------------------- */
/*  Искра (мини-график в строке таблицы)                                   */
/* --------------------------------------------------------------------- */

export class Sparkline {
  constructor(canvas, options = {}) {
    this.canvas = canvas;
    this.options = Object.assign({ colorIndex: 0 }, options);
    window.addEventListener("megatool:theme", () => this.draw());
  }

  setData(values) {
    this.values = (values || []).filter((value) => value !== null && value !== undefined && isFinite(value));
    this.draw();
  }

  draw() {
    const { ctx, width, height } = fitCanvas(this.canvas);
    const t = theme();
    ctx.clearRect(0, 0, width, height);
    const values = this.values || [];
    if (values.length < 2) {
      ctx.strokeStyle = t.grid;
      ctx.beginPath();
      ctx.moveTo(0, height - 2);
      ctx.lineTo(width, height - 2);
      ctx.stroke();
      return;
    }
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    const points = values.map((value, index) => [
      (index / (values.length - 1)) * width,
      height - 2 - ((value - min) / span) * (height - 4),
    ]);
    drawLine(ctx, points, t.series[this.options.colorIndex % t.series.length], []);
  }
}

/* --------------------------------------------------------------------- */
/*  Вертикальные столбики (загрузка по ядрам)                              */
/* --------------------------------------------------------------------- */

export class Bars {
  constructor(canvas, options = {}) {
    this.canvas = canvas;
    this.options = Object.assign({ max: 100, gap: 2 }, options);
    this.values = [];
    this.labels = [];
    new ResizeObserver(() => this.draw()).observe(canvas);
    window.addEventListener("megatool:theme", () => this.draw());
  }

  setData(values, labels = []) {
    this.values = values || [];
    this.labels = labels;
    this.draw();
  }

  draw() {
    const { ctx, width, height } = fitCanvas(this.canvas);
    const t = theme();
    ctx.clearRect(0, 0, width, height);
    const values = this.values;
    if (!values.length) {
      ctx.fillStyle = t.muted;
      ctx.font = "10px system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("нет данных", width / 2, height / 2);
      return;
    }
    const columns = values.length;
    const cell = (width - this.options.gap * (columns - 1)) / columns;
    const isSmall = columns > 16;
    values.forEach((value, index) => {
      const ratio = Math.max(0, Math.min(1, (value || 0) / this.options.max));
      const x = index * (cell + this.options.gap);
      const barHeight = ratio * (height - (isSmall ? 6 : 14));
      ctx.fillStyle = t.grid;
      ctx.fillRect(x, height - (isSmall ? 4 : 12), cell, isSmall ? 4 : 12);
      ctx.fillStyle = ratio > 0.85 ? t.fg : t.series[2 % t.series.length];
      ctx.fillRect(x, height - (isSmall ? 4 : 12) - barHeight, cell, barHeight);
      if (!isSmall) {
        ctx.fillStyle = t.muted;
        ctx.font = "9px system-ui, sans-serif";
        ctx.textAlign = "center";
        ctx.fillText(String(index), x + cell / 2, height - 1);
      }
    });
  }
}

export { fmtClock };

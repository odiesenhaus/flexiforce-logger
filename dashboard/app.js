import {
  ARC_POSITIONS_IN, COLOR_RANGE, CYLINDER, FORCE_COLOR_RANGE, FORCE_UNIT, NEWTON_COLOR_RANGE, PRESSURE_COLOR_RANGE,
  SATURATION_LEVEL, SENSING_DIAMETER_MM, SENSOR_HEAD_DIAMETER_IN, SIDES,
} from "/config.js";
import { initCalibration } from "/calibration.js";
import { initGripper } from "/gripper.js";
import { CylinderView, VIEW_PRESETS } from "/model3d.js";

const $ = (id) => document.getElementById(id);
const RADIUS = CYLINDER.diameterIn / 2;
const MAX_CHARTED = 4; // more lines than this get hard to tell apart

const sensors = SIDES.flatMap((side) => {
  const base = (side.angleDeg * Math.PI) / 180;
  const direction = Math.cos(base) >= 0 ? 1 : -1; // so positive arc is up on both sides
  return side.channels.map((channel, i) => ({
    channel,
    side: side.name,
    arc: ARC_POSITIONS_IN[i],
    angle: base + (direction * ARC_POSITIONS_IN[i]) / RADIUS,
    location: `${side.name}, ${formatArc(ARC_POSITIONS_IN[i])}`,
  }));
});
const CHANNELS = sensors.map((s) => s.channel).sort((a, b) => a - b);
const COUNT = Math.max(...CHANNELS);

function formatArc(arc) {
  if (arc === 0) return "middle";
  return `${arc > 0 ? "+" : "−"}${Math.abs(arc).toFixed(1)} in`;
}

// ------------------------------- data store -------------------------------

// Readings are stored in volts, as recorded, and converted when shown. That way
// a new calibration applies straight away, to live data and replays alike.
const units = { mode: "V", calibration: {} };

const GRAVITY = 9.80665; // m/s², to turn the calibration's grams into newtons
const SENSING_AREA_MM2 = Math.PI * (SENSING_DIAMETER_MM / 2) ** 2;

// Decimals for tables and for the larger readouts, roughly what each unit can resolve.
const UNIT_INFO = {
  V: { label: "V", table: 3, cell: 2 },
  force: { label: FORCE_UNIT, table: 1, cell: 0 },
  N: { label: "N", table: 3, cell: 2 },
  MPa: { label: "MPa", table: 4, cell: 3 },
};

function toUnits(channel, volts, mode = units.mode) {
  if (mode === "V") return volts;
  const cal = units.calibration[channel];
  if (!cal) return NaN; // NaN shows as "–"
  const grams = (volts - cal.intercept_v) / cal.slope_v_per_g;
  if (mode === "force") return grams;
  const newtons = (grams / 1000) * GRAVITY;
  return mode === "N" ? newtons : newtons / SENSING_AREA_MM2; // N/mm² is MPa
}

const unitLabel = (mode = units.mode) => UNIT_INFO[mode].label;
const isCalibrated = (channel) => units.mode === "V" || Boolean(units.calibration[channel]);

function formatValue(value, kind = "table", mode = units.mode) {
  if (value === null || !Number.isFinite(value)) return "–";
  return value.toFixed(UNIT_INFO[mode][kind]);
}

// Works for both the live stream and a loaded file. `end` is how far along the
// data the view is, which lets replay share all of the drawing code with live.
class Series {
  constructor(capacity = 24000) {
    this.capacity = capacity;
    this.time = new Float64Array(capacity);
    this.values = new Float32Array(capacity * COUNT);
    this.length = 0;
    this.end = 0;
  }

  clear() {
    this.length = 0;
    this.end = 0;
  }

  push(timeMs, channelValues) {
    if (this.length === this.capacity) this.dropOldestHalf();
    this.time[this.length] = timeMs;
    this.values.set(channelValues, this.length * COUNT);
    this.length += 1;
  }

  dropOldestHalf() {
    const keep = this.capacity >> 1;
    const dropped = this.length - keep;
    this.time.copyWithin(0, dropped, this.length);
    this.values.copyWithin(0, dropped * COUNT, this.length * COUNT);
    this.length = keep;
    this.end = Math.max(0, this.end - dropped);
    peak.index = Math.max(0, peak.index - dropped);
  }

  value(index, channel) {
    return toUnits(channel, this.raw(index, channel));
  }

  raw(index, channel) {
    return this.values[index * COUNT + channel - 1];
  }

  lowerBound(timeMs, limit = this.end) {
    let low = 0, high = limit;
    while (low < high) {
      const mid = (low + high) >> 1;
      if (this.time[mid] < timeMs) low = mid + 1;
      else high = mid;
    }
    return low;
  }
}

const live = new Series();
const liveMarkers = [];
const peak = { values: new Float32Array(COUNT), raw: new Float32Array(COUNT), index: 0 };

const state = {
  replay: null,
  status: null,
  statusError: null,
  localError: null,
  serverLost: false,
  selected: new Map(), // channel -> palette slot, so a sensor keeps its color while charted
  selectionOrder: [],
  windowS: 10,
  colorMode: "live",
  range: { ...COLOR_RANGE },
  // each unit keeps its own color range
  ranges: {
    V: { ...COLOR_RANGE },
    force: { ...FORCE_COLOR_RANGE },
    N: { ...NEWTON_COLOR_RANGE },
    MPa: { ...PRESSURE_COLOR_RANGE },
  },
  baseline: null,
  stats: [],
  lastSavedFile: null,
  chartHover: null,
};

let dirty = true;
const invalidate = () => { dirty = true; };

const currentSeries = () => (state.replay ? state.replay.series : live);
const currentMarkers = () => (state.replay ? state.replay.markers : liveMarkers);
const hasData = () => currentSeries().end > 0;

// -------------------------------- colors ----------------------------------

// Pressure-map scale: navy at no load, through teal, green, yellow, and orange,
// to red at the top of the color range.
const HEAT_STOPS = [
  [27, 49, 108], [52, 125, 137], [80, 174, 135], [120, 207, 120], [179, 231, 98],
  [229, 230, 75], [228, 183, 71], [225, 137, 80], [220, 38, 60],
];

function heatRgb(fraction) {
  const x = Math.max(0, Math.min(1, fraction)) * (HEAT_STOPS.length - 1);
  const i = Math.min(Math.floor(x), HEAT_STOPS.length - 2);
  const mix = x - i;
  return HEAT_STOPS[i].map((c, k) => Math.round(c + (HEAT_STOPS[i + 1][k] - c) * mix));
}

const cssRgb = ([r, g, b]) => `rgb(${r},${g},${b})`;

function inkOn([r, g, b]) {
  const linear = (c) => {
    const v = c / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  };
  const luminance = 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b);
  return luminance > 0.2 ? "#0b0b0b" : "#ffffff";
}

let tokens = {};
function readTokens() {
  const css = getComputedStyle(document.documentElement);
  const get = (name) => css.getPropertyValue(name).trim();
  tokens = {
    wire: get("--model-wire"),
    wireFar: get("--model-wire-far"),
    guide: get("--model-guide"),
    ring: get("--model-ring"),
    label: get("--model-label"),
    ink: get("--ink"),
    ink2: get("--ink-2"),
    muted: get("--muted"),
    grid: get("--grid"),
    axis: get("--axis"),
    surface: get("--surface"),
    neutral: get("--grid"),
    series: [get("--series-1"), get("--series-2"), get("--series-3"), get("--series-4")],
  };
  invalidate();
}

// ---------------------------- values for display --------------------------

function currentValue(channel) {
  const series = currentSeries();
  return series.end > 0 ? series.value(series.end - 1, channel) : null;
}

function currentRaw(channel) {
  const series = currentSeries();
  return series.end > 0 ? series.raw(series.end - 1, channel) : null;
}

// What the colors and summary use: optionally zeroed, while readouts stay raw.
function zeroed(channel, value) {
  if (!state.baseline) return value;
  return state.range.min + Math.max(0, value - state.baseline[channel - 1]);
}

function colorValue(channel) {
  if (!hasData()) return null;
  const value = state.colorMode === "peak" ? peak.values[channel - 1] : currentValue(channel);
  return zeroed(channel, value);
}

function sensorRgb(channel) {
  const value = colorValue(channel);
  if (value === null || !Number.isFinite(value)) return null; // no data, or not calibrated
  return heatRgb((value - state.range.min) / (state.range.max - state.range.min));
}

function updatePeak() {
  const series = currentSeries();
  if (series.end < peak.index) resetPeak();
  for (let i = peak.index; i < series.end; i++) {
    for (const channel of CHANNELS) {
      const raw = series.raw(i, channel);
      if (raw > peak.raw[channel - 1]) {
        peak.raw[channel - 1] = raw;
        peak.values[channel - 1] = toUnits(channel, raw); // calibrations only ever increase with volts
      }
    }
  }
  peak.index = series.end;
}

function resetPeak(fromNow = false) {
  peak.values.fill(0);
  peak.raw.fill(0);
  peak.index = fromNow ? currentSeries().end : 0;
}

function computeStats() {
  const series = currentSeries();
  if (series.end === 0) return [];
  const latest = series.time[series.end - 1];
  const start = series.lowerBound(latest - state.windowS * 1000);
  const count = series.end - start;
  return CHANNELS.map((channel) => {
    let sum = 0, sumSquares = 0;
    let min = Infinity, max = -Infinity;
    for (let i = start; i < series.end; i++) {
      const value = series.value(i, channel);
      sum += value;
      sumSquares += value * value;
      if (value < min) min = value;
      if (value > max) max = value;
    }
    const mean = sum / count;
    const variance = count > 1 ? Math.max(0, (sumSquares - count * mean * mean) / (count - 1)) : 0;
    return { channel, mean, std: Math.sqrt(variance), min, max };
  });
}

// ------------------------------- tooltip ----------------------------------

const tooltip = $("tooltip");

function showTooltip(clientX, clientY, title, rows) {
  tooltip.replaceChildren();
  if (title) {
    const heading = document.createElement("div");
    heading.className = "tip-title";
    heading.textContent = title;
    tooltip.append(heading);
  }
  for (const { value, label, key } of rows) {
    const row = document.createElement("div");
    row.className = "tip-row";
    if (key) {
      const swatch = document.createElement("span");
      swatch.className = "line-key";
      swatch.style.setProperty("--key", key);
      row.append(swatch);
    }
    const valueEl = document.createElement("span");
    valueEl.className = "tip-value";
    valueEl.textContent = value;
    const labelEl = document.createElement("span");
    labelEl.className = "tip-label";
    labelEl.textContent = label;
    row.append(valueEl, labelEl);
    tooltip.append(row);
  }
  tooltip.hidden = false;
  const { width, height } = tooltip.getBoundingClientRect();
  const x = clientX + 14 + width > window.innerWidth ? clientX - width - 14 : clientX + 14;
  const y = Math.min(clientY + 14, window.innerHeight - height - 8);
  tooltip.style.left = `${Math.max(8, x)}px`;
  tooltip.style.top = `${Math.max(8, y)}px`;
}

const hideTooltip = () => { tooltip.hidden = true; };

function sensorTooltip(sensor, clientX, clientY) {
  const now = currentValue(sensor.channel);
  const stat = state.stats.find((s) => s.channel === sensor.channel);
  if (!isCalibrated(sensor.channel)) {
    showTooltip(clientX, clientY, `S${sensor.channel} · ${sensor.location}`, [{ value: "Not calibrated", label: "" }]);
    return;
  }
  const rows = [{ value: now === null ? "–" : `${formatValue(now)} ${unitLabel()}`, label: "now" }];
  if (stat) {
    rows.push({ value: formatValue(stat.mean), label: "mean" }, { value: formatValue(stat.std), label: "std dev" });
  }
  showTooltip(clientX, clientY, `S${sensor.channel} · ${sensor.location}`, rows);
}

// ------------------------------- selection --------------------------------

function toggleSensor(channel) {
  if (state.selected.has(channel)) {
    state.selected.delete(channel);
    state.selectionOrder = state.selectionOrder.filter((c) => c !== channel);
  } else {
    if (state.selected.size === MAX_CHARTED) {
      const oldest = state.selectionOrder.shift();
      state.selected.delete(oldest);
    }
    const used = new Set(state.selected.values());
    let slot = 0;
    while (used.has(slot)) slot += 1;
    state.selected.set(channel, slot);
    state.selectionOrder.push(channel);
  }
  syncSelection();
  invalidate();
}

function syncSelection() {
  for (const [channel, cell] of heatCells) {
    const slot = state.selected.get(channel);
    cell.classList.toggle("selected", slot !== undefined);
    cell.setAttribute("aria-pressed", slot !== undefined);
    if (slot !== undefined) cell.style.setProperty("--key", tokens.series[slot]);
  }
  for (const [channel, row] of tableRows) {
    row.checkbox.checked = state.selected.has(channel);
  }

  const legend = $("chartLegend");
  legend.replaceChildren();
  const charted = [...state.selected.entries()].sort((a, b) => a[0] - b[0]);
  if (charted.length === 0) {
    legend.textContent = "Click a sensor to chart it";
  }
  for (const [channel, slot] of charted) {
    const item = document.createElement("span");
    item.className = "legend-item";
    const key = document.createElement("span");
    key.className = "line-key";
    key.style.setProperty("--key", tokens.series[slot]);
    const sensor = sensors.find((s) => s.channel === channel);
    const uncalibrated = isCalibrated(channel) ? "" : ", not calibrated";
    item.append(key, `S${channel} (${sensor.location}${uncalibrated})`);
    legend.append(item);
  }
}

// ---------------------------------- 3D ------------------------------------

const cylinder = new CylinderView($("modelCanvas"), {
  sensors,
  sides: SIDES,
  cylinder: CYLINDER,
  headDiameterIn: SENSOR_HEAD_DIAMETER_IN,
  onSelect: (sensor) => toggleSensor(sensor.channel),
  onHover: (sensor, event) => {
    if (sensor) sensorTooltip(sensor, event.clientX, event.clientY);
    else hideTooltip();
  },
});
cylinder.onChange = invalidate;

function modelStyle(sensor) {
  const rgb = sensorRgb(sensor.channel);
  const slot = state.selected.get(sensor.channel);
  return {
    fill: rgb ? cssRgb(rgb) : tokens.neutral,
    ink: rgb ? inkOn(rgb) : tokens.ink,
    selectedColor: slot === undefined ? null : tokens.series[slot],
  };
}

// ----------------------------- end-on diagram -----------------------------

const SVG_NS = "http://www.w3.org/2000/svg";
const endDots = new Map();

function svgEl(name, attributes, parent) {
  const el = document.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attributes)) el.setAttribute(key, value);
  parent.append(el);
  return el;
}

function buildEndView() {
  const svg = $("endView");
  svgEl("circle", { class: "end-outline", cx: 0, cy: 0, r: RADIUS }, svg);
  svgEl("line", { class: "end-axis", x1: -RADIUS, y1: 0, x2: RADIUS, y2: 0 }, svg);

  for (const side of SIDES) {
    const direction = Math.cos((side.angleDeg * Math.PI) / 180) >= 0 ? 1 : -1;
    const label = svgEl("text", { class: "end-side", x: direction * 1.1, y: -1.98 }, svg);
    label.textContent = side.name;
  }

  for (const sensor of sensors) {
    const x = RADIUS * Math.cos(sensor.angle);
    const y = -RADIUS * Math.sin(sensor.angle); // SVG y runs downward
    const dot = svgEl("circle", {
      class: "end-sensor", cx: x, cy: y, r: SENSOR_HEAD_DIAMETER_IN / 2 + 0.02,
    }, svg);
    const text = svgEl("text", {
      class: "end-text", x: x * 1.3, y: y * 1.3,
    }, svg);
    text.textContent = `S${sensor.channel}`;

    const hit = svgEl("circle", { class: "end-hit", cx: x * 1.1, cy: y * 1.1, r: 0.3 }, svg);
    hit.setAttribute("tabindex", "0");
    hit.setAttribute("role", "button");
    hit.setAttribute("aria-label", `S${sensor.channel}, ${sensor.location}. Toggle on chart.`);
    hit.addEventListener("click", () => toggleSensor(sensor.channel));
    hit.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggleSensor(sensor.channel);
      }
    });
    hit.addEventListener("pointermove", (event) => sensorTooltip(sensor, event.clientX, event.clientY));
    hit.addEventListener("pointerleave", hideTooltip);
    endDots.set(sensor.channel, dot);
  }

  $("endCaption").textContent =
    `End view: ${CYLINDER.diameterIn} in diameter, sensors ${Math.abs(ARC_POSITIONS_IN[1] - ARC_POSITIONS_IN[0])} in apart along the circumference`;
}

function updateEndView() {
  for (const [channel, dot] of endDots) {
    const rgb = sensorRgb(channel);
    const slot = state.selected.get(channel);
    dot.setAttribute("fill", rgb ? cssRgb(rgb) : tokens.neutral);
    dot.classList.toggle("selected", slot !== undefined);
    dot.style.stroke = slot !== undefined ? tokens.series[slot] : "";
  }
}

// ------------------------------- heat grid --------------------------------

const heatCells = new Map();
const heatText = new Map();

function buildHeatGrid() {
  const grid = $("heatGrid");
  const addDiv = (className, text) => {
    const el = document.createElement("div");
    el.className = className;
    el.textContent = text;
    grid.append(el);
  };

  addDiv("heat-corner", "");
  for (const side of SIDES) addDiv("heat-col", side.name);

  ARC_POSITIONS_IN.forEach((arc, rowIndex) => {
    addDiv("heat-row", formatArc(arc));
    for (const side of SIDES) {
      const channel = side.channels[rowIndex];
      const sensor = sensors.find((s) => s.channel === channel);
      const cell = document.createElement("button");
      cell.type = "button";
      cell.className = "heat-cell";
      cell.setAttribute("aria-label", `S${channel}, ${sensor.location}. Toggle on chart.`);
      const name = document.createElement("span");
      name.className = "heat-name";
      name.textContent = `S${channel}`;
      const value = document.createElement("span");
      value.className = "heat-value";
      value.textContent = "–";
      const second = document.createElement("span");
      second.className = "heat-second";
      const flag = document.createElement("span");
      flag.className = "heat-flag";
      cell.append(name, value, second, flag);
      cell.addEventListener("click", () => toggleSensor(channel));
      cell.addEventListener("pointermove", (event) => sensorTooltip(sensor, event.clientX, event.clientY));
      cell.addEventListener("pointerleave", hideTooltip);
      cell.addEventListener("focus", () => {
        const rect = cell.getBoundingClientRect();
        sensorTooltip(sensor, rect.right, rect.top);
      });
      cell.addEventListener("blur", hideTooltip);
      grid.append(cell);
      heatCells.set(channel, cell);
      heatText.set(channel, { value, second, flag });
    }
  });
}

function updateHeatColors() {
  for (const [channel, cell] of heatCells) {
    const rgb = sensorRgb(channel);
    cell.style.background = rgb ? cssRgb(rgb) : "";
    cell.style.color = rgb ? inkOn(rgb) : "";
  }
}

function updateHeatText() {
  // newtons and pressure are shown together, since each cell is one sensor's force on a known area
  const otherMode = { N: "MPa", MPa: "N" }[units.mode];
  for (const [channel, { value, second, flag }] of heatText) {
    const usePeak = state.colorMode === "peak" && hasData();
    const shown = usePeak ? peak.values[channel - 1] : currentValue(channel);
    const raw = usePeak ? peak.raw[channel - 1] : currentRaw(channel);
    const text = formatValue(shown, "cell");
    value.textContent = text === "–" ? text : `${text} ${unitLabel()}`;
    const other = otherMode && raw !== null ? formatValue(toUnits(channel, raw, otherMode), "cell", otherMode) : "–";
    second.textContent = other === "–" ? "" : `${other} ${unitLabel(otherMode)}`;
    if (raw !== null && raw >= SATURATION_LEVEL) flag.textContent = "⚠ Saturated";
    else flag.textContent = hasData() && !isCalibrated(channel) ? "Not calibrated" : "";
  }
}

function buildScales() {
  for (const slot of document.querySelectorAll(".scale-slot")) {
    const scale = document.createElement("div");
    scale.className = "scale";
    const bar = document.createElement("div");
    bar.className = "scale-bar";
    bar.style.background = `linear-gradient(to right, ${HEAT_STOPS.map(cssRgb).join(",")})`;
    const low = document.createElement("span");
    const mid = document.createElement("span");
    const high = document.createElement("span");
    low.dataset.scale = "low";
    mid.dataset.scale = "mid";
    high.dataset.scale = "high";
    scale.append(bar, low, mid, high);
    slot.replaceChildren(scale);
  }
  updateScales();
}

function updateScales() {
  const { min, max } = state.range;
  const set = (key, value) => {
    for (const el of document.querySelectorAll(`[data-scale="${key}"]`)) {
      el.textContent = `${formatValue(value, "cell")} ${unitLabel()}`;
    }
  };
  set("low", min);
  set("mid", (min + max) / 2);
  set("high", max);
}

// -------------------------- table and summary tiles -----------------------

const tableRows = new Map();

function buildSensorTable() {
  const body = $("sensorTable");
  for (const channel of CHANNELS) {
    const sensor = sensors.find((s) => s.channel === channel);
    const row = document.createElement("tr");
    const check = document.createElement("td");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.setAttribute("aria-label", `Chart S${channel}`);
    checkbox.addEventListener("change", () => toggleSensor(channel));
    check.append(checkbox);
    const addCell = (text, className) => {
      const cell = document.createElement("td");
      cell.textContent = text;
      if (className) cell.className = className;
      row.append(cell);
      return cell;
    };
    row.append(check);
    addCell(`S${channel}`);
    addCell(sensor.location);
    tableRows.set(channel, {
      checkbox,
      now: addCell("–", "num"),
      mean: addCell("–", "num"),
      std: addCell("–", "num"),
      min: addCell("–", "num"),
      max: addCell("–", "num"),
    });
    body.append(row);
  }
}

function updateSensorTable() {
  $("tableWindow").textContent = `In ${unitLabel()}. Statistics cover the last ${state.windowS} s.`;
  for (const [channel, row] of tableRows) {
    const stat = state.stats.find((s) => s.channel === channel);
    row.now.textContent = formatValue(currentValue(channel));
    row.mean.textContent = stat ? formatValue(stat.mean) : "–";
    row.std.textContent = stat ? formatValue(stat.std) : "–";
    row.min.textContent = stat ? formatValue(stat.min) : "–";
    row.max.textContent = stat ? formatValue(stat.max) : "–";
  }
}

function sideLoad(side) {
  let total = 0, moment = 0;
  side.channels.forEach((channel, i) => {
    const weight = Math.max(0, zeroed(channel, currentValue(channel)) - state.range.min);
    if (!Number.isFinite(weight)) return; // uncalibrated sensors are left out in grams
    total += weight;
    moment += weight * ARC_POSITIONS_IN[i];
  });
  return { total, center: total > 0 ? moment / total : 0 };
}

function updateTiles() {
  const clearTiles = () => {
    $("tileBalance").textContent = "–";
    $("tileCenterA").textContent = "–";
    $("tileCenterB").textContent = "–";
  };
  if (!hasData()) {
    clearTiles();
    $("tilePeak").textContent = "–";
    return;
  }

  // ignore the tiny noise on unloaded sensors, or idle balance would be meaningless
  const threshold = 0.02 * (state.range.max - state.range.min);
  const [a, b] = SIDES.map(sideLoad);
  const total = a.total + b.total;
  if (total < threshold) {
    clearTiles();
    $("tileBalanceSub").textContent = "No load detected";
  } else {
    const percentA = Math.round((100 * a.total) / total);
    $("tileBalance").textContent = `${percentA}% / ${100 - percentA}%`;
    const partial = units.mode !== "V" && Object.keys(units.calibration).length < CHANNELS.length;
    $("tileBalanceSub").textContent = `${SIDES[0].name} / ${SIDES[1].name}${partial ? ", calibrated sensors only" : ""}`;
    const formatCenter = (load) => {
      if (load.total < threshold) return "–";
      const center = Math.abs(load.center) < 0.005 ? 0 : load.center; // so it never shows −0.00
      return `${center > 0 ? "+" : center < 0 ? "−" : ""}${Math.abs(center).toFixed(2)} in`;
    };
    $("tileCenterA").textContent = formatCenter(a);
    $("tileCenterB").textContent = formatCenter(b);
  }

  let highest = null;
  const saturated = [];
  for (const channel of CHANNELS) {
    const value = currentValue(channel);
    if (Number.isFinite(value) && (highest === null || value > currentValue(highest))) highest = channel;
    if (currentRaw(channel) >= SATURATION_LEVEL) saturated.push(`S${channel}`);
  }
  $("tilePeak").textContent = highest === null
    ? "No calibrated sensors"
    : `S${highest}: ${formatValue(currentValue(highest), "cell")} ${unitLabel()}`;
  const note = $("tileSaturated");
  note.textContent = saturated.length ? `⚠ Saturated: ${saturated.join(", ")}` : "No sensors saturated";
  note.classList.toggle("saturated-note", saturated.length > 0);
}

// --------------------------------- chart ----------------------------------

const chart = { canvas: $("chartCanvas"), ctx: $("chartCanvas").getContext("2d"), width: 0, height: 0 };
const PLOT = { left: 44, right: 40, top: 10, bottom: 28 };

function chartWindow() {
  const series = currentSeries();
  const windowMs = state.windowS * 1000;
  const latest = series.end > 0 ? series.time[series.end - 1] : 0;
  const end = Math.max(latest, (series.end > 0 ? series.time[0] : 0) + windowMs);
  return { start: end - windowMs, end };
}

function drawChart() {
  const { ctx, width, height } = chart;
  if (!width) return;
  ctx.clearRect(0, 0, width, height);

  const series = currentSeries();
  const plotWidth = width - PLOT.left - PLOT.right;
  const plotHeight = height - PLOT.top - PLOT.bottom;
  const { start, end } = chartWindow();
  const yStep = niceStep(Math.max(state.range.max, 1e-6) / 5);
  const yMax = Math.max(yStep, Math.ceil(state.range.max / yStep) * yStep);
  const xOf = (t) => PLOT.left + ((t - start) / (end - start)) * plotWidth;
  const yOf = (v) => PLOT.top + plotHeight * (1 - v / yMax);

  ctx.font = "12px system-ui, -apple-system, 'Segoe UI', sans-serif";
  ctx.lineWidth = 1;

  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  const tickCount = Math.round(yMax / yStep);
  for (let i = 0; i <= tickCount; i++) {
    const v = i * yStep;
    const y = Math.round(yOf(v)) + 0.5;
    ctx.strokeStyle = i === 0 ? tokens.axis : tokens.grid;
    ctx.beginPath();
    ctx.moveTo(PLOT.left, y);
    ctx.lineTo(PLOT.left + plotWidth, y);
    ctx.stroke();
    ctx.fillStyle = tokens.muted;
    const label = Number(v.toPrecision(6)).toString();
    ctx.fillText(i === tickCount ? `${label} ${unitLabel()}` : label, PLOT.left - 6, y);
  }

  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  ctx.fillStyle = tokens.muted;
  const step = { 5: 1, 10: 2, 30: 5, 60: 10 }[state.windowS] * 1000;
  for (let t = Math.ceil(start / step) * step; t <= end; t += step) {
    if (t < 0) continue;
    ctx.fillText(`${t / 1000} s`, xOf(t), PLOT.top + plotHeight + 7);
  }

  // event markers
  for (const marker of currentMarkers()) {
    if (marker.t < start || marker.t > end) continue;
    const x = Math.round(xOf(marker.t)) + 0.5;
    ctx.strokeStyle = tokens.ink2;
    ctx.beginPath();
    ctx.moveTo(x, PLOT.top);
    ctx.lineTo(x, PLOT.top + plotHeight);
    ctx.stroke();
    ctx.fillStyle = tokens.ink2;
    ctx.textAlign = "left";
    ctx.textBaseline = "top";
    ctx.fillText(marker.label, x + 4, PLOT.top + 2);
  }

  if (series.end > 0) {
    ctx.save();
    ctx.beginPath();
    ctx.rect(PLOT.left, PLOT.top, plotWidth, plotHeight);
    ctx.clip();
    const first = Math.max(0, series.lowerBound(start) - 1);
    const endLabels = [];
    ctx.lineWidth = 2;
    ctx.lineJoin = "round";
    for (const [channel, slot] of state.selected) {
      if (!isCalibrated(channel)) continue;
      ctx.strokeStyle = tokens.series[slot];
      ctx.beginPath();
      drawDecimated(ctx, series, channel, first, xOf, yOf);
      ctx.stroke();
      endLabels.push({ channel, y: yOf(series.value(series.end - 1, channel)) });
    }
    ctx.restore();

    // direct labels at the line ends, nudged apart so they can't overlap
    endLabels.sort((a, b) => a.y - b.y);
    for (let i = 1; i < endLabels.length; i++) {
      endLabels[i].y = Math.max(endLabels[i].y, endLabels[i - 1].y + 13);
    }
    ctx.fillStyle = tokens.ink;
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    const labelX = Math.min(xOf(series.time[series.end - 1]) + 5, PLOT.left + plotWidth + 5);
    for (const { channel, y } of endLabels) ctx.fillText(`S${channel}`, labelX, y);
  } else {
    ctx.fillStyle = tokens.muted;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("No data yet. Start recording or use the live view.", PLOT.left + plotWidth / 2, PLOT.top + plotHeight / 2);
  }

  drawChartHover(xOf, yOf, plotHeight);
}

// Axis steps of 1, 2, or 5 times a power of ten, so labels stay round in volts and grams.
function niceStep(rough) {
  const power = 10 ** Math.floor(Math.log10(rough));
  const scaled = rough / power;
  return (scaled <= 1 ? 1 : scaled <= 2 ? 2 : scaled <= 5 ? 5 : 10) * power;
}

// One pixel column can hold many samples, so keep each column's extremes and
// short spikes stay visible at long time windows.
function drawDecimated(ctx, series, channel, first, xOf, yOf) {
  let column = null, low = 0, high = 0, firstValue = 0, lastValue = 0, x = 0;
  let started = false;
  const flush = () => {
    if (column === null) return;
    const points = [firstValue, low, high, lastValue];
    for (const value of points) {
      if (started) ctx.lineTo(x, yOf(value));
      else { ctx.moveTo(x, yOf(value)); started = true; }
    }
  };
  for (let i = first; i < series.end; i++) {
    const px = xOf(series.time[i]);
    const nextColumn = Math.round(px);
    const value = series.value(i, channel);
    if (nextColumn !== column) {
      flush();
      column = nextColumn;
      x = px;
      low = high = firstValue = value;
    }
    low = Math.min(low, value);
    high = Math.max(high, value);
    lastValue = value;
  }
  flush();
}

function drawChartHover(xOf, yOf, plotHeight) {
  const series = currentSeries();
  const hover = state.chartHover;
  if (!hover || series.end === 0 || state.selected.size === 0) return;

  const { start, end } = chartWindow();
  const plotWidth = chart.width - PLOT.left - PLOT.right;
  const hoverTime = start + ((hover.x - PLOT.left) / plotWidth) * (end - start);
  if (hoverTime < start || hoverTime > end) {
    hideTooltip();
    return;
  }
  let index = Math.min(series.lowerBound(hoverTime), series.end - 1);
  if (index > 0 && hoverTime - series.time[index - 1] < series.time[index] - hoverTime) index -= 1;

  const { ctx } = chart;
  const x = Math.round(xOf(series.time[index])) + 0.5;
  ctx.strokeStyle = tokens.ink2;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(x, PLOT.top);
  ctx.lineTo(x, PLOT.top + plotHeight);
  ctx.stroke();

  const rows = [...state.selected.entries()].sort((a, b) => a[0] - b[0]).map(([channel, slot]) => ({
    value: isCalibrated(channel) ? `${formatValue(series.value(index, channel))} ${unitLabel()}` : "Not calibrated",
    label: `S${channel}`,
    key: tokens.series[slot],
  }));
  for (const [channel, slot] of state.selected) {
    if (!isCalibrated(channel)) continue;
    ctx.beginPath();
    ctx.arc(x, yOf(series.value(index, channel)), 4, 0, 2 * Math.PI);
    ctx.fillStyle = tokens.series[slot];
    ctx.strokeStyle = tokens.surface;
    ctx.lineWidth = 2;
    ctx.fill();
    ctx.stroke();
  }
  showTooltip(hover.clientX, hover.clientY, `${(series.time[index] / 1000).toFixed(2)} s`, rows);
}

function bindChartHover() {
  chart.canvas.addEventListener("pointermove", (event) => {
    const rect = chart.canvas.getBoundingClientRect();
    state.chartHover = { x: event.clientX - rect.left, clientX: event.clientX, clientY: event.clientY };
    invalidate();
  });
  chart.canvas.addEventListener("pointerleave", () => {
    state.chartHover = null;
    hideTooltip();
    invalidate();
  });
}

function fitCanvases() {
  const modelBox = $("modelCanvas").parentElement.getBoundingClientRect();
  if (modelBox.width > 0) cylinder.resize(modelBox.width, modelBox.height);

  const box = chart.canvas.parentElement.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  chart.width = box.width;
  chart.height = box.height;
  chart.canvas.width = Math.round(box.width * dpr);
  chart.canvas.height = Math.round(box.height * dpr);
  chart.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  invalidate();
}

// -------------------------------- server ----------------------------------

async function post(path, body = {}) {
  state.localError = null;
  try {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `Request failed (${response.status}).`);
    applyStatus(payload);
  } catch (error) {
    state.localError = error.message;
    renderBanner();
  }
}

function connectStream() {
  const stream = new EventSource("/api/stream");
  stream.onmessage = (event) => {
    state.serverLost = false;
    const message = JSON.parse(event.data);
    for (const sample of message.samples) ingestSample(sample);
    for (const marker of message.markers) liveMarkers.push(marker);
    applyStatus(message.status);
  };
  stream.onerror = () => {
    state.serverLost = true;
    renderBanner();
  };
}

function ingestSample(sample) {
  const timeMs = sample[0];
  if (live.length > 0 && timeMs < live.time[live.length - 1]) {
    // the Arduino restarted its clock, so this is a new run
    live.clear();
    liveMarkers.length = 0;
    if (!state.replay) resetPeak();
    state.baseline = state.replay ? state.baseline : null;
  }
  const row = new Float32Array(COUNT);
  for (const channel of CHANNELS) {
    row[channel - 1] = sample[channel];
  }
  live.push(timeMs, row);
  live.end = live.length;
  if (!state.replay) invalidate();
}

function formatElapsed(seconds) {
  const minutes = Math.floor(seconds / 60);
  return `${String(minutes).padStart(2, "0")}:${(seconds - minutes * 60).toFixed(1).padStart(4, "0")}`;
}

function applyStatus(status) {
  state.status = status;
  state.statusError = status.error;

  const recording = status.recording;
  const running = status.state !== "idle";
  const pill = $("statusPill");
  pill.dataset.state = recording ? "recording" : status.state;
  $("statusText").textContent = recording
    ? `Recording on ${status.port}`
    : { idle: "Idle", connecting: `Connecting to ${status.port}`, live: `Live on ${status.port}` }[status.state];
  $("simBadge").hidden = !(running && status.simulated);

  $("quality").textContent = status.state === "live" && status.rate_hz
    ? `${status.rate_hz.toFixed(1)} Hz · ${status.gaps} gaps · ${status.bad_lines} bad lines`
    : "";

  const recordButton = $("recordButton");
  recordButton.textContent = recording ? "Stop recording" : "Start recording";
  recordButton.classList.toggle("recording", Boolean(recording));
  const connectButton = $("connectButton");
  connectButton.textContent = running ? "Disconnect" : "Live view only";
  $("markerButton").disabled = status.state !== "live";
  // a gripper test controls the recording itself until it finishes
  recordButton.disabled = status.test.running;
  connectButton.disabled = status.test.running;
  // the page can be reloaded mid-run, so the server's port is the one to show
  if (running && $("portSelect").value !== status.port) $("portSelect").value = status.port;
  $("portSelect").disabled = running;

  const recStatus = $("recStatus");
  if (recording) {
    const limit = recording.duration_s ? ` of ${formatElapsed(recording.duration_s)}` : "";
    recStatus.textContent = `Saving to ${recording.file} · ${formatElapsed(recording.elapsed_s)}${limit} · ${recording.rows.toLocaleString()} samples`;
  } else if (status.last_saved) {
    const saved = status.last_saved;
    recStatus.textContent = `Saved ${saved.file} · ${formatElapsed(saved.elapsed_s)} · ${saved.rows.toLocaleString()} samples`;
  } else {
    recStatus.textContent = "";
  }

  const savedFile = status.last_saved ? status.last_saved.file : null;
  if (savedFile !== state.lastSavedFile) {
    state.lastSavedFile = savedFile;
    refreshRecordings();
  }
  gripper.update(status);
  renderBanner();
}

function renderBanner() {
  const banner = $("banner");
  const message = state.serverLost
    ? "Lost contact with the dashboard server. Check that dashboard.py is still running."
    : state.localError || state.statusError;
  banner.hidden = !message;
  banner.textContent = message || "";
}

async function refreshPorts({ likelyOnly = false } = {}) {
  const sensorSelect = $("portSelect");
  const gripperSelect = $("gripperPort");
  const previousSensor = sensorSelect.value || loadSavedPort("flexiforce-port");
  const previousGripper = gripperSelect.value || loadSavedPort("flexiforce-gripper-port");
  try {
    const listing = await (await fetch("/api/ports")).json();
    const fill = (select, placeholder, simulated, simulatedLabel) => {
      select.replaceChildren();
      const addOption = (value, text) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = text;
        select.append(option);
      };
      addOption("", placeholder);
      for (const port of listing.ports) {
        addOption(port.device, port.description.includes(port.device) ? port.description : `${port.device}: ${port.description}`);
      }
      addOption(simulated, simulatedLabel);
    };
    fill(sensorSelect, "Choose a port…", listing.simulated_port, "Simulated Arduino (test data)");
    fill(gripperSelect, "Choose a port…", listing.simulated_actuator_port, "Simulated gripper (test only)");

    // never preselect a simulator, so test data can't be recorded by accident
    const restore = (select, previous, simulated) => {
      const available = [...select.options].map((o) => o.value);
      select.value = previous && previous !== simulated && available.includes(previous) ? previous : "";
    };
    restore(sensorSelect, previousSensor, listing.simulated_port);
    restore(gripperSelect, previousGripper, listing.simulated_actuator_port);
    const likely = listing.ports.find((p) => p.likely_arduino);
    if (!sensorSelect.value && likely) sensorSelect.value = likely.device;
  } catch (error) {
    state.localError = "Couldn't list serial ports.";
    renderBanner();
    return;
  }
  await findArduinos(likelyOnly);
}

// COM numbers change between computers, so ask each port which Arduino it is.
async function findArduinos(likelyOnly) {
  const note = $("detectStatus");
  note.textContent = "Looking for your Arduinos…";
  try {
    const response = await fetch("/api/ports/identify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ likely_only: likelyOnly }),
    });
    if (!response.ok) {
      note.textContent = "";
      return;
    }
    const found = await response.json();
    if (found.sensors && !$("portSelect").disabled) $("portSelect").value = found.sensors;
    if (found.actuator && !$("gripperPort").disabled) $("gripperPort").value = found.actuator;

    const parts = [];
    if (found.sensors) parts.push(`Sensors on ${found.sensors}.`);
    if (found.actuator) parts.push(`Gripper on ${found.actuator}.`);
    if (found.sensors && !found.actuator) parts.push("The gripper Arduino didn't answer. It may need the latest actuator_control.ino.");
    if (found.busy.length) parts.push(`${found.busy.join(", ")} in use by another program.`);
    if (parts.length === 0) parts.push("No Arduinos found. Check that they're plugged in.");
    note.textContent = parts.join(" ");
  } catch (error) {
    note.textContent = "";
  }
}

function loadSavedPort(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function savePort(key, port) {
  try {
    localStorage.setItem(key, port);
  } catch {
    // only a convenience, so it's fine if storage is blocked
  }
}

function requirePort() {
  const port = $("portSelect").value;
  if (port) return port;
  state.localError = "Choose a serial port first. Use the simulated Arduino to try the page without hardware.";
  renderBanner();
  $("portSelect").focus();
  return null;
}

// ------------------------------ recordings --------------------------------

function formatSize(bytes) {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1048576).toFixed(1)} MB`;
}

async function refreshRecordings() {
  try {
    const { recordings, data_dir: dataDir } = await (await fetch("/api/recordings")).json();
    $("dataDir").textContent = `Saved in ${dataDir}`;
    const body = $("recordingsTable");
    body.replaceChildren();
    if (recordings.length === 0) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 5;
      cell.className = "muted";
      cell.textContent = "No recordings yet.";
      row.append(cell);
      body.append(row);
    }
    for (const recording of recordings) {
      const row = document.createElement("tr");
      const addCell = (text, className) => {
        const cell = document.createElement("td");
        cell.textContent = text;
        if (className) cell.className = className;
        row.append(cell);
      };
      addCell(recording.active ? `${recording.name} (still recording)` : recording.name);
      addCell(new Date(recording.modified * 1000).toLocaleString([], { dateStyle: "short", timeStyle: "short" }));
      addCell(recording.duration_s === null ? "–" : formatElapsed(recording.duration_s), "num");
      addCell(formatSize(recording.size), "num");

      const actions = document.createElement("td");
      const wrap = document.createElement("div");
      wrap.className = "row-actions";
      if (recording.replayable) {
        const replay = document.createElement("button");
        replay.type = "button";
        replay.textContent = "Replay";
        replay.addEventListener("click", () => startReplay(recording.name));
        wrap.append(replay);
      }
      const download = document.createElement("a");
      download.className = "button-link";
      download.href = `/api/recordings/${encodeURIComponent(recording.name)}?download=1`;
      download.textContent = "Download";
      wrap.append(download);
      actions.append(wrap);
      row.append(actions);
      body.append(row);
    }
  } catch (error) {
    state.localError = "Couldn't list recordings.";
    renderBanner();
  }
}

function parseRecording(text) {
  const lines = text.split(/\r?\n/);
  const header = lines[0].split(",").map((name) => name.trim());
  const timeColumn = header.indexOf("time_ms");
  const columns = CHANNELS.map((channel) => header.indexOf(`S${channel}_V`));
  const markerColumn = header.indexOf("marker");
  if (timeColumn < 0 || columns.some((column) => column < 0)) {
    throw new Error("This file doesn't have the expected time_ms and S1_V to S10_V columns.");
  }

  const series = new Series(Math.max(1, lines.length));
  const markers = [];
  const row = new Float32Array(COUNT);
  for (let i = 1; i < lines.length; i++) {
    const fields = lines[i].split(",");
    if (fields.length !== header.length) continue;
    const timeMs = Number(fields[timeColumn]);
    if (!Number.isFinite(timeMs)) continue;
    let valid = true;
    CHANNELS.forEach((channel, k) => {
      const value = Number(fields[columns[k]]);
      if (!Number.isFinite(value)) valid = false;
      row[channel - 1] = value;
    });
    if (!valid) continue;
    series.push(timeMs, row);
    if (markerColumn >= 0 && fields[markerColumn].trim()) {
      markers.push({ t: timeMs, label: fields[markerColumn].trim() });
    }
  }
  if (series.length === 0) throw new Error("This file has no data rows.");
  return { series, markers };
}

async function startReplay(name) {
  state.localError = null;
  try {
    const response = await fetch(`/api/recordings/${encodeURIComponent(name)}`);
    if (!response.ok) throw new Error("Couldn't load that file.");
    const { series, markers } = parseRecording(await response.text());
    series.end = 1;
    state.replay = { name, series, markers, playing: true, speed: Number($("replaySpeed").value), lastFrame: null };
    state.baseline = null;
    updateZeroButton();
    resetPeak();
    $("replayBar").hidden = false;
    $("replayName").textContent = name;
    const first = series.time[0];
    const last = series.time[series.length - 1];
    $("replaySlider").min = first / 1000;
    $("replaySlider").max = last / 1000;
    updateReplayControls();
    invalidate();
    $("replayBar").scrollIntoView({ block: "nearest" });
  } catch (error) {
    state.localError = error.message;
  }
  renderBanner();
}

function exitReplay() {
  state.replay = null;
  $("replayBar").hidden = true;
  state.baseline = null;
  updateZeroButton();
  resetPeak();
  invalidate();
}

function seekReplay(timeMs) {
  const { series } = state.replay;
  const index = series.lowerBound(timeMs, series.length);
  series.end = Math.max(1, Math.min(series.length, index + 1));
  invalidate();
}

function advanceReplay(now) {
  const replay = state.replay;
  if (!replay || !replay.playing) {
    if (replay) replay.lastFrame = null;
    return;
  }
  const { series } = replay;
  const lastTime = series.time[series.length - 1];
  if (replay.lastFrame !== null) {
    const timeMs = series.time[series.end - 1] + (now - replay.lastFrame) * replay.speed;
    seekReplay(timeMs);
    if (timeMs >= lastTime) {
      series.end = series.length;
      replay.playing = false;
    }
    updateReplayControls();
  }
  replay.lastFrame = now;
}

function updateReplayControls() {
  const replay = state.replay;
  if (!replay) return;
  const { series } = replay;
  const position = series.time[series.end - 1];
  $("replaySlider").value = position / 1000;
  $("replayTime").textContent = `${formatElapsed(position / 1000)} / ${formatElapsed(series.time[series.length - 1] / 1000)}`;
  $("replayPlay").textContent = replay.playing ? "Pause" : "Play";
}

// -------------------------------- controls --------------------------------

function updateZeroButton() {
  $("zeroButton").textContent = state.baseline ? "Clear zero" : "Zero sensors";
}

function zeroSensors() {
  if (state.baseline) {
    state.baseline = null;
  } else {
    const series = currentSeries();
    if (series.end === 0) {
      state.localError = "Zeroing needs data first. Start the live view with no load on the sensors.";
      renderBanner();
      return;
    }
    const start = series.lowerBound(series.time[series.end - 1] - 1000);
    const count = series.end - start;
    state.baseline = new Float32Array(COUNT);
    for (let i = start; i < series.end; i++) {
      for (const channel of CHANNELS) state.baseline[channel - 1] += series.value(i, channel) / count;
    }
  }
  updateZeroButton();
  invalidate();
}

function applyRangeInputs() {
  const min = Number($("rangeMin").value);
  const max = Number($("rangeMax").value);
  const valid = Number.isFinite(min) && Number.isFinite(max) && min < max;
  $("rangeMin").setCustomValidity(valid ? "" : "The minimum must be below the maximum.");
  if (!valid) {
    $("rangeMin").reportValidity();
    return;
  }
  state.range = { min, max };
  state.ranges[units.mode] = { min, max };
  updateScales();
  invalidate();
}

function setUnits(mode) {
  units.mode = mode;
  state.range = { ...state.ranges[mode] };
  $("rangeMin").value = state.range.min;
  $("rangeMax").value = state.range.max;
  $("rangeUnit").textContent = unitLabel();
  $("unitNote").hidden = !(mode === "N" || mode === "MPa");
  // both are in the old unit, so they'd be meaningless after switching
  resetPeak();
  state.baseline = null;
  updateZeroButton();
  syncSelection();
  updateScales();
  invalidate();
  try {
    localStorage.setItem("flexiforce-units", mode);
  } catch {
    // only a convenience, so it's fine if storage is blocked
  }
}

function applyCalibrations(sensorsMap) {
  units.calibration = sensorsMap || {};
  const count = Object.keys(units.calibration).length;
  for (const option of $("unitSelect").options) {
    if (option.value === "V") continue;
    option.textContent = count < CHANNELS.length
      ? `${option.dataset.label} (${count} of ${CHANNELS.length} calibrated)`
      : option.dataset.label;
  }
  $("unitNote").textContent =
    `Pressure is each sensor's force spread evenly over its ${SENSING_DIAMETER_MM} mm sensing circle `
    + `(${SENSING_AREA_MM2.toFixed(2)} mm²), so it's the average over that circle.`;
  setUnits(units.mode);
}

function bindControls() {
  $("findArduinos").addEventListener("click", () => refreshPorts());
  $("portSelect").addEventListener("change", (event) => savePort("flexiforce-port", event.target.value));
  $("gripperPort").addEventListener("change", (event) => savePort("flexiforce-gripper-port", event.target.value));
  $("refreshRecordings").addEventListener("click", refreshRecordings);

  $("recordButton").addEventListener("click", async () => {
    if (state.status && state.status.recording) {
      await post("/api/record/stop");
      refreshRecordings();
      return;
    }
    const running = state.status && state.status.state !== "idle";
    const port = running ? state.status.port : requirePort();
    if (!port) return;
    if (state.replay) exitReplay();
    await post("/api/record/start", {
      port,
      label: $("runLabel").value,
      duration_s: $("autoStop").value,
    });
  });

  $("connectButton").addEventListener("click", async () => {
    if (state.status && state.status.state !== "idle") {
      await post("/api/disconnect");
      refreshRecordings();
      return;
    }
    const port = requirePort();
    if (!port) return;
    if (state.replay) exitReplay();
    await post("/api/connect", { port });
  });

  $("markerButton").addEventListener("click", () => post("/api/marker"));
  document.addEventListener("keydown", (event) => {
    const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(event.target.tagName);
    if (event.key.toLowerCase() === "m" && !typing && !event.ctrlKey && !event.metaKey && !event.altKey
        && !$("markerButton").disabled) {
      post("/api/marker");
    }
  });

  for (const button of document.querySelectorAll("[data-view]")) {
    button.addEventListener("click", () => cylinder.setView(VIEW_PRESETS[button.dataset.view]));
  }

  $("unitSelect").addEventListener("change", (event) => setUnits(event.target.value));

  $("windowSelect").addEventListener("change", (event) => {
    state.windowS = Number(event.target.value);
    invalidate();
  });
  $("colorMode").addEventListener("change", (event) => {
    state.colorMode = event.target.value;
    $("resetPeak").hidden = state.colorMode !== "peak";
    invalidate();
  });
  $("resetPeak").addEventListener("click", () => {
    resetPeak(true);
    invalidate();
  });
  $("rangeMin").addEventListener("change", applyRangeInputs);
  $("rangeMax").addEventListener("change", applyRangeInputs);
  $("zeroButton").addEventListener("click", zeroSensors);

  $("replayPlay").addEventListener("click", () => {
    const replay = state.replay;
    if (!replay) return;
    if (!replay.playing && replay.series.end >= replay.series.length) replay.series.end = 1;
    replay.playing = !replay.playing;
    updateReplayControls();
    invalidate();
  });
  $("replaySlider").addEventListener("input", (event) => {
    if (!state.replay) return;
    state.replay.playing = false;
    seekReplay(Number(event.target.value) * 1000);
    $("replayTime").textContent = `${formatElapsed(Number(event.target.value))} / ${formatElapsed(state.replay.series.time[state.replay.series.length - 1] / 1000)}`;
    $("replayPlay").textContent = "Play";
  });
  $("replaySpeed").addEventListener("change", (event) => {
    if (state.replay) state.replay.speed = Number(event.target.value);
  });
  $("replayExit").addEventListener("click", exitReplay);
}

const gripper = initGripper({
  post,
  sensorPort: () => $("portSelect").value,
  runLabel: () => $("runLabel").value,
  sensorName: (column) => {
    const channel = Number(column.match(/\d+/)[0]);
    const sensor = sensors.find((s) => s.channel === channel);
    return sensor ? `S${channel} (${sensor.location})` : `S${channel}`;
  },
  beforeTestStart: () => {
    if (state.replay) exitReplay();
  },
  onResult: refreshRecordings,
});

async function request(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status}).`);
  return payload;
}

const calibration = initCalibration({
  sensors,
  request,
  latestLive: (channel) => (state.status && state.status.state === "live" && live.length > 0
    ? live.raw(live.length - 1, channel)
    : null),
  liveSince: {
    now: () => (live.length > 0 ? live.time[live.length - 1] : 0),
    values: (channel, fromMs) => {
      const values = [];
      for (let i = live.lowerBound(fromMs + 0.001, live.length); i < live.length; i++) values.push(live.raw(i, channel));
      return values;
    },
  },
  onSaved: applyCalibrations,
  focusSensor: (channel) => {
    if (!state.selected.has(channel)) toggleSensor(channel);
  },
  showError: (message) => {
    state.localError = message;
    renderBanner();
  },
});

async function loadCalibrations() {
  try {
    const snapshot = await (await fetch("/api/calibration")).json();
    calibration.load(snapshot);
    applyCalibrations(snapshot.sensors);
  } catch (error) {
    state.localError = "Couldn't load the sensor calibrations.";
    renderBanner();
  }
}

// ------------------------------- main loop --------------------------------

let lastDraw = 0;
let lastText = 0;

function frame(now) {
  requestAnimationFrame(frame);
  advanceReplay(now);
  if (!dirty || now - lastDraw < 33) return;
  lastDraw = now;
  dirty = false;

  updatePeak();
  cylinder.render(modelStyle, tokens);
  updateEndView();
  updateHeatColors();
  drawChart();

  // numbers change too fast to read at full speed, and stats are the costly part
  if (now - lastText >= 150) {
    lastText = now;
    state.stats = computeStats();
    updateHeatText();
    updateSensorTable();
    updateTiles();
  } else {
    dirty = true; // come back so the last values on screen are never stale
  }
}

function init() {
  readTokens();
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    readTokens();
    syncSelection();
  });

  buildHeatGrid();
  buildEndView();
  buildSensorTable();
  buildScales();
  bindControls();
  bindChartHover();

  let savedUnits = null;
  try {
    savedUnits = localStorage.getItem("flexiforce-units");
  } catch {
    // only a convenience, so it's fine if storage is blocked
  }
  if (["force", "N", "MPa"].includes(savedUnits)) $("unitSelect").value = savedUnits;
  setUnits($("unitSelect").value);
  loadCalibrations();

  // start with each side's middle sensor on the chart
  for (const side of SIDES) toggleSensor(side.channels[Math.floor(side.channels.length / 2)]);

  fitCanvases();
  new ResizeObserver(fitCanvases).observe($("modelCanvas").parentElement);
  new ResizeObserver(fitCanvases).observe(chart.canvas.parentElement);

  refreshPorts({ likelyOnly: true });
  refreshRecordings();
  connectStream();
  requestAnimationFrame(frame);
}

init();

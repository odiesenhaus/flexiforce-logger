// The sensor calibration panel: walks through one sensor at a time, captures
// readings with known weights, fits a line on the server, and saves it.

import { CALIBRATION_WEIGHTS_G, FORCE_UNIT, SATURATION_LEVEL } from "/config.js";

const $ = (id) => document.getElementById(id);

// Rounds first, so a tiny negative error shows as 0.0 rather than −0.0.
function grams(value, signed = false) {
  const rounded = Math.abs(value) < 0.05 ? 0 : value;
  const sign = signed ? (rounded > 0 ? "+" : rounded < 0 ? "−" : "") : rounded < 0 ? "−" : "";
  return `${sign}${Math.abs(rounded).toFixed(1)} ${FORCE_UNIT}`;
}

export function initCalibration({ sensors, request, latestLive, liveSince, onSaved, focusSensor, showError }) {
  let saved = {};
  let channel = sensors[0].channel;
  const drafts = new Map(); // channel -> captured points, kept while switching sensors
  let preview = null;
  let capture = null;

  const points = () => drafts.get(channel) || [];
  const sensorFor = (c) => sensors.find((s) => s.channel === c);

  function setSaved(snapshot) {
    saved = snapshot.sensors || {};
    if (snapshot.error) showError(snapshot.error);
    renderSensorSelect();
    renderSaved();
  }

  function renderSensorSelect() {
    const select = $("calSensor");
    const current = select.value || String(channel);
    select.replaceChildren();
    for (const sensor of [...sensors].sort((a, b) => a.channel - b.channel)) {
      const option = document.createElement("option");
      option.value = sensor.channel;
      option.textContent = `S${sensor.channel} (${sensor.location})${saved[sensor.channel] ? " ✓" : ""}`;
      select.append(option);
    }
    select.value = current;
    const done = Object.keys(saved).length;
    $("calSummary").textContent = `${done} of ${sensors.length} sensors calibrated`;
  }

  function renderSaved() {
    const entry = saved[channel];
    $("calClear").hidden = !entry;
    $("calSavedNote").textContent = entry
      ? `S${channel} was calibrated ${new Date(entry.calibrated_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}.`
      : `S${channel} isn't calibrated yet.`;
  }

  function selectSensor(next) {
    channel = next;
    $("calSensor").value = String(channel);
    preview = null;
    focusSensor(channel);
    renderSaved();
    refit();
  }

  // ------------------------------ live readout -----------------------------

  function activeFit() {
    if (preview && !preview.error) return { fit: preview, label: "with this fit" };
    if (saved[channel]) return { fit: saved[channel], label: "with the saved calibration" };
    return null;
  }

  function updateLive() {
    const latest = latestLive(channel);
    const value = $("calLiveValue");
    const force = $("calLiveForce");
    const flag = $("calLiveFlag");
    if (latest === null) {
      value.textContent = "–";
      force.textContent = "Start the live view or a recording to see this sensor.";
      flag.textContent = "";
      return;
    }
    value.textContent = `${latest.toFixed(3)} V`;
    const active = activeFit();
    force.textContent = active
      ? `≈ ${((latest - active.fit.intercept_v) / active.fit.slope_v_per_g).toFixed(0)} ${FORCE_UNIT} ${active.label}`
      : `${Math.round((latest / 5) * 100)}% of the 5 V range`;
    flag.textContent = latest >= SATURATION_LEVEL ? "⚠ Saturated" : "";
  }

  // -------------------------------- capture --------------------------------

  function startCapture(grams) {
    if (capture) return;
    if (!Number.isFinite(grams) || grams < 0) {
      showError("Enter the weight in grams.");
      return;
    }
    const latest = latestLive(channel);
    if (latest === null) {
      showError("Capturing needs live data. Start the live view first.");
      return;
    }
    const seconds = Number($("calSeconds").value) || 3;
    capture = { grams, channel, seconds, started: performance.now(), from: liveSince.now() };
    setCaptureButtons(true);
    tickCapture();
  }

  function tickCapture() {
    if (!capture) return;
    const elapsed = (performance.now() - capture.started) / 1000;
    if (elapsed < capture.seconds) {
      $("calCaptureStatus").textContent = `Capturing ${capture.grams} ${FORCE_UNIT} on S${capture.channel}: hold still, ${Math.ceil(capture.seconds - elapsed)} s…`;
      setTimeout(tickCapture, 200);
      return;
    }
    const values = liveSince.values(capture.channel, capture.from);
    const done = capture;
    capture = null;
    setCaptureButtons(false);
    if (values.length < 5) {
      $("calCaptureStatus").textContent = "";
      showError("Not enough data arrived during the capture. Check that the sensors are live.");
      return;
    }
    const mean = values.reduce((a, b) => a + b, 0) / values.length;
    const std = Math.sqrt(values.reduce((a, b) => a + (b - mean) ** 2, 0) / Math.max(1, values.length - 1));
    const list = drafts.get(done.channel) || [];
    list.push({ grams: done.grams, volts: mean, std });
    list.sort((a, b) => a.grams - b.grams);
    drafts.set(done.channel, list);
    $("calCaptureStatus").textContent = `Captured ${done.grams} ${FORCE_UNIT}: ${mean.toFixed(3)} V.`;
    if (done.channel === channel) refit();
  }

  function setCaptureButtons(busy) {
    for (const button of document.querySelectorAll("[data-cal-weight], #calCaptureOther")) button.disabled = busy;
  }

  // ---------------------------------- fit ----------------------------------

  async function refit() {
    const list = points();
    preview = null;
    if (list.length >= 2) {
      try {
        preview = await request("/api/calibration/fit", { points: list });
      } catch (error) {
        preview = { error: error.message };
      }
    }
    renderPoints();
  }

  function renderPoints() {
    const list = points();
    const body = $("calPoints");
    body.replaceChildren();
    const fitted = preview && !preview.error ? preview.points : null;
    list.forEach((point, index) => {
      const row = document.createElement("tr");
      const addCell = (text, numeric) => {
        const td = document.createElement("td");
        td.textContent = text;
        if (numeric) td.className = "num";
        row.append(td);
      };
      const f = fitted ? fitted[index] : null;
      addCell(`${point.grams} ${FORCE_UNIT}`, true);
      addCell(point.volts.toFixed(3), true);
      addCell(point.std.toFixed(3), true);
      if (point.volts >= SATURATION_LEVEL) {
        addCell("⚠ Saturated, left out of the fit");
        addCell("");
      } else {
        addCell(f ? grams(f.fit_grams) : "–", true);
        addCell(f ? grams(f.error_grams, true) : "–", true);
      }
      const actions = document.createElement("td");
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "ghost";
      remove.textContent = "Remove";
      remove.addEventListener("click", () => {
        list.splice(index, 1);
        refit();
      });
      actions.append(remove);
      row.append(actions);
      body.append(row);
    });
    $("calPointsTable").hidden = list.length === 0;

    const result = $("calFit");
    if (!preview) {
      result.textContent = list.length ? "Capture one more weight to fit a line." : "";
    } else if (preview.error) {
      result.textContent = preview.error;
    } else {
      const used = preview.points.filter((p) => p.used);
      const worst = used.reduce((a, p) => (Math.abs(p.error_grams) > Math.abs(a.error_grams) ? p : a), used[0]);
      result.textContent =
        `Volts = ${preview.slope_v_per_g.toFixed(5)} × grams + ${preview.intercept_v.toFixed(3)} · ` +
        `R² ${preview.r2.toFixed(4)} · largest error ${grams(Math.abs(worst.error_grams))} ` +
        `at ${worst.grams} ${FORCE_UNIT} · saturates at about ${preview.saturates_at_g.toFixed(0)} ${FORCE_UNIT}`;
    }
    $("calSave").disabled = !preview || Boolean(preview.error);
  }

  // -------------------------------- buttons --------------------------------

  const weights = $("calWeights");
  for (const grams of CALIBRATION_WEIGHTS_G) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.calWeight = grams;
    button.textContent = grams === 0 ? "No load (0 g)" : `${grams} ${FORCE_UNIT}`;
    button.addEventListener("click", () => startCapture(grams));
    weights.append(button);
  }
  $("calCaptureOther").addEventListener("click", () => startCapture(Number($("calOtherWeight").value)));

  $("calSensor").addEventListener("change", (event) => selectSensor(Number(event.target.value)));
  $("calNext").addEventListener("click", () => {
    const ordered = [...sensors].map((s) => s.channel).sort((a, b) => a - b);
    selectSensor(ordered[(ordered.indexOf(channel) + 1) % ordered.length]);
  });

  $("calSave").addEventListener("click", async () => {
    try {
      const snapshot = await request("/api/calibration/save", { channel, points: points() });
      drafts.delete(channel);
      preview = null;
      setSaved(snapshot);
      onSaved(snapshot.sensors);
      renderPoints();
      $("calCaptureStatus").textContent =
        `Saved S${channel}. The reading above now uses it, so put the weights back on to check it, then go to the next sensor.`;
    } catch (error) {
      showError(error.message);
    }
  });

  $("calClear").addEventListener("click", async () => {
    if (!window.confirm(`Remove S${channel}'s calibration? It stays in calibration.json's history.`)) return;
    try {
      const snapshot = await request("/api/calibration/clear", { channel });
      setSaved(snapshot);
      onSaved(snapshot.sensors);
    } catch (error) {
      showError(error.message);
    }
  });

  $("calPanel").addEventListener("toggle", () => {
    if ($("calPanel").open) focusSensor(channel);
  });

  setInterval(updateLive, 200);

  return {
    load: setSaved,
  };
}

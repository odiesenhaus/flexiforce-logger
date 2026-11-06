// The gripper panel and the test results table. Everything else on the page
// keeps working without the gripper connected.

const $ = (id) => document.getElementById(id);

const PHASE_LABELS = {
  connecting: "Connecting",
  preparing: "Opening the gripper and getting ready",
  closed: "Closed",
  open: "Open",
  finishing: "Saving results",
};

export function initGripper({ post, sensorPort, runLabel, sensorName, beforeTestStart, onResult }) {
  let status = null;
  let result = null;
  let shownResultId = null;

  function update(next) {
    status = next;
    const { actuator, test } = next;
    const connected = actuator.state === "connected";

    const pill = $("gripperPill");
    pill.dataset.state = actuator.state;
    $("gripperText").textContent = connected
      ? `Gripper ${actuator.gripper} on ${actuator.port}`
      : "Gripper not connected";

    const portSelect = $("gripperPort");
    if (connected && portSelect.value !== actuator.port) portSelect.value = actuator.port;
    portSelect.disabled = connected || test.running;

    const connectButton = $("gripperConnect");
    connectButton.textContent = connected ? "Disconnect" : "Connect";
    connectButton.disabled = test.running;
    $("gripperOpen").disabled = !connected || test.running;
    $("gripperClose").disabled = !connected || test.running;
    $("findArduinos").disabled = test.running;

    const testButton = $("testButton");
    testButton.textContent = test.running ? "Stop test" : "Start test";
    testButton.classList.toggle("recording", test.running);
    for (const id of ["testCycles", "testClosed", "testOpen", "testSettle"]) $(id).disabled = test.running;

    $("testProgress").hidden = !test.running;
    if (test.running) {
      const label = PHASE_LABELS[test.phase] || "Starting";
      const trial = test.trial > 0 ? `Trial ${test.trial} of ${test.cycles} · ` : "";
      const left = test.phase_remaining_s === null ? "" : ` · ${test.phase_remaining_s.toFixed(0)} s left`;
      $("testProgressText").textContent = `${trial}${label}${left}`;
      const bar = $("testProgressBar");
      if (test.phase_total_s > 0) bar.value = 1 - test.phase_remaining_s / test.phase_total_s;
      else bar.removeAttribute("value");
    }
    $("testMessage").textContent = test.running ? "" : test.message || actuator.error || "";

    if (test.result_id !== shownResultId) {
      const isNew = shownResultId !== null;
      shownResultId = test.result_id;
      if (test.result_id > 0) loadResult(isNew);
    }
  }

  async function loadResult(reveal) {
    const response = await fetch("/api/test/result");
    result = await response.json();
    renderResult();
    onResult();
    if (reveal) $("resultsCard").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function renderResult() {
    if (!result || !result.channels) return;
    const card = $("resultsCard");
    card.hidden = false;

    const phase = $("resultsPhase").value;
    const stats = result.phases[phase];
    const numbers = result.trial_numbers;

    const head = $("resultsHead");
    head.replaceChildren();
    const headRow = document.createElement("tr");
    const addHeader = (text, numeric) => {
      const th = document.createElement("th");
      th.scope = "col";
      th.textContent = text;
      if (numeric) th.className = "num";
      headRow.append(th);
    };
    addHeader("Sensor");
    for (const number of numbers) addHeader(`Trial ${number}`, true);
    addHeader("Average", true);
    addHeader("Std between trials", true);
    head.append(headRow);

    const body = $("resultsBody");
    body.replaceChildren();
    let anySaturated = false;
    for (const channel of result.channels) {
      const row = document.createElement("tr");
      const addCell = (text, numeric) => {
        const td = document.createElement("td");
        td.textContent = text;
        if (numeric) td.className = "num";
        row.append(td);
      };
      addCell(sensorName(channel));
      const channelStats = stats[channel];
      if (channelStats) {
        for (const trial of channelStats.trials) {
          const saturated = trial.saturated_pct > 0;
          anySaturated = anySaturated || saturated;
          addCell(`${saturated ? "⚠ " : ""}${trial.mean.toFixed(3)} ± ${trial.std.toFixed(3)}`, true);
        }
        addCell(channelStats.mean.toFixed(3), true);
        addCell(channelStats.std === null ? "–" : channelStats.std.toFixed(3), true);
      }
      body.append(row);
    }
    $("resultsSaturated").hidden = !anySaturated;

    const summary = `Each cell is the mean ± standard deviation in V for that trial, leaving out the first ${result.settle_s} s of each phase while the gripper moves.`;
    $("resultsNote").textContent = result.completed
      ? summary
      : `Ended early with ${result.trials_completed} of ${result.cycles_requested} trials finished: ${result.note} ${summary}`;

    const files = $("resultsFiles");
    files.replaceChildren();
    const addFile = (label, name) => {
      const link = document.createElement("a");
      link.href = `/api/recordings/${encodeURIComponent(name)}?download=1`;
      link.textContent = `${label}: ${name}`;
      files.append(link);
    };
    addFile("All trials", result.trials_file);
    addFile("Summary", result.summary_file);
    addFile("Full recording", result.raw_file);
  }

  $("resultsPhase").addEventListener("change", renderResult);

  $("gripperConnect").addEventListener("click", () => {
    if (status && status.actuator.state === "connected") post("/api/gripper/disconnect");
    else post("/api/gripper/connect", { port: $("gripperPort").value });
  });
  $("gripperOpen").addEventListener("click", () => post("/api/gripper/move", { position: "open" }));
  $("gripperClose").addEventListener("click", () => post("/api/gripper/move", { position: "closed" }));

  $("testButton").addEventListener("click", () => {
    if (status && status.test.running) {
      post("/api/test/stop");
      return;
    }
    beforeTestStart();
    post("/api/test/start", {
      port: sensorPort(),
      actuator_port: $("gripperPort").value,
      label: runLabel(),
      cycles: $("testCycles").value,
      closed_s: $("testClosed").value,
      open_s: $("testOpen").value,
      settle_s: $("testSettle").value,
    });
  });

  return { update };
}

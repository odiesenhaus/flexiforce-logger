"""
gripper_test.py
----------------
Used by dashboard.py to run automated gripper tests. Talks to the actuator
Arduino (actuator_control.ino), opens and closes the gripper on a schedule
while the sensors are recorded, then splits the recording into trials.

Each test leaves three files:
    flexiforce_<time>_<name>.csv           everything, with the events marked
    flexiforce_<time>_<name>_trials.csv    only the complete trials, labeled
    flexiforce_<time>_<name>_summary.csv   mean and std for every trial and sensor

Actuator commands are limited to open and closed. The positions themselves
live in actuator_control.ino, so nothing here can drive it past its limits.
"""

import csv
import os
import re
import statistics
import threading
import time

import serial
from serial.tools import list_ports

SENSOR_BAUD = 115200
ACTUATOR_BAUD = 9600
SIMULATED_ACTUATOR_PORT = "SIMULATED_GRIPPER"
SATURATION_V = 4.98
ACK_TIMEOUT_S = 1.0
HANDSHAKE_TIMEOUT_S = 3.5
PREPARE_S = 5.0   # lets the gripper finish opening before the first trial
START_TIMEOUT_S = 10.0

COMMON_USB_SERIAL_VIDS = {0x2341, 0x2A03, 0x1A86, 0x10C4, 0x0403}  # Arduino, CH340, CP210x, and FTDI chips

GRIPPER_EVENT = re.compile(r"^# GRIPPER (OPEN|CLOSED) (\w+)")
IDENTITY = re.compile(r"^# ACTUATOR CONTROLLER(?: gripper=(\w+))?")
TRIAL_MARKER = re.compile(r"Trial (\d+) (close|open)")
END_MARKER = "Test end"


def friendly_open_error(port, error):
    text = str(error)
    if "PermissionError" in text or "Access is denied" in text:
        return (f"{port} is in use by another program. Close the Arduino IDE's Serial Monitor/Plotter "
                "and any running collect_data.py or live_view.py, then try again.")
    if "FileNotFoundError" in text or "could not find" in text.lower():
        return f"{port} wasn't found. Check the USB cable and refresh the port list."
    return f"Couldn't open {port}: {text}"


def open_without_reset(port, baud, timeout):
    """Arduinos usually reset when a port opens, and DTR off prevents that."""
    ser = serial.Serial()
    ser.port = port
    ser.baudrate = baud
    ser.timeout = timeout
    ser.dtr = False
    ser.rts = False
    ser.open()
    return ser


class SimulatedGripper:
    """Shared between the simulated Arduinos so the fake sensors respond to the fake gripper."""

    TRAVEL_S = 2.0

    def __init__(self):
        self.active = False
        self.closed = False
        self.changed_at = 0.0
        self.grip = 1.0

    def move(self, closed):
        if closed and not self.closed:
            self.grip = 0.9 + 0.1 * (time.monotonic() % 1.0)  # each grip lands a little differently
        self.closed = closed
        self.changed_at = time.monotonic()

    def closure(self):
        """How far closed, from 0 to 1."""
        progress = min(1.0, (time.monotonic() - self.changed_at) / self.TRAVEL_S)
        return self.grip * (progress if self.closed else 1.0 - progress)


simulated_gripper = SimulatedGripper()


class SimulatedActuator:
    """Stands in for the actuator Arduino, speaking the same protocol as actuator_control.ino."""

    def __init__(self):
        self.lines = []
        self.state = "unknown"
        simulated_gripper.active = True
        simulated_gripper.closed = False
        simulated_gripper.changed_at = 0.0

    def write(self, data):
        for command in data.decode():
            if command == "?":
                self.lines.append(f"# ACTUATOR CONTROLLER gripper={self.state}")
            elif command in "oc":
                closed = command == "c"
                simulated_gripper.move(closed)
                self.state = "closed" if closed else "open"
                self.lines.append(f"# GRIPPER {self.state.upper()} command")

    def press_button(self):
        closed = self.state != "closed"
        simulated_gripper.move(closed)
        self.state = "closed" if closed else "open"
        self.lines.append(f"# GRIPPER {self.state.upper()} button")

    def readline(self):
        if self.lines:
            return (self.lines.pop(0) + "\r\n").encode()
        time.sleep(0.02)
        return b""

    def close(self):
        simulated_gripper.active = False


class ActuatorLink:
    """The connection to the actuator Arduino. Separate from the sensor connection."""

    def __init__(self, on_button=None):
        self.lock = threading.Lock()
        self.ser = None
        self.port = None
        self.state = "disconnected"  # disconnected or connected
        self.gripper = "unknown"     # unknown, open, or closed
        self.error = None
        self.on_button = on_button
        self.button_presses = 0
        self.ack = threading.Event()

    def status(self):
        return {"state": self.state, "port": self.port, "gripper": self.gripper, "error": self.error}

    def connect(self, port):
        if not port:
            raise ValueError("Choose the gripper's serial port first.")
        with self.lock:
            if self.state == "connected" and self.port == port:
                return
        self.disconnect()

        self.error = None
        try:
            if port == SIMULATED_ACTUATOR_PORT:
                ser = SimulatedActuator()
            else:
                ser = open_without_reset(port, ACTUATOR_BAUD, 0.1)
        except (serial.SerialException, OSError) as e:
            raise ValueError(friendly_open_error(port, e))

        gripper = self._handshake(ser)
        if gripper is None:
            ser.close()
            raise ValueError(f"{port} didn't answer as the gripper Arduino. Check that it has the latest "
                             "actuator_control.ino and that this isn't the sensor Arduino's port.")

        with self.lock:
            self.ser = ser
            self.port = port
            self.gripper = gripper
            self.state = "connected"
        threading.Thread(target=self._read_loop, args=(ser,), daemon=True).start()

    def _handshake(self, ser):
        # asks more than once, in case the board reset anyway and is still booting
        deadline = time.monotonic() + HANDSHAKE_TIMEOUT_S
        next_ask = 0.0
        while time.monotonic() < deadline:
            if time.monotonic() >= next_ask:
                ser.write(b'?')
                next_ask = time.monotonic() + 0.3
            match = IDENTITY.match(ser.readline().decode("utf-8", errors="replace").strip())
            if match and match.group(1):
                return match.group(1)
        return None

    def disconnect(self):
        with self.lock:
            ser = self.ser
            self.ser = None
            self.port = None
            self.state = "disconnected"
            self.gripper = "unknown"
        if ser is not None:
            ser.close()

    def _read_loop(self, ser):
        while True:
            try:
                line = ser.readline().decode("utf-8", errors="replace").strip()
            except (serial.SerialException, OSError, TypeError, AttributeError):
                with self.lock:
                    lost = self.ser is ser  # otherwise it was closed on purpose
                if lost:
                    port = self.port
                    self.disconnect()
                    self.error = f"Lost the connection to the gripper on {port}."
                return

            with self.lock:
                if self.ser is not ser:
                    return

            match = GRIPPER_EVENT.match(line)
            if not match:
                continue
            self.gripper = match.group(1).lower()
            if match.group(2) == "button":
                self.button_presses += 1
                if self.on_button:
                    self.on_button(self.gripper)
            else:
                self.ack.set()

    def move(self, position):
        """Sends a move and waits for the Arduino to confirm it."""
        with self.lock:
            ser = self.ser
        if ser is None:
            raise ValueError("The gripper isn't connected.")
        self.ack.clear()
        try:
            ser.write(b'o' if position == "open" else b'c')
        except (serial.SerialException, OSError):
            raise ValueError("Lost the connection to the gripper.")
        if not self.ack.wait(ACK_TIMEOUT_S):
            raise ValueError("The gripper Arduino didn't confirm the move.")


def probe_port(device):
    """Works out which Arduino is on a port. Returns 'sensors', 'actuator', 'busy', or None."""
    try:
        ser = open_without_reset(device, ACTUATOR_BAUD, 0.05)
    except (serial.SerialException, OSError):
        return "busy"

    # The actuator is tried first, at its own baud rate. Bytes sent at the sensor's
    # higher rate can only arrive at the actuator as high values, never as 'o' or 'c'.
    try:
        for _ in range(2):  # a second pass in case the board reset and was still booting
            ser.baudrate = ACTUATOR_BAUD
            ser.reset_input_buffer()
            deadline = time.monotonic() + 0.9
            while time.monotonic() < deadline:
                ser.write(b'?')
                for _ in range(6):
                    if IDENTITY.match(ser.readline().decode("utf-8", errors="replace").strip()):
                        return "actuator"

            ser.baudrate = SENSOR_BAUD
            ser.reset_input_buffer()
            deadline = time.monotonic() + 0.9
            while time.monotonic() < deadline:
                ser.write(b's')
                for _ in range(6):
                    line = ser.readline().decode("utf-8", errors="replace").strip()
                    if line.startswith("time_ms") or "FlexiForce" in line:
                        ser.write(b'x')
                        return "sensors"
            ser.write(b'x')
        return None
    except (serial.SerialException, OSError):
        return None
    finally:
        ser.close()


def identify_arduinos(in_use=None, likely_only=False):
    """
    Finds the sensor and actuator Arduinos by asking each USB serial port.
    COM numbers differ between computers, so this is what makes the code portable.
    `in_use` maps ports the dashboard already holds to their role, since they can't be opened.
    """
    in_use = in_use or {}
    roles = dict(in_use)
    threads = []

    def work(device):
        roles[device] = probe_port(device)

    for p in list_ports.comports():
        if p.vid is None or p.device in in_use:
            continue  # no vendor ID means it isn't USB, like the Bluetooth ports
        if likely_only and p.vid not in COMMON_USB_SERIAL_VIDS:
            continue
        thread = threading.Thread(target=work, args=(p.device,), daemon=True)
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join(timeout=6)

    found = {"sensors": None, "actuator": None, "busy": []}
    for device, role in list(roles.items()):
        if role in ("sensors", "actuator"):
            found[role] = device
        elif role == "busy":
            found["busy"].append(device)
    return found


# ------------------------------- analysis --------------------------------

def read_run(raw_path):
    with open(raw_path, newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = [row for row in reader if len(row) == len(header)]
    return header, rows


def split_trials(header, rows):
    """Finds each trial's boundaries from the markers. Only complete trials are returned."""
    time_col = header.index("time_ms")
    marker_col = header.index("marker")

    closes, opens, end = {}, {}, None
    for row in rows:
        marker = row[marker_col]
        if not marker:
            continue
        for number, event in TRIAL_MARKER.findall(marker):
            (closes if event == "close" else opens)[int(number)] = float(row[time_col])
        if END_MARKER in marker:
            end = float(row[time_col])

    trials = []
    for number in sorted(closes):
        finish = closes.get(number + 1, end)
        if number in opens and finish is not None:
            trials.append({"number": number, "close": closes[number], "open": opens[number], "end": finish})
    return trials


def phase_of(trial, t):
    if trial["close"] <= t < trial["open"]:
        return "closed", t - trial["close"]
    if trial["open"] <= t < trial["end"]:
        return "open", t - trial["open"]
    return None, None


def summarize(values):
    return {
        "mean": statistics.fmean(values),
        "std": statistics.stdev(values) if len(values) > 1 else 0.0,
        "min": min(values),
        "max": max(values),
        "saturated_pct": 100.0 * sum(v >= SATURATION_V for v in values) / len(values),
        "samples": len(values),
    }


def analyze_run(raw_path, settle_s):
    """
    Splits a marked recording into trials and works out the statistics.
    The first settle_s of each phase counts as the gripper moving, so it stays
    in the trials file but is left out of the statistics.
    """
    header, rows = read_run(raw_path)
    trials = split_trials(header, rows)
    channels = [c for c in header if c.startswith("S") and c.endswith("_V")]
    time_col = header.index("time_ms")
    columns = {c: header.index(c) for c in channels}
    settle_ms = settle_s * 1000

    labeled = []
    samples = {t["number"]: {"closed": {c: [] for c in channels}, "open": {c: [] for c in channels}} for t in trials}
    for row in rows:
        t = float(row[time_col])
        for trial in trials:
            phase, since_start = phase_of(trial, t)
            if phase is None:
                continue
            in_stats = since_start >= settle_ms
            labeled.append((row, trial, phase, in_stats))
            if in_stats:
                for channel in channels:
                    samples[trial["number"]][phase][channel].append(float(row[columns[channel]]))
            break

    phases = {}
    for phase in ("closed", "open"):
        phases[phase] = {}
        for channel in channels:
            per_trial = [summarize(samples[t["number"]][phase][channel]) for t in trials
                         if samples[t["number"]][phase][channel]]
            if not per_trial:
                continue
            means = [s["mean"] for s in per_trial]
            total = sum(s["samples"] for s in per_trial)
            phases[phase][channel] = {
                "trials": per_trial,
                "mean": statistics.fmean(means),
                # how much the trials differ from each other
                "std": statistics.stdev(means) if len(means) > 1 else None,
                "min": min(s["min"] for s in per_trial),
                "max": max(s["max"] for s in per_trial),
                "saturated_pct": sum(s["saturated_pct"] * s["samples"] for s in per_trial) / total,
                "samples": total,
            }

    return {"header": header, "trials": trials, "channels": channels, "labeled": labeled, "phases": phases}


def write_trials_csv(path, analysis):
    channels = analysis["channels"]
    header = analysis["header"]
    time_col = header.index("time_ms")
    columns = [header.index(c) for c in channels]
    with open(path, "x", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["time_ms", "trial", "phase", "trial_time_s", "in_stats"] + channels)
        for row, trial, phase, in_stats in analysis["labeled"]:
            trial_time = (float(row[time_col]) - trial["close"]) / 1000.0
            writer.writerow([row[time_col], trial["number"], phase, f"{trial_time:.3f}", int(in_stats)]
                            + [row[c] for c in columns])


def write_summary_csv(path, analysis):
    trial_numbers = [t["number"] for t in analysis["trials"]]
    with open(path, "x", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["phase", "sensor", "trial", "mean_V", "std_V", "min_V", "max_V", "saturated_pct", "samples"])
        for phase, by_channel in analysis["phases"].items():
            for channel, stats in by_channel.items():
                for number, s in zip(trial_numbers, stats["trials"]):
                    writer.writerow([phase, channel, number, f"{s['mean']:.4f}", f"{s['std']:.4f}", f"{s['min']:.4f}",
                                     f"{s['max']:.4f}", f"{s['saturated_pct']:.1f}", s["samples"]])
                # "all" is the average of the trial means, with the std between those means
                std = "" if stats["std"] is None else f"{stats['std']:.4f}"
                writer.writerow([phase, channel, "all", f"{stats['mean']:.4f}", std, f"{stats['min']:.4f}",
                                 f"{stats['max']:.4f}", f"{stats['saturated_pct']:.1f}", stats["samples"]])


# ------------------------------- test runner ------------------------------

class TestStopped(Exception):
    """Ends a test early. The message is shown to the user."""


class GripperTest:
    """Runs the open/close schedule on its own thread while the sensors record."""

    def __init__(self, acquisition, link, data_dir):
        self.acquisition = acquisition
        self.link = link
        self.data_dir = data_dir
        self.lock = threading.Lock()
        self.thread = None
        self.stop_event = threading.Event()
        self.running = False
        self.phase = None
        self.trial = 0
        self.cycles = 0
        self.phase_ends_at = None
        self.phase_total_s = 0.0
        self.message = None
        self.result = None
        self.result_id = 0

    def status(self):
        remaining = None
        if self.phase_ends_at is not None:
            remaining = max(0.0, self.phase_ends_at - time.monotonic())
        return {
            "running": self.running,
            "phase": self.phase,
            "trial": self.trial,
            "cycles": self.cycles,
            "phase_remaining_s": remaining,
            "phase_total_s": self.phase_total_s,
            "message": self.message,
            "result_id": self.result_id,
        }

    def start(self, sensor_port, actuator_port, label, cycles, closed_s, open_s, settle_s):
        with self.lock:
            if self.running:
                raise ValueError("A test is already running.")
            if self.acquisition.recording is not None:
                raise ValueError("Stop the current recording before starting a test.")
            active_port = self.acquisition.port if self.acquisition.state != "idle" else sensor_port
            if not active_port:
                raise ValueError("Choose the sensor Arduino's serial port first.")
            if not actuator_port:
                raise ValueError("Choose the gripper Arduino's serial port first.")
            if active_port == actuator_port:
                raise ValueError("The sensors and the gripper need different serial ports.")
            simulated_sensors = active_port == self.acquisition.simulated_port
            if simulated_sensors != (actuator_port == SIMULATED_ACTUATOR_PORT):
                # never move the real gripper for fake data, or file fake motion with real data
                raise ValueError("Use the simulated sensors and simulated gripper together, or neither.")

            self.running = True
            self.message = None
            self.trial = 0
            self.cycles = cycles
            self.stop_event = threading.Event()
            self.thread = threading.Thread(
                target=self._run, daemon=True,
                args=(active_port, actuator_port, label, cycles, closed_s, open_s, settle_s))
            self.thread.start()

    def stop(self):
        self.stop_event.set()

    def join(self, timeout=None):
        if self.thread is not None:
            self.thread.join(timeout)

    def _set_phase(self, phase, seconds=None):
        self.phase = phase
        self.phase_total_s = seconds or 0.0
        self.phase_ends_at = None if seconds is None else time.monotonic() + seconds

    def _wait(self, seconds, button_presses):
        """Waits out a phase, ending the test if anything goes wrong during it."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.stop_event.wait(0.02):
                raise TestStopped("Stopped by user.")
            if self.acquisition.state != "live" or self.acquisition.recording is None:
                raise TestStopped(self.acquisition.error or "The sensor data stopped.")
            if self.link.state != "connected":
                raise TestStopped(self.link.error or "The gripper disconnected.")
            if self.link.button_presses != button_presses:
                raise TestStopped("The physical button was pressed during the test.")

    def _wait_for_sensors(self, sensor_port):
        self.acquisition.connect(sensor_port)
        deadline = time.monotonic() + START_TIMEOUT_S
        while self.acquisition.state != "live":
            if self.stop_event.wait(0.05):
                raise TestStopped("Stopped by user.")
            if self.acquisition.state == "idle":
                raise TestStopped(self.acquisition.error or "Couldn't start the sensors.")
            if time.monotonic() > deadline:
                raise TestStopped("No data from the sensors.")

    def _run(self, sensor_port, actuator_port, label, cycles, closed_s, open_s, settle_s):
        acq = self.acquisition
        raw_name = None
        completed = False
        moved = False
        try:
            self._set_phase("connecting")
            self.link.connect(actuator_port)
            self._wait_for_sensors(sensor_port)

            acq.start_recording(None, label, None)
            raw_name = acq.recording["name"]
            presses = self.link.button_presses

            self._set_phase("preparing", PREPARE_S)
            acq.add_marker("Test start")
            moved = True
            self.link.move("open")
            self._wait(PREPARE_S, presses)

            for trial in range(1, cycles + 1):
                self.trial = trial

                self._set_phase("closed", closed_s)
                acq.add_marker(f"Trial {trial} close")
                self.link.move("closed")
                self._wait(closed_s, presses)

                self._set_phase("open", open_s)
                acq.add_marker(f"Trial {trial} open")
                self.link.move("open")
                self._wait(open_s, presses)

            acq.add_marker(END_MARKER)
            time.sleep(0.1)  # gives the marker a sample to land on
            completed = True
        except (TestStopped, ValueError) as e:
            self.message = str(e)
        finally:
            self._set_phase("finishing")
            if moved and self.link.state == "connected" and self.link.gripper != "open":
                # always let go, whatever ended the test
                try:
                    self.link.move("open")
                except ValueError as e:
                    self.message = f"{self.message} The gripper could NOT be opened: {e}"
            acq.stop_recording()
            self._finish(raw_name, label, cycles, settle_s, completed)
            self._set_phase(None)
            self.running = False

    def _finish(self, raw_name, label, cycles, settle_s, completed):
        raw_path = os.path.join(self.data_dir, raw_name) if raw_name else None
        if raw_path is None or not os.path.exists(raw_path):
            return
        try:
            analysis = analyze_run(raw_path, settle_s)
            if not analysis["trials"]:
                self.message = f"{self.message or 'Test ended'} No trial finished, so only {raw_name} was saved."
                return

            stem = raw_name[:-len(".csv")]
            trials_name, summary_name = f"{stem}_trials.csv", f"{stem}_summary.csv"
            write_trials_csv(os.path.join(self.data_dir, trials_name), analysis)
            write_summary_csv(os.path.join(self.data_dir, summary_name), analysis)
        except OSError as e:
            self.message = f"Couldn't write the test results: {e}"
            return

        self.result = {
            "raw_file": raw_name,
            "trials_file": trials_name,
            "summary_file": summary_name,
            "trials_completed": len(analysis["trials"]),
            "cycles_requested": cycles,
            "settle_s": settle_s,
            "completed": completed,
            "note": self.message,
            "saturation_v": SATURATION_V,
            "channels": analysis["channels"],
            "phases": analysis["phases"],
            "trial_numbers": [t["number"] for t in analysis["trials"]],
        }
        self.result_id += 1

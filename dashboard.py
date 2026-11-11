"""
dashboard.py
-------------
Local web dashboard for the Arduino Mega FlexiForce logger. Shows the 10
sensors live on a 3D cylinder and a flat heat grid, records runs to CSV,
and replays saved runs. With the actuator Arduino plugged in too, it can
also run automated gripper tests (see gripper_test.py), and each sensor can
be calibrated to grams (see calibration.py). The page itself lives in the
dashboard/ folder.

Recordings use the same columns as collect_data.py plus a "marker"
column, so plot_data.py works on them too. They hold raw voltages only.

Requires: pyserial (pip install pyserial). The page has no other
dependencies and works without internet.

Usage:
    python dashboard.py
    python dashboard.py --http-port 8080 --no-browser
    python dashboard.py --data-dir runs

Only one program can use the serial port at a time, so close the Arduino
IDE's Serial Monitor/Plotter first. Press Ctrl+C to stop the server --
any recording in progress is closed out cleanly.
"""

import argparse
import csv
import json
import math
import os
import random
import re
import statistics
import threading
import time
import webbrowser
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

import serial
from serial.tools import list_ports

from calibration import CalibrationStore, fit as calibration_fit
from gripper_test import (COMMON_USB_SERIAL_VIDS, SENSOR_BAUD, SIMULATED_ACTUATOR_PORT, ActuatorLink, GripperTest,
                          friendly_open_error, identify_arduinos, simulated_gripper)

SIMULATED_PORT = "SIMULATED"
RESET_WAIT_S = 2.0        # the Mega resets when the port opens
NO_DATA_TIMEOUT_S = 5.0
FLUSH_EVERY_ROWS = 25     # at 100 Hz, a crash loses at most 1/4 second
MAX_CLIENT_BACKLOG = 2000
RECORDING_NAME = re.compile(r"^flexiforce_[\w-]+\.csv$")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard")
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/config.js": ("config.js", "text/javascript; charset=utf-8"),
    "/model3d.js": ("model3d.js", "text/javascript; charset=utf-8"),
    "/gripper.js": ("gripper.js", "text/javascript; charset=utf-8"),
    "/calibration.js": ("calibration.js", "text/javascript; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


class SimulatedArduino:
    """
    Stands in for the Mega so the dashboard can be tried without hardware.
    It speaks the same serial text protocol as flexiforce_logger.ino, so
    simulated data goes through the real parsing and recording code.
    """

    SAMPLE_INTERVAL_S = 0.01
    ARC_POSITIONS_IN = (1.0, 0.5, 0.0, -0.5, -1.0)
    HEADER = "time_ms," + ",".join(f"S{n}_V" for n in range(1, 11))

    def __init__(self):
        self.lines = deque()
        self.logging = False
        self.start = 0.0
        self.next_sample = 0.0
        self.count = 0

    def reset_input_buffer(self):
        self.lines.clear()

    def write(self, data):
        if data == b's':
            self.lines.extend(["# LOGGING START", self.HEADER])
            self.start = self.next_sample = time.monotonic()
            self.count = 0
            self.logging = True
        elif data == b'x':
            self.logging = False
            self.lines.append("# LOGGING STOP")

    def close(self):
        pass

    def readline(self):
        if self.lines:
            return (self.lines.popleft() + "\r\n").encode()
        if not self.logging:
            time.sleep(0.05)
            return b""

        delay = self.next_sample - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        t = self.count * self.SAMPLE_INTERVAL_S
        self.next_sample += self.SAMPLE_INTERVAL_S
        self.count += 1

        if self.count % 997 == 0:
            return b"12,0.4\r\n"  # a torn line, like the real port occasionally produces

        # with the simulated gripper connected the load follows it, and otherwise
        # it's a slow squeeze whose contact point drifts along the arc of each side
        gripped = simulated_gripper.active
        squeeze = simulated_gripper.closure() if gripped else math.sin(math.pi * t / 8.0) ** 2
        values = []
        for side_gain, drift in ((1.0, 0.4), (0.85, -0.3)):
            center = drift if gripped else drift * math.sin(t / 5.0)
            for arc in self.ARC_POSITIONS_IN:
                load = side_gain * squeeze * math.exp(-((arc - center) / 0.6) ** 2)
                values.append(min(5.0, 0.436 + 4.8 * load + random.gauss(0, 0.003)))
        fields = [str(round(t * 1000))] + [f"{v:.4f}" for v in values]
        return (",".join(fields) + "\r\n").encode()


def list_serial_ports():
    ports = []
    for p in list_ports.comports():
        description = p.description or p.device
        lowered = description.lower()
        ports.append({
            "device": p.device,
            "description": description,
            "likely_arduino": p.vid in COMMON_USB_SERIAL_VIDS or any(k in lowered for k in ("arduino", "ch340", "mega")),
            "bluetooth": "bluetooth" in lowered,
        })
    ports.sort(key=lambda p: (not p["likely_arduino"], p["bluetooth"], p["device"]))
    return ports


class Client:
    """One open browser tab, with the samples it hasn't been sent yet."""

    def __init__(self):
        self.samples = deque(maxlen=MAX_CLIENT_BACKLOG)
        self.markers = deque()


class Acquisition:
    """Owns the serial connection, parses the stream, and writes recordings."""

    simulated_port = SIMULATED_PORT

    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.lock = threading.RLock()
        self.clients = set()
        self.thread = None
        self.stop_event = None
        self.state = "idle"  # idle, connecting, or live
        self.port = None
        self.error = None
        self.recording = None
        self.last_saved = None
        self.pending_markers = []
        self.marker_count = 0
        self.calibration = None  # set in main(); adds newton and MPa columns to recordings
        self._reset_counters()

    def _reset_counters(self):
        self.samples_seen = 0
        self.bad_lines = 0
        self.gaps = 0
        self.last_time_ms = None
        self.intervals = deque(maxlen=200)

    def connect(self, port):
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                return
            self.state = "connecting"
            self.port = port
            self.error = None
            self._reset_counters()
            self.stop_event = threading.Event()
            self.thread = threading.Thread(target=self._run, args=(port, self.stop_event), daemon=True)
            self.thread.start()

    def disconnect(self):
        with self.lock:
            thread, stop = self.thread, self.stop_event
        if thread is None:
            return
        stop.set()
        thread.join(timeout=5)

    def start_recording(self, port, label, duration_s):
        with self.lock:
            if self.recording is not None:
                raise ValueError("Already recording.")
            active = self.thread is not None and self.thread.is_alive()
            target = self.port if active else port
            if not target:
                raise ValueError("Choose a serial port first.")

            # simulated runs are named differently so they can't pass for real data
            prefix = "flexiforce_sim_" if target == SIMULATED_PORT else "flexiforce_"
            clean_label = re.sub(r"[^\w-]+", "_", label or "").strip("_")[:40]
            name = prefix + datetime.now().strftime("%Y%m%d_%H%M%S")
            if clean_label:
                name += "_" + clean_label
            self.recording = {
                "name": name + ".csv",
                "file": None,   # opened at the first sample, so a failed start leaves no empty file
                "writer": None,
                "rows": 0,
                "t0": None,
                "last_t": 0,
                "duration_s": duration_s,
                # frozen at the start, so recalibrating mid-recording can't change the file partway
                "conversion": self.calibration.conversion() if self.calibration else None,
            }
            self.last_saved = None
            self.connect(target)

    def stop_recording(self):
        with self.lock:
            rec = self.recording
            self.recording = None
            if rec is None:
                return
            if rec["file"] is not None:
                rec["file"].close()
                self.last_saved = {
                    "file": rec["name"],
                    "rows": rec["rows"],
                    "elapsed_s": (rec["last_t"] - rec["t0"]) / 1000.0,
                }

    def add_marker(self, label=None):
        with self.lock:
            if self.state != "live":
                raise ValueError("Markers need a live connection.")
            if label is None:
                self.marker_count += 1
                label = f"Mark {self.marker_count}"
            self.pending_markers.append(label)

    def add_client(self):
        client = Client()
        with self.lock:
            self.clients.add(client)
        return client

    def remove_client(self, client):
        with self.lock:
            self.clients.discard(client)

    def status(self):
        with self.lock:
            rate = None
            if len(self.intervals) >= 20:
                mean_ms = sum(self.intervals) / len(self.intervals)
                rate = 1000.0 / mean_ms if mean_ms > 0 else None
            rec = None
            if self.recording is not None:
                t0 = self.recording["t0"]
                rec = {
                    "file": self.recording["name"],
                    "rows": self.recording["rows"],
                    "elapsed_s": 0 if t0 is None else (self.recording["last_t"] - t0) / 1000.0,
                    "duration_s": self.recording["duration_s"],
                }
            return {
                "state": self.state,
                "port": self.port,
                "simulated": self.port == SIMULATED_PORT,
                "error": self.error,
                "rate_hz": rate,
                "samples": self.samples_seen,
                "gaps": self.gaps,
                "bad_lines": self.bad_lines,
                "recording": rec,
                "last_saved": self.last_saved,
            }

    def list_recordings(self):
        with self.lock:
            active = self.recording["name"] if self.recording else None
        recordings = []
        for entry in os.scandir(self.data_dir):
            if not RECORDING_NAME.match(entry.name) or not entry.is_file():
                continue
            info = entry.stat()
            recordings.append({
                "name": entry.name,
                "size": info.st_size,
                "modified": info.st_mtime,
                "duration_s": last_time_s(entry.path),
                "active": entry.name == active,
                "replayable": not entry.name.endswith("_summary.csv"),
            })
        recordings.sort(key=lambda r: r["modified"], reverse=True)
        return recordings

    def _run(self, port, stop):
        ser = None
        try:
            ser = SimulatedArduino() if port == SIMULATED_PORT else serial.Serial(port, SENSOR_BAUD, timeout=0.2)
            if port != SIMULATED_PORT:
                stop.wait(RESET_WAIT_S)
            ser.reset_input_buffer()
            ser.write(b's')
            self._read_loop(ser, stop)
        except (serial.SerialException, OSError) as e:
            if ser is None:
                self._set_error(friendly_open_error(port, e))
            else:
                self._set_error(f"Lost the connection to {port}. Anything recorded so far is saved.")
        except TimeoutError as e:
            self._set_error(str(e))
        finally:
            if ser is not None:
                try:
                    ser.write(b'x')
                    ser.close()
                except (serial.SerialException, OSError):
                    pass  # port already gone, nothing left to stop
            self.stop_recording()
            with self.lock:
                self.state = "idle"
                self.pending_markers = []

    def _set_error(self, message):
        with self.lock:
            self.error = message

    def _read_loop(self, ser, stop):
        header = None
        last_data = time.monotonic()
        while not stop.is_set():
            raw_line = ser.readline().decode("utf-8", errors="replace").strip()

            if time.monotonic() - last_data > NO_DATA_TIMEOUT_S:
                raise TimeoutError(f"No data from {self.port} for {NO_DATA_TIMEOUT_S:.0f} s. Check that "
                                   "flexiforce_logger.ino is uploaded and this is the Mega's port.")
            if not raw_line or raw_line.startswith("#"):
                continue

            fields = raw_line.split(",")

            if fields[0] == "time_ms":
                header = fields
                continue

            if header is None or len(fields) != len(header):
                self._count_bad_line(header)
                continue  # malformed/partial line, skip it rather than corrupt the file

            try:
                values = [float(x) for x in fields]
            except ValueError:
                self._count_bad_line(header)
                continue
            if not all(math.isfinite(v) for v in values):
                self._count_bad_line(header)
                continue

            last_data = time.monotonic()
            self._on_sample(header, fields, values)

    def _count_bad_line(self, header):
        if header is not None:  # lines before the header are just the tail of old output
            with self.lock:
                self.bad_lines += 1

    def _on_sample(self, header, fields, values):
        with self.lock:
            self.state = "live"
            time_ms = values[0]
            self.samples_seen += 1
            if self.last_time_ms is not None:
                interval = time_ms - self.last_time_ms
                if self.intervals and interval > 2.5 * statistics.median(self.intervals):
                    self.gaps += 1
                self.intervals.append(interval)
            self.last_time_ms = time_ms

            marker = "; ".join(self.pending_markers)
            self.pending_markers = []
            if self.recording is not None:
                self._write_row(header, fields, values, time_ms, marker)

            for client in self.clients:
                client.samples.append(values)
                if marker:
                    client.markers.append({"t": time_ms, "label": marker})

    def _write_row(self, header, fields, values, time_ms, marker):
        rec = self.recording
        try:
            if rec["file"] is None:
                self._open_recording(rec, header)
                rec["t0"] = time_ms

            converted = []
            if rec["sensor_columns"]:
                volts = [values[i] for i in rec["sensor_columns"]]
                converted = rec["conversion"].values(rec["channels"], volts)

            # each recording starts at t=0, even if the live view was already running
            rec["writer"].writerow([round(time_ms - rec["t0"])] + fields[1:] + converted + [marker or ""])
            rec["rows"] += 1
            rec["last_t"] = time_ms
            if rec["rows"] % FLUSH_EVERY_ROWS == 0:
                rec["file"].flush()
        except OSError as e:
            self.error = f"Couldn't write {rec['name']}, so recording stopped: {e}"
            self.stop_recording()
            return

        if rec["duration_s"] is not None and (time_ms - rec["t0"]) >= rec["duration_s"] * 1000:
            self.stop_recording()

    def _open_recording(self, rec, header):
        """Volts come first, as recorded, then newtons and MPa for any calibrated sensors."""
        rec["channels"], rec["sensor_columns"] = [], []
        for i, name in enumerate(header):
            match = re.fullmatch(r"S(\d+)_V", name)
            if match:
                rec["channels"].append(int(match.group(1)))
                rec["sensor_columns"].append(i)

        conversion = rec["conversion"]
        if conversion is None or not conversion.lines:
            rec["sensor_columns"] = []  # nothing calibrated, so the file is volts only
        extra = conversion.columns(rec["channels"]) if rec["sensor_columns"] else []

        rec["file"] = open(os.path.join(self.data_dir, rec["name"]), "x", newline="")
        rec["writer"] = csv.writer(rec["file"])
        rec["writer"].writerow(header + extra + ["marker"])

        if extra:
            # a record of exactly which calibration produced the N and MPa columns
            sidecar = os.path.join(self.data_dir, rec["name"][:-len(".csv")] + "_calibration.json")
            with open(sidecar, "w", encoding="utf-8") as f:
                json.dump(conversion.describe(), f, indent=2)


def last_time_s(path):
    """Length of a recording, read from the last line so listing stays fast."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 256))
            last_line = f.read().decode("utf-8", errors="replace").strip().splitlines()[-1]
        return float(last_line.split(",")[0]) / 1000.0
    except (OSError, ValueError, IndexError):
        return None


class Handler(BaseHTTPRequestHandler):
    # set in main()
    acquisition = None
    actuator = None
    gripper_test = None
    calibration = None

    def _full_status(self):
        status = self.acquisition.status()
        status["actuator"] = self.actuator.status()
        status["test"] = self.gripper_test.status()
        return status

    def log_message(self, format, *args):
        pass  # the 20 Hz stream would bury anything useful in the terminal

    def _allowed_host(self):
        # blocks other websites from reaching this server through the browser
        host = self.headers.get("Host", "").rsplit(":", 1)[0]
        return host in ("localhost", "127.0.0.1", "[::1]")

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._allowed_host():
            self.send_error(403)
            return

        url = urlsplit(self.path)
        if url.path in STATIC_FILES:
            self._send_static(*STATIC_FILES[url.path])
        elif url.path == "/api/ports":
            self._json({"ports": list_serial_ports(), "simulated_port": SIMULATED_PORT,
                        "simulated_actuator_port": SIMULATED_ACTUATOR_PORT})
        elif url.path == "/api/status":
            self._json(self._full_status())
        elif url.path == "/api/calibration":
            self._json(self.calibration.snapshot())
        elif url.path == "/api/test/result":
            self._json(self.gripper_test.result or {})
        elif url.path == "/api/recordings":
            self._json({"recordings": self.acquisition.list_recordings(),
                        "data_dir": self.acquisition.data_dir})
        elif url.path.startswith("/api/recordings/"):
            self._send_recording(unquote(url.path[len("/api/recordings/"):]), parse_qs(url.query))
        elif url.path == "/api/stream":
            self._stream()
        else:
            self.send_error(404)

    def do_POST(self):
        origin = self.headers.get("Origin")
        if not self._allowed_host() or (origin and urlsplit(origin).netloc != self.headers.get("Host")):
            self.send_error(403)
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(min(length, 65536)) or b"{}")
        except ValueError:
            self._json({"error": "Bad request."}, 400)
            return

        acq = self.acquisition
        test = self.gripper_test
        link = self.actuator
        try:
            if test.running and self.path in BLOCKED_DURING_TEST:
                raise ValueError("A gripper test is running. Stop the test first.")

            if self.path == "/api/connect":
                if not body.get("port"):
                    raise ValueError("Choose a serial port first.")
                acq.connect(body["port"])
            elif self.path == "/api/disconnect":
                acq.disconnect()
            elif self.path == "/api/record/start":
                acq.start_recording(body.get("port"), body.get("label"), parse_duration(body.get("duration_s")))
            elif self.path == "/api/record/stop":
                acq.stop_recording()
            elif self.path == "/api/marker":
                acq.add_marker()
            elif self.path == "/api/ports/identify":
                self._json(self._identify(bool(body.get("likely_only"))))
                return
            elif self.path == "/api/gripper/connect":
                link.connect(body.get("port"))
            elif self.path == "/api/gripper/disconnect":
                link.disconnect()
            elif self.path == "/api/gripper/move":
                position = body.get("position")
                if position not in ("open", "closed"):
                    raise ValueError("Unknown gripper position.")
                link.move(position)
                if acq.state == "live":
                    acq.add_marker(f"Gripper {position} (website)")
            elif self.path == "/api/test/start":
                test.start(body.get("port"), body.get("actuator_port"), body.get("label"), *parse_test_settings(body))
            elif self.path == "/api/test/stop":
                test.stop()
            elif self.path == "/api/calibration/fit":
                self._json(calibration_fit(body.get("points")))
                return
            elif self.path == "/api/calibration/save":
                self.calibration.save(body.get("channel"), body.get("points"))
                self._json(self.calibration.snapshot())
                return
            elif self.path == "/api/calibration/clear":
                self.calibration.clear(body.get("channel"))
                self._json(self.calibration.snapshot())
                return
            else:
                self.send_error(404)
                return
        except ValueError as e:
            self._json({"error": str(e)}, 409)
            return
        except OSError as e:
            self._json({"error": f"Couldn't save: {e}"}, 500)
            return
        self._json(self._full_status())

    def _identify(self, likely_only):
        in_use = {}
        if self.acquisition.state != "idle" and self.acquisition.port != SIMULATED_PORT:
            in_use[self.acquisition.port] = "sensors"
        if self.actuator.state == "connected" and self.actuator.port != SIMULATED_ACTUATOR_PORT:
            in_use[self.actuator.port] = "actuator"
        return identify_arduinos(in_use, likely_only)

    def _send_static(self, filename, content_type):
        with open(os.path.join(STATIC_DIR, filename), "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_recording(self, name, query):
        # the name pattern has no path separators, so this can't leave the data folder
        path = os.path.join(self.acquisition.data_dir, name)
        if not RECORDING_NAME.match(name) or not os.path.isfile(path):
            self.send_error(404)
            return
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if "download" in query:
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.end_headers()
        self.wfile.write(body)

    def _stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        acq = self.acquisition
        client = acq.add_client()
        try:
            while True:
                with acq.lock:
                    samples = list(client.samples)
                    markers = list(client.markers)
                    client.samples.clear()
                    client.markers.clear()
                message = {"samples": samples, "markers": markers, "status": self._full_status()}
                self.wfile.write(b"data: " + json.dumps(message).encode() + b"\n\n")
                self.wfile.flush()
                time.sleep(0.05)
        except OSError:
            pass  # the browser tab closed
        finally:
            acq.remove_client(client)


BLOCKED_DURING_TEST = {
    "/api/disconnect", "/api/record/start", "/api/record/stop", "/api/ports/identify",
    "/api/gripper/connect", "/api/gripper/disconnect", "/api/gripper/move",
}


def parse_number(body, key, label, low, high):
    try:
        value = float(body.get(key))
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number.")
    if not low <= value <= high:
        raise ValueError(f"{label} must be between {low:g} and {high:g}.")
    return value


def parse_test_settings(body):
    cycles = parse_number(body, "cycles", "Trials", 1, 50)
    if cycles != int(cycles):
        raise ValueError("Trials must be a whole number.")
    closed_s = parse_number(body, "closed_s", "Closed time", 2, 600)
    open_s = parse_number(body, "open_s", "Open time", 2, 600)
    settle_s = parse_number(body, "settle_s", "Ignored time", 0, 600)
    if settle_s >= min(closed_s, open_s):
        raise ValueError("The ignored time must be shorter than the closed and open times.")
    return int(cycles), closed_s, open_s, settle_s


def parse_duration(value):
    if value in (None, ""):
        return None
    try:
        duration = float(value)
    except (TypeError, ValueError):
        raise ValueError("Auto-stop must be a number of seconds.")
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Auto-stop must be more than 0 seconds.")
    return duration


def parse_args():
    p = argparse.ArgumentParser(description="Local web dashboard for the FlexiForce logger.")
    p.add_argument("--http-port", type=int, default=8765, help="Port for the web page (default: 8765)")
    p.add_argument("--data-dir", default=os.path.dirname(os.path.abspath(__file__)),
                    help="Folder for recordings (default: this script's folder)")
    p.add_argument("--no-browser", action="store_true", help="Don't open the page automatically")
    return p.parse_args()


def main():
    args = parse_args()
    data_dir = os.path.abspath(args.data_dir)
    os.makedirs(data_dir, exist_ok=True)

    acquisition = Acquisition(data_dir)
    calibration = CalibrationStore(data_dir)
    acquisition.calibration = calibration

    def on_gripper_button(position):
        # manual runs get the button presses marked too, whenever the actuator is connected
        try:
            acquisition.add_marker(f"Gripper {position} (button)")
        except ValueError:
            pass  # nothing is streaming, so there's nothing to mark

    actuator = ActuatorLink(on_gripper_button)
    gripper_test = GripperTest(acquisition, actuator, data_dir)
    Handler.acquisition = acquisition
    Handler.actuator = actuator
    Handler.gripper_test = gripper_test
    Handler.calibration = calibration
    ThreadingHTTPServer.daemon_threads = True
    # On Windows this flag lets a second server quietly share the port with one
    # that's already running, so requests could reach either. Off, it fails instead.
    ThreadingHTTPServer.allow_reuse_address = os.name != "nt"
    try:
        # 127.0.0.1 only, so nothing else on the network can reach it
        server = ThreadingHTTPServer(("127.0.0.1", args.http_port), Handler)
    except OSError:
        print(f"Port {args.http_port} is busy. Is the dashboard already running? "
              "If not, try --http-port with another number.")
        return

    url = f"http://localhost:{args.http_port}"
    print(f"Dashboard running at {url}")
    print(f"Recordings go in {data_dir}")
    print("Press Ctrl+C to stop.")
    if not args.no_browser:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        gripper_test.stop()  # this also opens the gripper if a test was running
        gripper_test.join(timeout=5)
        acquisition.disconnect()
        actuator.disconnect()
        server.server_close()


if __name__ == "__main__":
    main()

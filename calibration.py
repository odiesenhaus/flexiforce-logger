"""
calibration.py
---------------
Per-sensor force calibration for the dashboard, following Tekscan's
procedure: record the output with no load and with 2-3 known weights, then
fit a straight line. Our sensors use the inverting op-amp circuit, which
Tekscan describes as linear in force.

Calibrations are saved to calibration.json next to the recordings.
Recalibrating a sensor moves its old calibration into the file's history
instead of deleting it.
"""

import json
import math
import os
import threading
from datetime import datetime

from gripper_test import SATURATION_V

FILE_NAME = "calibration.json"
MAX_POINTS = 50

GRAVITY = 9.80665  # m/s²
SENSING_DIAMETER_MM = 3.8  # ESS102 active area; keep in step with SENSING_DIAMETER_MM in dashboard/config.js
SENSING_AREA_MM2 = math.pi * (SENSING_DIAMETER_MM / 2) ** 2


class Conversion:
    """
    Turns volts into newtons and MPa using the calibrations as they were when a
    recording started, so a later recalibration can't change what a file says.
    Pressure is the force spread evenly over the sensing circle (N/mm² is MPa).
    """

    def __init__(self, sensors):
        self.sensors = sensors
        self.lines = {int(k): (v["slope_v_per_g"], v["intercept_v"]) for k, v in sensors.items()}

    def newtons(self, channel, volts):
        line = self.lines.get(channel)
        if line is None:
            return None
        slope, intercept = line
        return (volts - intercept) / slope / 1000 * GRAVITY

    @staticmethod
    def columns(channels):
        return [f"S{c}_N" for c in channels] + [f"S{c}_MPa" for c in channels]

    def values(self, channels, volts):
        """Formatted N then MPa values for one row, blank for sensors that aren't calibrated."""
        forces = [self.newtons(c, v) for c, v in zip(channels, volts)]
        return ([("" if f is None else f"{f:.4f}") for f in forces]
                + [("" if f is None else f"{f / SENSING_AREA_MM2:.5f}") for f in forces])

    def describe(self):
        return {
            "gravity_m_s2": GRAVITY,
            "sensing_diameter_mm": SENSING_DIAMETER_MM,
            "sensing_area_mm2": SENSING_AREA_MM2,
            "formula": "N = (V - intercept_v) / slope_v_per_g / 1000 * gravity; MPa = N / sensing_area_mm2",
            "sensors": {k: {key: v[key] for key in ("slope_v_per_g", "intercept_v", "r2", "calibrated_at")}
                        for k, v in self.sensors.items()},
        }


def clean_points(points):
    if not isinstance(points, list) or not points or len(points) > MAX_POINTS:
        raise ValueError("No calibration points to fit.")
    cleaned = []
    for p in points:
        try:
            grams, volts, std = float(p["grams"]), float(p["volts"]), float(p.get("std", 0))
        except (TypeError, ValueError, KeyError):
            raise ValueError("A calibration point is missing its weight or reading.")
        if not all(math.isfinite(v) for v in (grams, volts, std)) or not 0 <= grams <= 100000 or not 0 <= volts <= 6:
            raise ValueError("A calibration point has an impossible weight or reading.")
        cleaned.append({"grams": grams, "volts": volts, "std": max(0.0, std)})
    return cleaned


def fit(points):
    """Least-squares line through the unsaturated points: volts = slope * grams + intercept."""
    points = clean_points(points)
    for p in points:
        p["used"] = p["volts"] < SATURATION_V  # a clipped reading only says "at least this much"
    used = [p for p in points if p["used"]]
    if len({p["grams"] for p in used}) < 2:
        raise ValueError("Capture at least two different weights that don't saturate the sensor, "
                         "for example no load and 100 g.")

    n = len(used)
    mean_g = sum(p["grams"] for p in used) / n
    mean_v = sum(p["volts"] for p in used) / n
    sxx = sum((p["grams"] - mean_g) ** 2 for p in used)
    sxy = sum((p["grams"] - mean_g) * (p["volts"] - mean_v) for p in used)
    slope = sxy / sxx
    intercept = mean_v - slope * mean_g
    if slope <= 0:
        raise ValueError("The reading doesn't go up with weight. Check that the weight is on this sensor.")

    total = sum((p["volts"] - mean_v) ** 2 for p in used)
    residual = sum((p["volts"] - (slope * p["grams"] + intercept)) ** 2 for p in used)
    for p in points:
        p["fit_grams"] = (p["volts"] - intercept) / slope
        p["error_grams"] = p["fit_grams"] - p["grams"]

    return {
        "slope_v_per_g": slope,
        "intercept_v": intercept,
        "r2": 1.0 if total == 0 else 1 - residual / total,
        "saturates_at_g": (SATURATION_V - intercept) / slope,
        "points": points,
    }


class CalibrationStore:
    def __init__(self, data_dir):
        self.path = os.path.join(data_dir, FILE_NAME)
        self.lock = threading.Lock()
        self.load_error = None
        self.data = {"version": 1, "unit": "g", "sensors": {}, "history": []}
        if os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    loaded = json.load(f)
                if not isinstance(loaded.get("sensors"), dict):
                    raise ValueError("no sensors section")
                self.data.update(loaded)
            except (OSError, ValueError, AttributeError) as e:
                # refusing to save keeps a damaged file from being overwritten and lost
                self.load_error = f"{FILE_NAME} couldn't be read ({e}). Fix or move it to save calibrations."

    def conversion(self):
        with self.lock:
            return Conversion(json.loads(json.dumps(self.data["sensors"])))  # a copy, frozen for one recording

    def snapshot(self):
        with self.lock:
            return {"sensors": self.data["sensors"], "unit": self.data["unit"], "error": self.load_error,
                    "path": self.path}

    def save(self, channel, points):
        channel = self._check_channel(channel)
        entry = fit(points)
        entry["channel"] = channel
        entry["calibrated_at"] = datetime.now().isoformat(timespec="seconds")
        with self.lock:
            previous = self.data["sensors"].get(str(channel))
            if previous:
                self.data["history"].append(previous)
            self.data["sensors"][str(channel)] = entry
            self._write()
        return entry

    def clear(self, channel):
        channel = self._check_channel(channel)
        with self.lock:
            previous = self.data["sensors"].pop(str(channel), None)
            if previous:
                self.data["history"].append(previous)
                self._write()

    def _check_channel(self, channel):
        if self.load_error:
            raise ValueError(self.load_error)
        try:
            channel = int(channel)
        except (TypeError, ValueError):
            raise ValueError("Unknown sensor.")
        if not 1 <= channel <= 10:
            raise ValueError("Unknown sensor.")
        return channel

    def _write(self):
        # written to a temporary file first, so a crash mid-save can't leave half a file
        temporary = self.path + ".tmp"
        with open(temporary, "w") as f:
            json.dump(self.data, f, indent=2)
        os.replace(temporary, self.path)

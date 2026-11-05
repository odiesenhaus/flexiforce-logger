"""
live_view.py
-------------
Live scrolling plot of the Arduino Mega FlexiForce logger over serial.
Shows raw sensor voltage (0-5V) with each channel's mean and standard
deviation over the visible time window. Nothing is saved to disk -- use
collect_data.py to record a run.

Requires: pyserial, matplotlib (pip install pyserial matplotlib)

Usage:
    python live_view.py --port COM5
    python live_view.py --port COM5 --channels 1 3 5
    python live_view.py --port COM5 --channels 1 --window 30

Close the plot window to stop. Only one program can use the port at a
time, so close the Arduino IDE's Serial Monitor/Plotter first.
"""

import argparse
import threading
import time
from collections import deque

import matplotlib.pyplot as plt
import numpy as np
import serial
from matplotlib.animation import FuncAnimation


def parse_args():
    p = argparse.ArgumentParser(description="Live plot of FlexiForce sensor data from an Arduino Mega over serial.")
    p.add_argument("--port", required=True, help="Serial port, e.g. COM5 or /dev/ttyACM0")
    p.add_argument("--baud", type=int, default=115200, help="Baud rate (must match the Arduino sketch)")
    p.add_argument("--channels", type=int, nargs="+", default=None,
                    help="Which sensor numbers to show (1-10). Default: all.")
    p.add_argument("--window", type=float, default=10.0,
                    help="Seconds of data to show and to compute mean/std over (default: 10)")
    return p.parse_args()


def read_serial(ser, rows, lock, window_s, header, header_ready, stop):
    """Runs in a background thread so slow plot redraws can't back up the serial port."""
    while not stop.is_set():
        try:
            raw_line = ser.readline().decode("utf-8", errors="replace").strip()
        except serial.SerialException as e:
            print(f"Serial connection lost: {e}")
            return

        if not raw_line:
            continue

        if raw_line.startswith("#"):
            # status/comment line from the Arduino (e.g. "LOGGING START")
            print(raw_line)
            continue

        fields = raw_line.split(",")

        if fields[0] == "time_ms":
            # the Arduino re-sends the header at each start, and t=0 restarts with it
            with lock:
                header[:] = fields
                rows.clear()
            header_ready.set()
            continue

        if not header or len(fields) != len(header):
            continue  # malformed/partial line, skip it

        try:
            sample = [float(x) for x in fields]
        except ValueError:
            continue

        with lock:
            rows.append(sample)
            while rows[-1][0] - rows[0][0] > window_s * 1000:
                rows.popleft()


def build_plot(names, window_s):
    # capped so all 10 channels fit on a laptop screen
    fig, axes = plt.subplots(len(names), 1, sharex=True, figsize=(10, min(8, max(3, 1.8 * len(names)))))
    if len(names) == 1:
        axes = [axes]

    views = []
    for ax, name in zip(axes, names):
        line, = ax.plot([], [])
        color = line.get_color()
        views.append({
            "line": line,
            "mean": ax.axhline(0, color=color, linestyle="--", linewidth=1, visible=False),
            "upper": ax.axhline(0, color=color, linestyle=":", linewidth=1, visible=False),
            "lower": ax.axhline(0, color=color, linestyle=":", linewidth=1, visible=False),
            "text": ax.text(0.99, 0.92, "", transform=ax.transAxes, ha="right", va="top"),
        })
        ax.set_ylabel(name)
        ax.set_ylim(0, 5)
    axes[-1].set_xlabel("Time (s)")
    axes[-1].set_xlim(0, window_s)
    fig.suptitle(f"FlexiForce live voltage -- mean and std over last {window_s:g} s -- close window to stop")
    fig.tight_layout(rect=(0, 0, 1, 0.94))  # leave room for the title
    return fig, axes, views


def main():
    args = parse_args()

    print(f"Opening {args.port} at {args.baud} baud...")
    ser = serial.Serial(args.port, args.baud, timeout=1)
    time.sleep(2)  # let the Mega finish its reset after the port opens

    ser.reset_input_buffer()

    rows = deque()
    lock = threading.Lock()
    header = []
    header_ready = threading.Event()
    stop = threading.Event()
    reader = threading.Thread(target=read_serial, daemon=True,
                              args=(ser, rows, lock, args.window, header, header_ready, stop))
    reader.start()

    print("Sending start command...")
    ser.write(b's')

    try:
        if not header_ready.wait(timeout=5):
            print("No data header from the Arduino. Check the port and that the sketch is uploaded.")
            return

        with lock:
            all_names = [c for c in header if c.startswith("S") and c.endswith("_V")]
            if args.channels:
                wanted = [f"S{n}_V" for n in args.channels]
                names = [c for c in wanted if c in all_names]
                missing = set(wanted) - set(names)
                if missing:
                    print(f"Warning: requested channels not found: {sorted(missing)}")
            else:
                names = all_names
            columns = [header.index(c) for c in names]

        if not names:
            print("No matching sensor channels to show.")
            return

        fig, axes, views = build_plot(names, args.window)

        def update(_frame):
            with lock:
                snapshot = list(rows)
            if len(snapshot) < 2:
                return

            data = np.array(snapshot)
            time_s = data[:, 0] / 1000.0
            for view, col in zip(views, columns):
                values = data[:, col]
                mean, std = values.mean(), values.std(ddof=1)
                view["line"].set_data(time_s, values)
                view["mean"].set_ydata([mean, mean])
                view["upper"].set_ydata([mean + std, mean + std])
                view["lower"].set_ydata([mean - std, mean - std])
                for key in ("mean", "upper", "lower"):
                    view[key].set_visible(True)
                view["text"].set_text(f"mean {mean:.3f} V, std {std:.3f} V")

            right = max(time_s[-1], args.window)
            axes[-1].set_xlim(right - args.window, right)

        # keep a reference, or the animation gets garbage collected and stops
        anim = FuncAnimation(fig, update, interval=100, cache_frame_data=False)
        plt.show()

    except KeyboardInterrupt:
        print("\nStopped by user (Ctrl+C).")

    finally:
        stop.set()
        reader.join(timeout=2)
        print("Sending stop command...")
        try:
            ser.write(b'x')
            time.sleep(0.2)
        except serial.SerialException:
            pass  # port already gone, nothing left to stop
        ser.close()


if __name__ == "__main__":
    main()

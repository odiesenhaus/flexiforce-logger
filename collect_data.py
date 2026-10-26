"""
collect_data.py
----------------
Connects to the Arduino Mega FlexiForce logger over serial, starts a
logging run, and writes the incoming CSV stream to a timestamped file
on disk. This script only records raw sensor voltages -- calibration
(volts -> force) is a separate step, done later on the saved CSV.

Requires: pyserial (pip install pyserial)

Usage:
    python collect_data.py --port COM5
    python collect_data.py --port /dev/ttyACM0 --outfile run1.csv
    python collect_data.py --port COM5 --duration 60
    python collect_data.py --port COM5 --duration 60 --plot
    python collect_data.py --port COM5 --channels 1 3 --overlay

Press Ctrl+C to stop logging early -- the script will still close out
the file cleanly and tell the Arduino to stop.
"""

import argparse
import csv
import time
from datetime import datetime

import serial


def parse_args():
    p = argparse.ArgumentParser(description="Log FlexiForce sensor data from an Arduino Mega over serial.")
    p.add_argument("--port", required=True, help="Serial port, e.g. COM5 or /dev/ttyACM0")
    p.add_argument("--baud", type=int, default=115200, help="Baud rate (must match the Arduino sketch)")
    p.add_argument("--outfile", default=None, help="Output CSV path. Default: flexiforce_<timestamp>.csv")
    p.add_argument("--duration", type=float, default=None,
                    help="Stop automatically after this many seconds (default: run until Ctrl+C)")
    p.add_argument("--plot", action="store_true",
                    help="Plot all channels with plot_data.py when logging finishes")
    p.add_argument("--channels", type=int, nargs="+", default=None,
                    help="Plot only these sensor numbers (1-10) when logging finishes. Implies --plot.")
    p.add_argument("--overlay", action="store_true",
                    help="Plot channels on one set of axes. Implies --plot.")
    return p.parse_args()


def main():
    args = parse_args()
    outfile = args.outfile or f"flexiforce_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    print(f"Opening {args.port} at {args.baud} baud...")
    ser = serial.Serial(args.port, args.baud, timeout=1)
    time.sleep(2)  # let the Mega finish its reset after the port opens

    ser.reset_input_buffer()

    print("Sending start command...")
    ser.write(b's')

    header = None
    rows_written = 0
    start_time = time.time()

    try:
        with open(outfile, "w", newline="") as f:
            writer = None
            while True:
                if args.duration is not None and (time.time() - start_time) > args.duration:
                    print(f"\nReached duration limit ({args.duration}s), stopping.")
                    break

                raw_line = ser.readline().decode("utf-8", errors="replace").strip()
                if not raw_line:
                    continue  # read timed out with nothing new, keep waiting

                if raw_line.startswith("#"):
                    # status/comment line from the Arduino (e.g. "LOGGING START")
                    print(raw_line)
                    continue

                fields = raw_line.split(",")

                if header is None:
                    # first non-comment line should be the CSV header from the Arduino
                    header = fields
                    writer = csv.writer(f)
                    writer.writerow(header)
                    print(f"Logging columns: {header}")
                    continue

                if len(fields) != len(header):
                    # malformed/partial line (e.g. torn by a reset), skip rather than corrupt the file
                    continue

                writer.writerow(fields)
                rows_written += 1

                if rows_written % 100 == 0:
                    print(f"  {rows_written} samples logged...", end="\r")

    except KeyboardInterrupt:
        print("\nStopped by user (Ctrl+C).")

    finally:
        print("Sending stop command...")
        ser.write(b'x')
        time.sleep(0.2)
        ser.close()
        print(f"Saved {rows_written} samples to {outfile}")

    if args.plot or args.channels or args.overlay:
        if rows_written == 0:
            print("No samples logged, skipping plot.")
            return
        # imported here so logging still works without pandas/matplotlib installed
        from plot_data import plot_csv
        plot_csv(outfile, args.channels, args.overlay)


if __name__ == "__main__":
    main()

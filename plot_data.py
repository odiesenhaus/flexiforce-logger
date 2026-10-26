"""
plot_data.py
-------------
Quick-look plot of a FlexiForce data CSV recorded by collect_data.py.
Plots raw sensor voltage (0-5V) vs time for each channel. This is a
sanity-check tool, not a calibrated force plot -- convert volts to
force in a separate calibration step before doing real analysis.

Requires: pandas, matplotlib (pip install pandas matplotlib)

Usage:
    python plot_data.py run1.csv
    python plot_data.py run1.csv --channels 1 3 5
    python plot_data.py run1.csv --overlay
"""

import argparse
import pandas as pd
import matplotlib.pyplot as plt


def parse_args():
    p = argparse.ArgumentParser(description="Plot raw FlexiForce voltage data from a logged CSV.")
    p.add_argument("csv_file", help="Path to CSV file written by collect_data.py")
    p.add_argument("--channels", type=int, nargs="+", default=None,
                    help="Which sensor numbers to plot (1-10). Default: all.")
    p.add_argument("--overlay", action="store_true",
                    help="Plot all channels on one set of axes instead of stacked subplots")
    return p.parse_args()


def plot_csv(csv_file, channels=None, overlay=False):
    """Plot a logged CSV. Also called by collect_data.py after a run."""
    df = pd.read_csv(csv_file)

    time_s = df["time_ms"] / 1000.0

    all_channels = [c for c in df.columns if c.startswith("S") and c.endswith("_V")]
    if channels:
        wanted = [f"S{n}_V" for n in channels]
        channels = [c for c in wanted if c in all_channels]
        missing = set(wanted) - set(channels)
        if missing:
            print(f"Warning: requested channels not found in file: {sorted(missing)}")
    else:
        channels = all_channels

    if not channels:
        print("No matching sensor columns found in this file.")
        return

    if overlay:
        plt.figure(figsize=(10, 5))
        for col in channels:
            plt.plot(time_s, df[col], label=col)
        plt.xlabel("Time (s)")
        plt.ylabel("Voltage (V)")
        plt.title(f"FlexiForce raw voltage -- {csv_file}")
        plt.legend()
        plt.tight_layout()
    else:
        fig, axes = plt.subplots(len(channels), 1, sharex=True, figsize=(10, 1.8 * len(channels)))
        if len(channels) == 1:
            axes = [axes]
        for ax, col in zip(axes, channels):
            ax.plot(time_s, df[col])
            ax.set_ylabel(col)
            ax.set_ylim(0, 5)
        axes[-1].set_xlabel("Time (s)")
        fig.suptitle(f"FlexiForce raw voltage -- {csv_file}")
        plt.tight_layout()

    plt.show()


def main():
    args = parse_args()
    plot_csv(args.csv_file, args.channels, args.overlay)


if __name__ == "__main__":
    main()

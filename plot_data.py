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
    python plot_data.py run1.csv --channels 1 3 --stats
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
    p.add_argument("--stats", action="store_true",
                    help="Print each channel's mean and standard deviation, and show them on the plot")
    return p.parse_args()


def print_stats(df, channels):
    print(f"\nStats over {len(df)} samples:")
    print(f"{'Channel':<10}{'Mean (V)':>10}{'Std (V)':>10}")
    for col in channels:
        print(f"{col:<10}{df[col].mean():>10.4f}{df[col].std():>10.4f}")


def draw_stats(ax, time_s, values, color):
    """Dashed line at the mean, shaded band at +/- 1 standard deviation."""
    mean, std = values.mean(), values.std()
    ax.axhline(mean, color=color, linestyle="--", linewidth=1)
    ax.fill_between(time_s, mean - std, mean + std, color=color, alpha=0.2)


def plot_csv(csv_file, channels=None, overlay=False, stats=False):
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

    if stats:
        print_stats(df, channels)

    if overlay:
        plt.figure(figsize=(10, 5))
        for col in channels:
            label = f"{col}: {df[col].mean():.3f} ± {df[col].std():.3f} V" if stats else col
            line, = plt.plot(time_s, df[col], label=label)
            if stats:
                draw_stats(plt.gca(), time_s, df[col], line.get_color())
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
            line, = ax.plot(time_s, df[col])
            ax.set_ylabel(col)
            ax.set_ylim(0, 5)
            if stats:
                draw_stats(ax, time_s, df[col], line.get_color())
                ax.text(0.99, 0.92, f"mean {df[col].mean():.3f} V, std {df[col].std():.3f} V",
                        transform=ax.transAxes, ha="right", va="top")
        axes[-1].set_xlabel("Time (s)")
        fig.suptitle(f"FlexiForce raw voltage -- {csv_file}")
        plt.tight_layout()

    plt.show()


def main():
    args = parse_args()
    plot_csv(args.csv_file, args.channels, args.overlay, args.stats)


if __name__ == "__main__":
    main()

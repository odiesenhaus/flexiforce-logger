"""
compare_tests.py
-----------------
Compares several gripper tests (for example, different finger designs) and
makes report-ready figures of how the grip force is distributed over the
10 sensors.

Point it at a folder with one subfolder per test, each holding the files a
dashboard gripper test saves (the *_trials.csv file is the one used):

    python compare_tests.py "Test 2 - Recalibrated Sensors"
    python compare_tests.py "Test 2 - Recalibrated Sensors" --calibration calibration.json

By default the newtons saved in each test's files are used. With
--calibration, they're recalculated from the recorded volts instead, which
fixes tests that were recorded with an out-of-date calibration. Either way,
the script warns when a test's calibration differs from calibration.json.

It writes into <folder>/figures/:
    total_grip_force.png/.pdf       total force per test, split by side
    force_profiles.png/.pdf         force along each side's arc, one panel per test
    force_maps.png/.pdf             heat grids of all tests side by side
    force_distribution_summary.csv  the numbers behind the figures

How the numbers are made: in each trial, a sensor's net force is its mean
over the held part of the closed phase minus its mean over the held part of
the open phase that follows, which removes each sensor's resting offset. The
figures show the mean of the trials, with the standard deviation between
trials. A reading that hit the sensor's limit (4.98 V or more) is a lower
bound, and is marked as one.

Requires: pandas, matplotlib
"""

import argparse
import glob
import json
import math
import os
import re
import statistics

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.patches import Rectangle

from calibration import GRAVITY, SENSING_AREA_MM2

SATURATION_V = 4.98
HERE = os.path.dirname(os.path.abspath(__file__))

# matches dashboard/config.js: channels top to bottom, at these distances along the arc (inches, + is up)
ARC_POSITIONS_IN = [1.0, 0.5, 0.0, -0.5, -1.0]
SIDES = {"Side A": [1, 2, 3, 4, 5], "Side B": [6, 7, 8, 9, 10]}

# the dashboard's pressure-map colors, from no load to the top of the scale
HEAT_STOPS = ["#1b316c", "#347d89", "#50ae87", "#78cf78", "#b3e762", "#e5e64b", "#e4b747", "#e18950", "#dc263c"]
SIDE_COLORS = {"Side A": "#2a78d6", "Side B": "#eb6834"}
INK, INK_2, GRID = "#0b0b0b", "#52514e", "#e1e0d9"

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Segoe UI", "Arial", "DejaVu Sans"],
    "font.size": 9,
    "axes.titlesize": 9.5,
    "axes.labelsize": 9,
    "axes.edgecolor": "#c3c2b7",
    "axes.labelcolor": INK,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "xtick.color": INK_2,
    "ytick.color": INK_2,
    "legend.frameon": False,
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
})


def parse_args():
    p = argparse.ArgumentParser(description="Compare grip force distributions across several gripper tests.")
    p.add_argument("folder", help="Folder with one subfolder per test")
    p.add_argument("--calibration", default=None,
                   help="Recalculate newtons from the volts with this calibration.json instead of using the saved ones")
    return p.parse_args()


def load_sensors(path):
    with open(path) as f:
        return json.load(f)["sensors"]


def check_calibration(tests, reference_path):
    """Warns about tests whose saved newtons came from a different calibration than reference_path."""
    if not os.path.exists(reference_path):
        return
    reference = load_sensors(reference_path)
    for name, trials_path in tests:
        records = glob.glob(trials_path.replace("_trials.csv", "_calibration.json"))
        if not records:
            continue
        used = json.load(open(records[0]))["sensors"]
        stale = [f"S{ch}" for ch in sorted(reference, key=int)
                 if ch not in used or abs(used[ch]["slope_v_per_g"] - reference[ch]["slope_v_per_g"]) > 1e-12
                 or abs(used[ch]["intercept_v"] - reference[ch]["intercept_v"]) > 1e-12]
        if stale:
            print(f"Note: {name} was recorded with a different calibration for {', '.join(stale)} "
                  f"than {os.path.basename(reference_path)}.")


def test_name(folder_name):
    match = re.match(r"Fin\s*(\d+)", folder_name, re.IGNORECASE)
    return f"Fin {match.group(1)}" if match else folder_name


def find_tests(root):
    tests = []
    for entry in sorted(os.scandir(root), key=lambda e: [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", e.name)]):
        if not entry.is_dir() or entry.name == "figures":
            continue
        trials = [f for f in os.listdir(entry.path) if f.endswith("_trials.csv")]
        if len(trials) == 1:
            tests.append((test_name(entry.name), os.path.join(entry.path, trials[0])))
        elif trials:
            print(f"Skipping {entry.name}: more than one *_trials.csv in it")
    return tests


def net_forces(trials_path, sensors=None):
    """
    Per trial and sensor: net force (closed minus the following open phase) and whether it saturated.
    With `sensors` (a calibration), the newtons are recalculated from the volts first.
    """
    df = pd.read_csv(trials_path)
    if sensors:
        for ch, cal in sensors.items():
            if f"S{ch}_V" in df.columns:
                df[f"S{ch}_N"] = (df[f"S{ch}_V"] - cal["intercept_v"]) / cal["slope_v_per_g"] / 1000 * GRAVITY
    held = df[df.in_stats == 1]
    channels = sorted(int(m.group(1)) for c in df.columns if (m := re.fullmatch(r"S(\d+)_N", c)))
    if not channels:
        raise SystemExit(f"{trials_path} has no newton columns. Run with --calibration calibration.json.")

    rows = []
    for trial, group in held.groupby("trial"):
        closed = group[group.phase == "closed"]
        opened = group[group.phase == "open"]
        for ch in channels:
            rows.append({
                "trial": trial,
                "channel": ch,
                "net_N": closed[f"S{ch}_N"].mean() - opened[f"S{ch}_N"].mean(),
                "saturated": bool((closed[f"S{ch}_V"] >= SATURATION_V).any()),
            })
    return pd.DataFrame(rows)


def side_of(channel):
    for side, channels in SIDES.items():
        if channel in channels:
            return side, ARC_POSITIONS_IN[channels.index(channel)]
    return None, None


def summarize(tests, sensors=None):
    per_sensor, per_test = [], []
    for name, path in tests:
        net = net_forces(path, sensors)
        for ch, group in net.groupby("channel"):
            side, arc = side_of(ch)
            values = group.net_N.tolist()
            per_sensor.append({
                "test": name, "sensor": f"S{ch}", "channel": ch, "side": side, "arc_in": arc,
                "net_mean_N": statistics.fmean(values),
                "net_sd_N": statistics.stdev(values) if len(values) > 1 else 0.0,
                "net_mean_MPa": statistics.fmean(values) / SENSING_AREA_MM2,
                "trials": len(values),
                "saturated_trials": int(group.saturated.sum()),
            })

        # totals use only positive net force, so a sensor's noise can't cancel real load elsewhere
        totals, side_a = [], []
        for trial, group in net.groupby("trial"):
            load = {ch: max(0.0, f) for ch, f in zip(group.channel, group.net_N)}
            total = sum(load.values())
            totals.append(total)
            side_a.append(sum(load[c] for c in SIDES["Side A"] if c in load))
        load_by_ch = {r["channel"]: max(0.0, r["net_mean_N"]) for r in per_sensor if r["test"] == name}
        centers = {}
        for side, channels in SIDES.items():
            weight = sum(load_by_ch.get(c, 0) for c in channels)
            centers[side] = (sum(load_by_ch.get(c, 0) * ARC_POSITIONS_IN[i] for i, c in enumerate(channels)) / weight
                             if weight > 0 else float("nan"))
        per_test.append({
            "test": name,
            "total_mean_N": statistics.fmean(totals),
            "total_sd_N": statistics.stdev(totals) if len(totals) > 1 else 0.0,
            "trial_totals_N": totals,
            "side_a_mean_N": statistics.fmean(side_a),
            "side_b_mean_N": statistics.fmean(totals) - statistics.fmean(side_a),
            "side_a_pct": 100 * statistics.fmean(side_a) / statistics.fmean(totals) if statistics.fmean(totals) else float("nan"),
            "center_a_in": centers["Side A"],
            "center_b_in": centers["Side B"],
            "loaded_sensors": sum(1 for r in per_sensor if r["test"] == name and r["net_mean_N"] > 0.1),
            "any_saturated": any(r["saturated_trials"] for r in per_sensor if r["test"] == name),
        })
    return pd.DataFrame(per_sensor), pd.DataFrame(per_test)


def save(fig, out_dir, stem):
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(out_dir, f"{stem}.{ext}"))
    plt.close(fig)


def plot_totals(per_test, out_dir):
    fig, ax = plt.subplots(figsize=(6.5, 3.2))
    x = range(len(per_test))
    width = 0.55
    ax.bar(x, per_test.side_a_mean_N, width, color=SIDE_COLORS["Side A"], label="Side A", zorder=2)
    # the white edge leaves a 2 px gap between the stacked segments
    ax.bar(x, per_test.side_b_mean_N, width, bottom=per_test.side_a_mean_N, color=SIDE_COLORS["Side B"],
           label="Side B", edgecolor="white", linewidth=1.5, zorder=2)
    ax.errorbar(x, per_test.total_mean_N, yerr=per_test.total_sd_N, fmt="none", ecolor=INK, elinewidth=1,
                capsize=3, zorder=3)
    for i, row in per_test.iterrows():
        trial_x = [i + 0.33 + 0.05 * k for k in range(len(row.trial_totals_N))]
        ax.scatter(trial_x, row.trial_totals_N, s=9, color=INK_2, zorder=3, linewidths=0,
                   label="Individual trials" if i == 0 else None)
        label = f"{'≥' if row.any_saturated else ''}{row.total_mean_N:.2f}"
        ax.text(i, row.total_mean_N + row.total_sd_N + 0.12, label, ha="center", va="bottom", fontsize=8, color=INK)
    ax.set_xticks(list(x), per_test.test)
    ax.set_ylabel("Total grip force (N)")
    ax.yaxis.grid(True, color=GRID, linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)
    ax.set_ylim(0, (per_test.total_mean_N + per_test.total_sd_N).max() * 1.22)
    ax.legend(ncol=3, loc="upper left", fontsize=8)
    ax.set_title("Total grip force by finger set (mean ± SD of 4 trials)", loc="left")
    fig.text(0, -0.04, "≥ marks a total that includes a saturated sensor, so the true force is at least that much.",
             fontsize=7.5, color=INK_2)
    save(fig, out_dir, "total_grip_force")


def plot_profiles(per_sensor, per_test, out_dir):
    tests = list(per_test.test)
    cols = 4
    rows = math.ceil(len(tests) / cols)
    fig, axes = plt.subplots(rows, cols, figsize=(6.5, 2.1 * rows + 0.4), sharex=True, sharey=True)
    axes = axes.flatten()
    top = (per_sensor.net_mean_N + per_sensor.net_sd_N).max() * 1.1
    for ax, name in zip(axes, tests):
        data = per_sensor[per_sensor.test == name]
        for side, color in SIDE_COLORS.items():
            s = data[data.side == side].sort_values("arc_in")
            ax.errorbar(s.arc_in, s.net_mean_N, yerr=s.net_sd_N, color=color, marker="o", markersize=3.5,
                        linewidth=1.5, elinewidth=0.8, capsize=2, label=side)
            sat = s[s.saturated_trials > 0]
            # a hollow ring on readings that saturated: the real force there is higher
            ax.scatter(sat.arc_in, sat.net_mean_N, s=60, facecolors="none", edgecolors=INK, linewidths=1, zorder=4)
        ax.axhline(0, color="#c3c2b7", linewidth=0.6)
        ax.yaxis.grid(True, color=GRID, linewidth=0.6)
        ax.set_axisbelow(True)
        ax.set_title(name, loc="left")
        ax.set_ylim(-0.25, top)
        ax.set_xticks([1, 0.5, 0, -0.5, -1], ["+1", "", "0", "", "−1"])
        ax.invert_xaxis()  # top of the gripper on the left
    for ax in axes[len(tests):]:
        ax.axis("off")
    if len(tests) < len(axes):
        handles, labels = axes[0].get_legend_handles_labels()
        ring = plt.Line2D([], [], marker="o", linestyle="none", markerfacecolor="none", markeredgecolor=INK,
                          markersize=7, label="Saturated (lower bound)")
        axes[len(tests)].legend(handles + [ring], labels + ["Saturated (lower bound)"], loc="center", fontsize=8)
    for ax in axes[::cols]:
        ax.set_ylabel("Net force (N)")
    for ax in axes[-cols:]:
        ax.set_xlabel("Arc position (in, + is up)")
    fig.suptitle("Force along each side of the cylinder (mean ± SD of 4 trials)", x=0.01, ha="left", fontsize=10)
    fig.tight_layout()
    save(fig, out_dir, "force_profiles")


def plot_maps(per_sensor, per_test, out_dir):
    tests = list(per_test.test)
    cmap = LinearSegmentedColormap.from_list("pressure_map", HEAT_STOPS)
    vmax = max(0.5, math.ceil(per_sensor.net_mean_N.max() * 2) / 2)
    fig, axes = plt.subplots(1, len(tests), figsize=(6.8, 2.9))
    fig.subplots_adjust(top=0.84, wspace=0.12)
    for ax, name in zip(axes, tests):
        data = per_sensor[per_sensor.test == name].set_index("channel")
        for col, (side, channels) in enumerate(SIDES.items()):
            for row, ch in enumerate(channels):
                r = data.loc[ch]
                value = max(0.0, r.net_mean_N)
                color = cmap(value / vmax)
                # 2 px of white between cells, like the dashboard grid
                ax.add_patch(Rectangle((col, 4 - row), 1, 1, facecolor=color, edgecolor="white", linewidth=1.5))
                luminance = 0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]
                text = f"{'≥' if r.saturated_trials else ''}{value:.1f}"
                ax.text(col + 0.5, 4 - row + 0.5, text, ha="center", va="center", fontsize=7,
                        color=INK if luminance > 0.45 else "white")
        ax.set_xlim(0, 2)
        ax.set_ylim(0, 5)
        ax.set_aspect("equal")
        ax.set_xticks([0.5, 1.5], ["A", "B"])
        ax.tick_params(length=0)
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.set_yticks([4.5, 3.5, 2.5, 1.5, 0.5] if ax is axes[0] else [],
                      ["+1.0", "+0.5", "0", "−0.5", "−1.0"] if ax is axes[0] else [])
        ax.set_title(name, fontsize=9)
    axes[0].set_ylabel("Arc position (in)")
    sm = plt.cm.ScalarMappable(cmap=cmap, norm=plt.Normalize(0, vmax))
    bar = fig.colorbar(sm, ax=axes, orientation="horizontal", fraction=0.05, pad=0.1, aspect=40)
    bar.set_label("Net force (N)")
    bar.outline.set_visible(False)
    fig.suptitle("Force map of each finger set (mean of 4 trials, N)", x=0.02, ha="left", fontsize=10)
    fig.text(0.02, -0.1,
             "Columns are the two sides of the cylinder; rows run top to bottom. ≥ marks a saturated sensor.\n"
             f"Pressure over the 3.8 mm sensing area: 1 N ≈ {1 / SENSING_AREA_MM2:.3f} MPa.",
             fontsize=7.5, color=INK_2, va="top")
    save(fig, out_dir, "force_maps")


def main():
    args = parse_args()
    tests = find_tests(args.folder)
    if not tests:
        raise SystemExit("No test subfolders with a *_trials.csv file found.")
    print("Tests:", ", ".join(name for name, _ in tests))

    check_calibration(tests, args.calibration or os.path.join(HERE, "calibration.json"))
    sensors = load_sensors(args.calibration) if args.calibration else None
    if sensors:
        print(f"Recalculating newtons from the volts with {args.calibration}.")

    per_sensor, per_test = summarize(tests, sensors)
    out_dir = os.path.join(args.folder, "figures")
    os.makedirs(out_dir, exist_ok=True)

    plot_totals(per_test, out_dir)
    plot_profiles(per_sensor, per_test, out_dir)
    plot_maps(per_sensor, per_test, out_dir)

    table = per_sensor[["test", "sensor", "side", "arc_in", "net_mean_N", "net_sd_N", "net_mean_MPa",
                        "trials", "saturated_trials"]].round(4)
    table.to_csv(os.path.join(out_dir, "force_distribution_summary.csv"), index=False)
    per_test.drop(columns="trial_totals_N").round(3).to_csv(os.path.join(out_dir, "grip_totals_summary.csv"), index=False)

    print(per_test[["test", "total_mean_N", "total_sd_N", "side_a_pct", "center_a_in", "center_b_in",
                    "loaded_sensors", "any_saturated"]].round(2).to_string(index=False))
    print(f"Figures saved in {out_dir}")


if __name__ == "__main__":
    main()

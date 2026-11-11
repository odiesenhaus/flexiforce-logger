# FlexiForce Logger

10-channel force sensor data acquisition for a linear actuator test rig (ME 351 Senior Design, UW-Madison).

An Arduino Mega 2560 samples ten Tekscan FlexiForce sensors at 100 Hz and streams timestamped CSV over USB. A Python script on the PC starts and stops each run, saves the stream to a file, and can plot it as soon as logging ends.

## Hardware

- Arduino Mega 2560 (10-bit ADC, 5 V reference)
- 10 Tekscan FlexiForce sensors, each conditioned by its own inverting op-amp circuit (per the Tekscan datasheet) to a 0-5 V output
- Sensor channels 1-10 wired to analog pins A0-A9

## Files

| File | What it does |
| --- | --- |
| `flexiforce_logger.ino` | Arduino firmware. Samples all 10 channels at a fixed 100 Hz (4x oversampled per channel for noise reduction) and prints `time_ms,S1_V,...,S10_V` lines. Send `s` to start a run and `x` to stop. |
| `collect_data.py` | Opens the serial port, starts a run, and writes the stream to `flexiforce_<timestamp>.csv`. Stops on Ctrl+C or after `--duration` seconds, and can plot the result automatically. |
| `plot_data.py` | Quick-look voltage-vs-time plots of a saved CSV, as stacked subplots or overlaid on one axis. |
| `live_view.py` | Real-time scrolling graph straight from the serial port, with each channel's mean and standard deviation over the visible window. Saves nothing to disk. |
| `dashboard.py`, `dashboard/` | Local web dashboard: live 3D cylinder and heat grid, one-click recording, and replay of saved runs. |
| `gripper_test.py` | Runs automated gripper tests for the dashboard, then splits each recording into trials and computes statistics. |
| `calibration.py` | Fits and saves each sensor's volts-to-grams calibration for the dashboard. |

## Setup

1. Upload `flexiforce_logger.ino` to the Mega with the Arduino IDE.
2. Close the Arduino IDE's Serial Monitor/Plotter. Only one program can hold the COM port at a time.
3. Install the Python dependencies:

   ```bash
   python -m pip install -r requirements.txt
   ```

## Usage

Log a run (Ctrl+C to stop early):

```bash
python collect_data.py --port COM7
python collect_data.py --port COM7 --duration 60 --outfile run1.csv
```

Log and plot automatically when the run ends:

```bash
python collect_data.py --port COM7 --duration 30 --plot
python collect_data.py --port COM7 --duration 30 --channels 1 3 --overlay
```

Add `--stats` to print each channel's mean and standard deviation and show them on the plot (dashed line at the mean, shaded band at ±1 standard deviation). It works with `plot_data.py` too:

```bash
python collect_data.py --port COM7 --duration 30 --channels 1 --stats
python plot_data.py run1.csv --channels 1 3 --stats
```

Plot a saved file:

```bash
python plot_data.py run1.csv
python plot_data.py run1.csv --channels 1 3 5 --overlay
```

Watch the sensors live without saving a file (close the graph window to stop):

```bash
python live_view.py --port COM7
python live_view.py --port COM7 --channels 1 3 --window 30
```

On the graph, the dashed line is the mean and the dotted lines are ±1 standard deviation, all computed over the visible window (`--window` seconds, default 10).

## Dashboard

```bash
python dashboard.py
```

This opens http://localhost:8765 and only needs pyserial, so it works without internet. Choose the Mega's port and press **Start recording**. The page goes live and saves to `flexiforce_<timestamp>.csv` as data arrives, so a crash loses at most a fraction of a second. To try it without hardware, choose **Simulated Arduino**; those files are named `flexiforce_sim_<timestamp>.csv`.

- **Cylinder and heat grid:** sensors go from blue (no load) through green and yellow to red across the color range (0.43-5 V by default). The wireframe model is see-through and can be dragged to rotate, and the end view shows the sensors' true positions.
- **History and sensor table:** click any sensor to chart it (up to 4 at a time). The table lists every channel's mean, standard deviation, min, and max over the time window.
- **Summary:** side-to-side balance, where the load is centered on each side, and which sensors are saturated. These are voltage-weighted until force calibration is added.
- **Markers:** press M during a run to mark an event. It's saved in the file's `marker` column.
- **Zero sensors:** removes preload from the colors and summary. Recordings stay raw.
- **Peak mode:** colors show the highest value so far instead of the current one.
- **Live view only:** watch the sensors without creating a file.
- **Auto-stop:** ends a recording after a set number of seconds.
- **Replay:** plays any saved run through the same views, including older `collect_data.py` files.

The sensor layout, channel order, and color ranges are all in `dashboard/config.js`.

## Calibrating to grams

The **Sensor calibration** panel walks through one sensor at a time, following Tekscan's procedure:

1. **Condition the sensor (recommended):** load it with 500 g 3–5 times, about 10 s each.
2. **Set the sensitivity:** with 500 g on, turn the sensor's feedback potentiometer until the reading just reaches the top, about 4.9–5.0 V.
3. **Capture the readings:** take one with no load, then one with each weight (100, 200, and 500 g, or any other weight). Each capture averages a few seconds of data.
4. **Fit and check:** the page fits a straight line, `volts = slope × grams + intercept`, and shows how far each point is from it. Then save it, put the weights back on to check the reading, and move on to the next sensor.

Any reading that saturates (4.98 V or more) is left out of the fit, since it only shows that the load is at least that much. Saved calibrations go in `calibration.json` next to the recordings. Recalibrating or removing a sensor keeps the old values in that file's history.

Use **Show** to see calibrated sensors in grams, newtons, or pressure (MPa) everywhere on the page; sensors that aren't calibrated show "–". Newtons use g = 9.80665 m/s². Pressure is each sensor's force divided by its 3.8 mm sensing circle (11.34 mm²), so it's the average over that circle, not the peak contact pressure. In newtons or MPa, each heat grid cell shows both. Recordings keep the raw volts and, for calibrated sensors, also save `S#_N` and `S#_MPa` columns. Those use the calibration from when the recording started, and a `_calibration.json` file saved alongside shows exactly which one. Uncalibrated sensors' N and MPa columns are left blank. On the page, replays use whichever calibration is current. If you turn a sensor's potentiometer, recalibrate it.

## Automated gripper test

With the actuator Arduino plugged in as well, **Start test** runs the gripper through a set number of trials while recording. It defaults to 4 trials of 10 s closed and 10 s open, after 5 s to open the gripper and get ready. The actuator Arduino can be an Uno or a native-USB board like the M0 Pro or Zero (on either of its USB ports). It needs `actuator_control/actuator_control.ino`, which accepts `o` (open), `c` (close), and `?` (identify) over serial at 9600 baud. Its physical button still works, and the open and closed positions are still set only in that sketch.

Each test saves three files:

| File | Contents |
| --- | --- |
| `flexiforce_<time>_<name>.csv` | The full recording, with every event in the `marker` column |
| `flexiforce_<time>_<name>_trials.csv` | Only the complete trials, labeled by `trial`, `phase` (closed or open), and `trial_time_s` |
| `flexiforce_<time>_<name>_summary.csv` | Mean, standard deviation, min, max, and % saturated for each phase, sensor, and trial |

- **Units:** with calibrated sensors, every file also has newtons and MPa (`S#_N`, `S#_MPa` columns, and `mean_N`, `std_N`, `mean_MPa`, and so on in the summary), and the results table can switch between V, N, and MPa. Volts are always kept.
- **Timing:** each phase is timed from the moment its command is sent, so the closed time includes the time spent moving.
- **Statistics:** the first few seconds of each phase (**Ignore first**, default 4 s) are left out of the statistics because the gripper is still moving. Those rows stay in the trials file with `in_stats` set to 0.
- **The `all` rows in the summary:** the mean is the average of the trial means, and the standard deviation is between those trial means.
- **Stopping early:** pressing **Stop test**, pressing the physical button, or losing either connection ends the test and opens the gripper. Trials that finished are still analyzed.

Pressing the physical button during a normal recording is marked in the file automatically if the actuator Arduino is connected. Recording works the same as before if it isn't.

### Serial ports

COM numbers differ between computers and can change when a board moves to another USB socket, so nothing in the code depends on them. **Find Arduinos** asks each USB serial port which board it is and fills in both dropdowns, and it also runs when the page loads. Each port can only be used by one program at a time, so close the Arduino IDE's Serial Monitor before connecting, and press **Disconnect** before uploading a sketch.

## Notes

The logged values are volts at the ADC pin, not force. Each channel's op-amp gain (R_F / V_REF) is different, so voltages have to be converted to force with a per-sensor calibration curve as a separate step.

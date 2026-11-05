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

## Notes

The logged values are volts at the ADC pin, not force. Each channel's op-amp gain (R_F / V_REF) is different, so voltages have to be converted to force with a per-sensor calibration curve as a separate step.

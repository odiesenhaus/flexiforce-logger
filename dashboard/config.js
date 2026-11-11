// Everything about the physical setup lives here, so correcting the layout
// only means editing this file. Force calibration is done on the page and
// saved by the server in calibration.json.

// Sizes in inches. The length is only for the drawing.
export const CYLINDER = { diameterIn: 3, lengthIn: 3.5 };

// ESS102 sensor head from the Tekscan datasheet (active area is 0.15 in inside it).
export const SENSOR_HEAD_DIAMETER_IN = 0.3;

// Distance along the circumference from each side's middle sensor. Positive is up.
export const ARC_POSITIONS_IN = [1, 0.5, 0, -0.5, -1];

// Which channel sits where, listed top to bottom to match ARC_POSITIONS_IN.
// angleDeg is measured around the cylinder from the right end of the horizontal axis.
export const SIDES = [
  { name: "Side A", angleDeg: 180, channels: [1, 2, 3, 4, 5] },
  { name: "Side B", angleDeg: 0, channels: [6, 7, 8, 9, 10] },
];

// ESS102 active sensing area from the Tekscan datasheet. Pressure is shown as
// each sensor's force spread evenly over this circle.
export const SENSING_DIAMETER_MM = 3.8;

// Default color scales. In volts the bottom is the unloaded op-amp output; the
// others top out at the 500 g that the sensors are tuned to saturate at.
export const COLOR_RANGE = { min: 0.43, max: 5 };
export const FORCE_UNIT = "g";
export const FORCE_COLOR_RANGE = { min: 0, max: 500 };
export const NEWTON_COLOR_RANGE = { min: 0, max: 5 };
export const PRESSURE_COLOR_RANGE = { min: 0, max: 0.45 };

// Readings at or above this many volts are flagged, since the true value may be higher.
export const SATURATION_LEVEL = 4.98;

// The weights offered as one-click calibration points, in grams.
export const CALIBRATION_WEIGHTS_G = [0, 100, 200, 500];

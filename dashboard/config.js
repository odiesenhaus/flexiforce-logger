// Everything about the physical setup lives here, so correcting the layout or
// adding force calibration only means editing this file.

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

// Default color scale: the bottom is the unloaded op-amp output.
export const COLOR_RANGE = { min: 0.43, max: 5 };

// Readings at or above this are flagged, since the true value may be higher.
export const SATURATION_LEVEL = 4.98;

// Force calibration goes here. Every view on the page uses this unit and this
// function, and recordings always keep the raw volts. When it's added, also
// change COLOR_RANGE and SATURATION_LEVEL above to the new unit.
export const UNIT = "V";
export function toDisplayUnits(channel, volts) {
  return volts;
}

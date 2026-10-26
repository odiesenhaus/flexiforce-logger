/*
  FlexiForce 10-Sensor Logger — Arduino Mega
  --------------------------------------------------------
  Reads 10 FlexiForce sensors (each behind its own inverting
  op-amp circuit, per the Tekscan datasheet) on analog pins
  A0-A9, and streams timestamped CSV over USB serial at a
  fixed sample rate.

  Output columns:
    time_ms, S1_V, S2_V, S3_V, S4_V, S5_V, S6_V, S7_V, S8_V, S9_V, S10_V

  Values are volts at the ADC pin (0-5V), NOT force. Convert
  to force downstream (Python/MATLAB) using each sensor's own
  calibration curve, since each op-amp circuit's R_F/V_REF
  setting changes the V-to-force relationship per channel.

  Serial commands (send from Serial Monitor or a script):
    's' -> start logging (resets t=0 to the moment 's' is received)
    'x' -> stop logging (Arduino stays idle, ready for next 's')

  --------------------------------------------------------
  HARDWARE ASSUMPTIONS (change the #define block if different):
    - Arduino Mega 2560 (10-bit ADC, 0-1023 counts, AREF = 5V)
    - Sensors wired to A0..A9 in channel order 1..10
    - Each channel already conditioned by its own op-amp circuit
      to output 0-5V (no raw sensor voltage dividers on the Mega)
  --------------------------------------------------------
*/

// ----------------------- CONFIG -----------------------

const uint8_t  NUM_SENSORS   = 10;
const uint8_t  SENSOR_PINS[NUM_SENSORS] = {A0, A1, A2, A3, A4, A5, A6, A7, A8, A9};

const float    ADC_MAX_COUNTS = 1023.0;   // 10-bit ADC on Mega
const float    VREF_ADC       = 5.0;      // Mega AREF, volts

const unsigned long SAMPLE_RATE_HZ = 100;                         // target sample rate
const unsigned long SAMPLE_INTERVAL_US = 1000000UL / SAMPLE_RATE_HZ;

const unsigned long SERIAL_BAUD = 115200;

const uint8_t  ADC_OVERSAMPLE = 4;  // avg this many reads per channel per sample, for noise reduction

// --------------------------------------------------------

bool logging = false;
unsigned long nextSampleMicros = 0;
unsigned long startMillisOffset = 0;

void setup() {
  Serial.begin(SERIAL_BAUD);
  // analogReference(DEFAULT) is the Mega default (5V) — left explicit for clarity
  analogReference(DEFAULT);

  Serial.println(F("# FlexiForce 10-channel logger ready."));
  Serial.println(F("# Send 's' to start, 'x' to stop."));
  printHeader();
}

void loop() {
  handleSerialCommands();

  if (logging) {
    unsigned long nowMicros = micros();
    if ((long)(nowMicros - nextSampleMicros) >= 0) {
      takeSampleAndPrint();
      // schedule next sample relative to the target time, not "now",
      // so the average rate doesn't drift even if a loop iteration runs long
      nextSampleMicros += SAMPLE_INTERVAL_US;
    }
  }
}

void handleSerialCommands() {
  while (Serial.available() > 0) {
    char c = Serial.read();
    if (c == 's' || c == 'S') {
      startMillisOffset = millis();
      nextSampleMicros = micros();
      logging = true;
      Serial.println(F("# LOGGING START"));
      printHeader();
    } else if (c == 'x' || c == 'X') {
      logging = false;
      Serial.println(F("# LOGGING STOP"));
    }
  }
}

void printHeader() {
  Serial.print(F("time_ms"));
  for (uint8_t i = 0; i < NUM_SENSORS; i++) {
    Serial.print(F(",S"));
    Serial.print(i + 1);
    Serial.print(F("_V"));
  }
  Serial.println();
}

void takeSampleAndPrint() {
  unsigned long tStamp = millis() - startMillisOffset;

  Serial.print(tStamp);
  for (uint8_t i = 0; i < NUM_SENSORS; i++) {
    float voltage = readVoltage(SENSOR_PINS[i]);
    Serial.print(',');
    Serial.print(voltage, 4);
  }
  Serial.println();
}

float readVoltage(uint8_t pin) {
  uint32_t sum = 0;
  for (uint8_t k = 0; k < ADC_OVERSAMPLE; k++) {
    sum += analogRead(pin);
  }
  float avgCounts = (float)sum / ADC_OVERSAMPLE;
  return (avgCounts / ADC_MAX_COUNTS) * VREF_ADC;
}

#include <Servo.h>

// Serial commands (from the dashboard, or type them in Serial Monitor at 9600 baud):
//   'o' -> open gripper
//   'c' -> close gripper
//   '?' -> report which Arduino this is and the gripper state
// The physical button keeps working either way.

//Objects
Servo linearActuator;
const float STROKE_LENGTH_IN = 2.0; // Linear actuator stroke length
const float OPEN_POSITION_IN = 2.0;
const float CLOSED_POSITION_IN = .75; //0.6 IN IS MINIMUM
const int ACTUATOR_PIN = 8;
const int BUTTON_PIN = 7;
bool isOpen = false; // Track gripper state
bool hasMoved = false; // The position is unknown until the first move


void setup() {

  // The actuator isn't attached until the first move. Attaching sends a mid-stroke
  // pulse by default, and this board resets whenever a computer opens its serial
  // port, so attaching here could move the gripper just from connecting.
  pinMode(BUTTON_PIN, INPUT_PULLUP); // Internal pull-up, no resistor needed
  Serial.begin(9600);
  Serial.println(F("# ACTUATOR CONTROLLER READY"));

}

void setPosition(float in) {
  // Map position from 1000 to 2000 microseconds (1ms-2ms pulse, which is open and close for this actuator)
  in = constrain(in, 0.0, STROKE_LENGTH_IN);  // Constrain position to STROKE_LENGTH_IN
  float frac = in / STROKE_LENGTH_IN; // Convert position to fraction
  int pulseUs= 1000 + (int)(frac * 1000); // Convert fraction to pulse length

  linearActuator.writeMicroseconds(pulseUs); // Set actuator pulse
  if (!linearActuator.attached()) {
    linearActuator.attach(ACTUATOR_PIN); // After setting the pulse, so the first pulse is already the target
  }

}

void setGripper(bool open, const __FlashStringHelper *source) {
  isOpen = open;
  hasMoved = true;

  if (isOpen) {
    Serial.print(F("# GRIPPER OPEN "));
    setPosition(OPEN_POSITION_IN);
  } else {
    Serial.print(F("# GRIPPER CLOSED "));
    setPosition(CLOSED_POSITION_IN);
  }
  Serial.println(source);
}

void handleSerialCommands() {
  while (Serial.available() > 0) {
    char c = Serial.read();
    if (c == 'o') {
      setGripper(true, F("command"));
    } else if (c == 'c') {
      setGripper(false, F("command"));
    } else if (c == '?') {
      Serial.print(F("# ACTUATOR CONTROLLER gripper="));
      if (!hasMoved) {
        Serial.println(F("unknown"));
      } else {
        Serial.println(isOpen ? F("open") : F("closed"));
      }
    }
  }
}

void loop() {
  handleSerialCommands();

  if (digitalRead(BUTTON_PIN) == LOW) { // LOW means pressed with pull-up
    setGripper(!isOpen, F("button")); // Toggle state

    while (digitalRead(BUTTON_PIN) == LOW) { delay(10); } // wait for release
    delay(50); // debounce after release
    }

  // Read sensors
  int sensorValue = analogRead(A0);
  // Convert the analog reading (which goes from 0 - 1023) to a voltage (0 - 5V):
  float voltage = sensorValue * (5.0 / 1023.0);
  // print out the value you read:
  //Serial.println(voltage);
}

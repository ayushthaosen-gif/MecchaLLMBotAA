/*
  esp32_servo_bridge.ino
  ----------------------
  Runs on the ESP32, talks to the Raspberry Pi over USB serial, and drives
  the Meccanoid smart-servo bus itself — moving the timing-sensitive part
  of this project off the Pi's Python/Linux stack entirely.

  Why this exists: motion_engine.py on the Pi paces servo commands with
  time.sleep(), which can jitter under CPU load because of Python's GIL
  and Linux not being a real-time OS (measured and confirmed in
  stress_test_gpio_timing.py — jitter spiked to 60ms+ against a 20ms
  target under heavy load). The ESP32 has no GIL, and this sketch
  dedicates one full core to nothing but servo bus timing.

  Command protocol (Pi -> ESP32), plain ASCII, newline-terminated:
      A:0=90,1=120,2=60,3=90\n
  meaning "servo 0 to 90 degrees, servo 1 to 120", etc. Angles are already
  calibration-corrected by servo_controller.py on the Pi side — this
  firmware just relays them onto the bus.

  PROTOCOL: no longer a placeholder. Ported from the community-reverse-
  engineered "SM protocol" (alexfrederiksen/MeccanoidForArduino,
  Meccanoid.cpp/.h — see servo_controller.py's module docstring for the
  full writeup this firmware mirrors byte-for-byte and timing-for-timing):
  single half-duplex wire, up to MAX_CHAIN=4 modules, each bus cycle sends
  HEADER_BYTE + 4 output bytes + checksum, then reads back one poll byte.
  Framing per byte: 1 start bit (417us LOW), 8 data bits LSB-first (417us
  each), 2 stop bits (417us HIGH each). This is why a plain HardwareSerial
  UART peripheral can't be used here — the bus is a single bidirectional
  pin, not separate TX/RX lines, so it's bit-banged with digitalWrite/
  delayMicroseconds/pulseIn instead, matching the reference library.

  Still worth verifying against your own logic-analyzer capture before
  fully trusting it — this project has not independently confirmed it
  against real Meccanoid silicon.

  This firmware drives ONE chain (matches servo_controller.py's flat,
  non-rig model that brain.py actually uses — MAX_SERVOS below is just
  historical headroom; the real confirmed hardware only has 4 servos on
  this chain). The corrected 2-independent-chain rig model (rig.py) is
  not wired up to an ESP32 bridge yet — that would need this same
  bit-banging logic duplicated onto a second GPIO pin, one per arm.
*/

#include <Arduino.h>

#define SERVO_BUS_PIN    17   // single half-duplex wire -> level shifter -> Meccanoid bus
#define MAX_SERVOS       8
#define MAX_CHAIN        4    // the real bus's hard limit — same constant as servo_controller.py
#define BIT_DELAY_US     417
#define MAX_COMMAND_LENGTH 128

#define HEADER_BYTE  0xFF
#define NOMOD_BYTE   0xFE
#define TYPE_NONE    0x00
#define TYPE_SERVO   0x01

#define SERVO_MIN 0x18   // 24  — byte value at logical angle 0
#define SERVO_MAX 0xE8   // 232 — byte value at logical angle 180

// Shared state between core 0 (serial command parsing) and core 1
// (bus-timing task). Guarded by a simple mutex since both cores touch it.
volatile int targetAngles[MAX_SERVOS];
volatile bool angleDirty[MAX_SERVOS];
SemaphoreHandle_t stateMutex;

TaskHandle_t busTaskHandle;

// Real Chain::outputs[] / moduleNum equivalent — see servo_controller.py's
// _build_frame()/_checksum(), which this ports 1:1.
uint8_t chainOutputs[MAX_CHAIN];
uint8_t chainPollIndex = 0;

uint8_t angleToByte(int angle) {
  angle = constrain(angle, 0, 180);
  return (uint8_t)round(SERVO_MIN + (angle / 180.0) * (SERVO_MAX - SERVO_MIN));
}

// ---------------------------------------------------------------------------
// Bit-level bus I/O — ported from Chain::sendByte()/receiveByte().
// ---------------------------------------------------------------------------
void sendByte(uint8_t data) {
  pinMode(SERVO_BUS_PIN, OUTPUT);

  digitalWrite(SERVO_BUS_PIN, LOW);          // start bit
  delayMicroseconds(BIT_DELAY_US);

  for (uint8_t mask = 0x01; mask > 0; mask <<= 1) {
    digitalWrite(SERVO_BUS_PIN, (data & mask) ? HIGH : LOW);
    delayMicroseconds(BIT_DELAY_US);
  }

  digitalWrite(SERVO_BUS_PIN, HIGH);         // 2 stop bits
  delayMicroseconds(BIT_DELAY_US);
  digitalWrite(SERVO_BUS_PIN, HIGH);
  delayMicroseconds(BIT_DELAY_US);
}

uint8_t receiveByte() {
  uint8_t result = 0;
  pinMode(SERVO_BUS_PIN, INPUT);
  delay(1);  // ~1.5ms turnaround in the reference; rounds to the nearest ms here

  for (uint8_t mask = 0x01; mask > 0; mask <<= 1) {
    if (pulseIn(SERVO_BUS_PIN, HIGH, 2500) > 400)
      result |= mask;
  }
  return result;
}

// checksum — ported from Chain::calculateCheckSum(). Deliberately widened
// to a 32-bit accumulator before the final byte truncation, matching the
// reference's `int sum` (only the `& 0xF0` step matters for correctness,
// since it already collapses the result into a single byte).
uint8_t calculateChecksum(const uint8_t outputs[MAX_CHAIN], uint8_t pollIndex) {
  uint32_t sum = 0;
  for (int i = 0; i < MAX_CHAIN; i++) sum += outputs[i];
  sum = sum + (sum >> 8);
  sum = sum + (sum << 4);
  sum = sum & 0xF0;
  sum = sum | (pollIndex & 0x0F);
  return (uint8_t)(sum & 0xFF);
}

// One full bus cycle: send HEADER + 4 output slots + checksum, then read
// back one poll byte for the currently-indexed slot, advancing the index —
// ported from Chain::update()'s send/receive/rotate sequence.
void updateChain() {
  sendByte(HEADER_BYTE);
  for (int i = 0; i < MAX_CHAIN; i++) sendByte(chainOutputs[i]);
  sendByte(calculateChecksum(chainOutputs, chainPollIndex));

  receiveByte();  // poll response — logged/used for position feedback once
                   // this firmware grows a read path back to the Pi; for
                   // now the write path (Pi -> servo) is what's exercised.

  chainPollIndex = (chainPollIndex + 1) % MAX_CHAIN;
}

// ---------------------------------------------------------------------------
// Core 1 task: does nothing but push current target angles onto the bus at
// a steady cadence. Pinned away from core 0 so USB serial parsing, WiFi
// (if you add it later), etc. never delay this loop's timing.
// ---------------------------------------------------------------------------
void busTimingTask(void *pvParameters) {
  const TickType_t stepInterval = pdMS_TO_TICKS(20); // matches motion_engine.py's STEP_INTERVAL
  TickType_t lastWake = xTaskGetTickCount();

  for (;;) {
    if (xSemaphoreTake(stateMutex, pdMS_TO_TICKS(5)) == pdTRUE) {
      for (int i = 0; i < MAX_CHAIN; i++) {
        if (angleDirty[i]) {
          chainOutputs[i] = angleToByte(targetAngles[i]);
          angleDirty[i] = false;
        }
      }
      xSemaphoreGive(stateMutex);
    }
    updateChain();  // always sends the full frame, even with nothing new —
                     // matches the real bus having no addressed single-servo write
    vTaskDelayUntil(&lastWake, stepInterval);
  }
}

// ---------------------------------------------------------------------------
// Core 0: parse "A:0=90,1=120,...\n" lines from the Pi over USB serial.
// ---------------------------------------------------------------------------
void parseCommandLine(String line) {
  if (!line.startsWith("A:")) return;
  line = line.substring(2);

  int start = 0;
  while (start < (int)line.length()) {
    int comma = line.indexOf(',', start);
    String pair = (comma == -1) ? line.substring(start) : line.substring(start, comma);

    int eq = pair.indexOf('=');
    if (eq > 0) {
      String servoText = pair.substring(0, eq);
      String angleText = pair.substring(eq + 1);
      char *servoEnd = nullptr;
      char *angleEnd = nullptr;
      long servoId = strtol(servoText.c_str(), &servoEnd, 10);
      long angle = strtol(angleText.c_str(), &angleEnd, 10);
      bool servoValid = servoEnd != servoText.c_str() && *servoEnd == '\0';
      bool angleValid = angleEnd != angleText.c_str() && *angleEnd == '\0';
      // Clamped to MAX_CHAIN, not MAX_SERVOS: only the first 4 slots are
      // ever actually sent on the bus (see busTimingTask) — silently
      // accepting ids 4-7 here would look like it worked but never move
      // anything.
      if (servoValid && angleValid && servoId >= 0 && servoId < MAX_CHAIN) {
        angle = constrain(angle, 0, 180);
        if (xSemaphoreTake(stateMutex, pdMS_TO_TICKS(5)) == pdTRUE) {
          targetAngles[servoId] = angle;
          angleDirty[servoId] = true;
          xSemaphoreGive(stateMutex);
        }
      }
    }

    if (comma == -1) break;
    start = comma + 1;
  }
}

void setup() {
  Serial.begin(115200);            // USB link to the Pi
  pinMode(SERVO_BUS_PIN, OUTPUT);
  digitalWrite(SERVO_BUS_PIN, HIGH);  // idle-high, matches the bus's stop-bit level

  stateMutex = xSemaphoreCreateMutex();
  for (int i = 0; i < MAX_SERVOS; i++) {
    targetAngles[i] = 90;
    angleDirty[i] = false;
  }
  for (int i = 0; i < MAX_CHAIN; i++) {
    chainOutputs[i] = angleToByte(90);
  }

  // Pin the timing-critical loop to core 1; setup()/loop() run on core 0
  // by default on Arduino-ESP32, which is where we parse USB serial.
  xTaskCreatePinnedToCore(
    busTimingTask,
    "ServoBusTiming",
    4096,
    NULL,
    2,            // priority
    &busTaskHandle,
    1             // core 1
  );

  Serial.println("esp32_servo_bridge ready");
}

void loop() {
  static String buffer;
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      parseCommandLine(buffer);
      buffer = "";
    } else if (c != '\r') {
      if (buffer.length() < MAX_COMMAND_LENGTH) {
        buffer += c;
      } else {
        // Drop an overlong/incomplete line instead of allowing unbounded
        // heap growth. Its remaining bytes are ignored until newline.
        buffer = "";
        while (Serial.available() && Serial.peek() != '\n') Serial.read();
      }
    }
  }
}

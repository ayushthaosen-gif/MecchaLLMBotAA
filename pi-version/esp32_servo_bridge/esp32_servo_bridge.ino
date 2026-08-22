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
  firmware just relays them onto the bus as fast and consistently as
  possible.

  PACKET FORMAT WARNING (same caveat as servo_controller.py):
  Meccano never published the smart-servo protocol. The byte format below
  mirrors the Python placeholder for consistency, but you must verify it
  against your own logic-analyzer capture before trusting it on real
  hardware — this is the one thing in this file to treat as unconfirmed.
*/

#include <Arduino.h>

#define SERVO_BUS_TX_PIN 17   // ESP32 UART2 TX -> level shifter -> Meccanoid bus
#define SERVO_BUS_RX_PIN 16   // ESP32 UART2 RX <- level shifter <- Meccanoid bus (if bus is bidirectional)
#define SERVO_BUS_BAUD   9600 // placeholder — match your captured protocol
#define MAX_SERVOS       8

HardwareSerial ServoBus(2); // UART2 — dedicated to the Meccanoid bus, separate
                            // from Serial (UART0), which stays free for USB/Pi comms

// Shared state between core 0 (serial command parsing) and core 1
// (bus-timing task). Guarded by a simple mutex since both cores touch it.
volatile int targetAngles[MAX_SERVOS];
volatile bool angleDirty[MAX_SERVOS];
SemaphoreHandle_t stateMutex;

TaskHandle_t busTaskHandle;

// ---------------------------------------------------------------------------
// Packet building — mirrors servo_controller.py's placeholder format.
// Replace this to match your verified protocol.
// ---------------------------------------------------------------------------
void sendServoPacket(uint8_t servoId, uint8_t angle) {
  uint8_t start = 0xFF;
  uint8_t payload[3] = { start, servoId, angle };
  uint8_t checksum = payload[0] ^ payload[1] ^ payload[2];

  ServoBus.write(payload, 3);
  ServoBus.write(checksum);
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
      for (int i = 0; i < MAX_SERVOS; i++) {
        if (angleDirty[i]) {
          sendServoPacket((uint8_t)i, (uint8_t)targetAngles[i]);
          angleDirty[i] = false;
        }
      }
      xSemaphoreGive(stateMutex);
    }
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
      int servoId = pair.substring(0, eq).toInt();
      int angle = pair.substring(eq + 1).toInt();
      if (servoId >= 0 && servoId < MAX_SERVOS) {
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
  ServoBus.begin(SERVO_BUS_BAUD, SERIAL_8N1, SERVO_BUS_RX_PIN, SERVO_BUS_TX_PIN);

  stateMutex = xSemaphoreCreateMutex();
  for (int i = 0; i < MAX_SERVOS; i++) {
    targetAngles[i] = 90;
    angleDirty[i] = false;
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
    } else {
      buffer += c;
    }
  }
}

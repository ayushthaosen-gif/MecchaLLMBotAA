/*
  esp32_cloud_brain.ino
  ----------------------
  The ESP32-only architecture: no Raspberry Pi at all. This board does
  everything the Pi used to do on the device side — it just never runs
  the LLM itself, since that's not possible on this chip. Instead:

    1. Connects to WiFi directly
    2. On a timer, polls the cloud function's /next_command endpoint
    3. If a gesture is pending, plays it on the Meccanoid servo bus
       (same timing-isolated approach as esp32_servo_bridge.ino — one
       core dedicated to bus timing)
    4. POSTs /ack back to the cloud function when done

  The dashboard talks to the SAME cloud function directly (POST /chat)
  — this board never receives inbound requests, only makes outbound
  ones, which is what lets it work behind home WiFi/NAT with no port
  forwarding.

  PROTOCOL: no longer a placeholder. Ported from the community-reverse-
  engineered "SM protocol" (alexfrederiksen/MeccanoidForArduino,
  Meccanoid.cpp/.h — see servo_controller.py's module docstring for the
  full writeup, which this firmware mirrors byte-for-byte and
  timing-for-timing): single half-duplex wire, up to 4 modules per chain,
  each bus cycle sends HEADER_BYTE + 4 output bytes + checksum, then
  reads back one poll byte. Framing per byte: 1 start bit (417us LOW), 8
  data bits LSB-first (417us each), 2 stop bits (417us HIGH each) — bit-
  banged with digitalWrite/delayMicroseconds/pulseIn on a single pin,
  since a normal two-wire UART peripheral can't reproduce a shared
  half-duplex line. Still worth verifying against your own logic-analyzer
  capture before fully trusting it on real hardware.

  SECURITY NOTE: WIFI_PASSWORD and any cloud auth token live in this
  firmware's flash. Never put your ANTHROPIC_API_KEY here — that stays
  server-side in the cloud function, which is exactly why the split
  exists.
*/

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

// ---- fill these in ----
const char* WIFI_SSID = "your-wifi-name";
const char* WIFI_PASSWORD = "your-wifi-password";
const char* CLOUD_BASE_URL = "https://your-region-your-project.cloudfunctions.net";
// -----------------------

#define SERVO_BUS_PIN     17   // single half-duplex wire -> level shifter -> Meccanoid bus
#define NUM_SERVOS        4
#define MAX_CHAIN         4    // the real bus's hard limit — same constant as servo_controller.py
#define BIT_DELAY_US      417
#define POLL_INTERVAL_MS  1500

#define HEADER_BYTE  0xFF
#define NOMOD_BYTE   0xFE

#define SERVO_MIN 0x18   // 24  — byte value at logical angle 0
#define SERVO_MAX 0xE8   // 232 — byte value at logical angle 180

uint8_t chainOutputs[MAX_CHAIN];
uint8_t chainPollIndex = 0;

uint8_t angleToByte(int angle) {
  angle = constrain(angle, 0, 180);
  return (uint8_t)round(SERVO_MIN + (angle / 180.0) * (SERVO_MAX - SERVO_MIN));
}

struct Keyframe {
  int angles[NUM_SERVOS];
  int holdMs;
};

// Servo indices match rig.py's mapping for this exact robot (4 servos,
// arms only, no head): 0=right_shoulder, 1=right_elbow,
// 2=left_shoulder, 3=left_elbow.
Keyframe GESTURE_WAVE_RIGHT[] = {
  {{150, 120, 90, 90}, 400},
  {{150, 70, 90, 90}, 250},
  {{150, 120, 90, 90}, 250},
  {{90, 90, 90, 90}, 400},
};
Keyframe GESTURE_WAVE_BOTH[] = {
  {{150, 120, 150, 120}, 400},
  {{150, 70, 150, 70}, 300},
  {{150, 120, 150, 120}, 300},
  {{90, 90, 90, 90}, 400},
};
Keyframe GESTURE_BOW[] = {
  {{50, 70, 50, 70}, 600},
  {{50, 70, 50, 70}, 800},
  {{90, 90, 90, 90}, 600},
};
Keyframe GESTURE_SHRUG[] = {
  {{130, 60, 130, 120}, 500},
  {{130, 60, 130, 120}, 500},
  {{90, 90, 90, 90}, 500},
};
Keyframe GESTURE_POINT[] = {
  {{100, 170, 90, 90}, 600},
  {{100, 170, 90, 90}, 800},
  {{90, 90, 90, 90}, 600},
};
Keyframe GESTURE_FULL_DANCE[] = {
  {{60, 130, 120, 50}, 350},
  {{120, 50, 60, 130}, 350},
  {{60, 130, 120, 50}, 350},
  {{120, 50, 60, 130}, 350},
  {{90, 90, 90, 90}, 500},
};
Keyframe GESTURE_SIT[] = {
  {{170, 20, 170, 20}, 600},
};

// --- Dance / meme gestures (same choreography as rig_gestures.py/gestures.py) ---
Keyframe GESTURE_DAB[] = {
  {{155, 35, 35, 165}, 500},
  {{155, 35, 35, 165}, 800},
  {{90, 90, 90, 90}, 600},
};
Keyframe GESTURE_FLEX[] = {
  {{140, 30, 140, 30}, 400},
  {{135, 40, 135, 40}, 250},
  {{140, 30, 140, 30}, 500},
  {{90, 90, 90, 90}, 500},
};
Keyframe GESTURE_FLOSS[] = {
  {{130, 150, 55, 45}, 220},
  {{55, 45, 130, 150}, 220},
  {{130, 150, 55, 45}, 220},
  {{55, 45, 130, 150}, 220},
  {{90, 90, 90, 90}, 400},
};
// Partial-keyframe rig-model version isolates one joint per step; this
// firmware's Keyframe always specifies all 4 angles, so each step here
// repeats the previous frame's other joints unchanged to get the same effect.
Keyframe GESTURE_THE_ROBOT[] = {
  {{140, 90, 90, 90}, 150},
  {{140, 60, 90, 90}, 150},
  {{90, 60, 90, 90}, 150},
  {{90, 90, 90, 90}, 150},
  {{90, 90, 140, 90}, 150},
  {{90, 90, 140, 60}, 150},
  {{90, 90, 90, 60}, 150},
  {{90, 90, 90, 90}, 150},
  {{120, 90, 120, 90}, 200},
  {{90, 90, 90, 90}, 400},
};
Keyframe GESTURE_MIC_DROP[] = {
  {{110, 50, 90, 90}, 500},
  {{110, 50, 90, 90}, 600},
  {{30, 170, 90, 90}, 350},
  {{30, 170, 90, 90}, 500},
  {{90, 90, 90, 90}, 500},
};
Keyframe GESTURE_FINGER_GUNS[] = {
  {{100, 170, 100, 170}, 350},
  {{95, 150, 95, 150}, 150},
  {{100, 170, 100, 170}, 350},
  {{95, 150, 95, 150}, 150},
  {{90, 90, 90, 90}, 500},
};


Keyframe GESTURE_AURA_FARM[] = {
  {{115, 70, 70, 115}, 550}, {{70, 115, 115, 70}, 550},
  {{125, 80, 125, 80}, 700}, {{90, 90, 90, 90}, 500},
};
Keyframe GESTURE_SIX_SEVEN[] = {
  {{75, 115, 115, 75}, 300}, {{115, 75, 75, 115}, 300},
  {{75, 115, 115, 75}, 300}, {{115, 75, 75, 115}, 300},
  {{90, 90, 90, 90}, 400},
};
Keyframe GESTURE_NPC_MODE[] = {
  {{120, 60, 120, 60}, 180}, {{105, 85, 105, 85}, 180},
  {{120, 60, 120, 60}, 180}, {{105, 85, 105, 85}, 180},
  {{90, 90, 90, 90}, 400},
};
Keyframe GESTURE_FACEPALM[] = {
  {{125, 35, 90, 90}, 550}, {{125, 35, 90, 90}, 650},
  {{90, 90, 90, 90}, 550},
};
Keyframe GESTURE_SUCCESS_PUMP[] = {
  {{145, 45, 90, 90}, 250}, {{120, 70, 90, 90}, 180},
  {{145, 45, 90, 90}, 300}, {{90, 90, 90, 90}, 450},
};
Keyframe GESTURE_SIDE_EYE[] = {
  {{125, 55, 80, 105}, 450}, {{125, 55, 80, 105}, 700},
  {{90, 90, 90, 90}, 500},
};

volatile int currentAngles[NUM_SERVOS] = {90, 90, 90, 90};
SemaphoreHandle_t busMutex;
QueueHandle_t gestureQueue; // holds pointers to a small struct describing which gesture to play

struct GestureRequest {
  char name[16];
};

// ---------------------------------------------------------------------------
// Bit-level bus I/O — ported from Chain::sendByte()/receiveByte() in
// alexfrederiksen/MeccanoidForArduino.
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

// checksum — ported from Chain::calculateCheckSum(). Widened to a 32-bit
// accumulator before the final byte truncation, matching the reference's
// `int sum` (only the `& 0xF0` step matters for correctness, since it
// already collapses the result into a single byte).
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
// back one poll byte for the currently-indexed slot, advancing the index.
// The real bus has no addressed single-servo write, so this always sends
// every slot's current value, not just whichever one changed.
void updateChain() {
  sendByte(HEADER_BYTE);
  for (int i = 0; i < MAX_CHAIN; i++) sendByte(chainOutputs[i]);
  sendByte(calculateChecksum(chainOutputs, chainPollIndex));

  receiveByte();  // poll response — not yet wired to a feedback path

  chainPollIndex = (chainPollIndex + 1) % MAX_CHAIN;
}

float easeInOut(float t) {
  return t * t * (3.0f - 2.0f * t);
}

void playKeyframeSequence(Keyframe* seq, int count) {
  const int stepIntervalMs = 20;
  int startAngles[NUM_SERVOS];

  for (int k = 0; k < count; k++) {
    if (xSemaphoreTake(busMutex, pdMS_TO_TICKS(10)) == pdTRUE) {
      for (int i = 0; i < NUM_SERVOS; i++) startAngles[i] = currentAngles[i];
      xSemaphoreGive(busMutex);
    }

    int maxDelta = 0;
    for (int i = 0; i < NUM_SERVOS; i++) {
      int d = abs(seq[k].angles[i] - startAngles[i]);
      if (d > maxDelta) maxDelta = d;
    }
    int speedDurationMs = maxDelta * 1000 / 220; // 220 deg/sec cap, matches motion_engine.py
    int durationMs = max(max(speedDurationMs, seq[k].holdMs), 50);
    int steps = max(durationMs / stepIntervalMs, 1);

    for (int s = 1; s <= steps; s++) {
      float t = easeInOut((float)s / steps);
      if (xSemaphoreTake(busMutex, pdMS_TO_TICKS(10)) == pdTRUE) {
        for (int i = 0; i < NUM_SERVOS; i++) {
          int frameAngle = startAngles[i] + (int)((seq[k].angles[i] - startAngles[i]) * t);
          currentAngles[i] = frameAngle;
          chainOutputs[i] = angleToByte(frameAngle);
        }
        // One whole-chain frame per step, not one packet per servo — the
        // real bus has no addressed single-servo write (see updateChain()).
        updateChain();
        xSemaphoreGive(busMutex);
      }
      vTaskDelay(pdMS_TO_TICKS(stepIntervalMs));
    }
  }
}

// ---------------------------------------------------------------------------
// Core 1 task: owns all servo bus timing, isolated from WiFi/HTTP on core 0.
// ---------------------------------------------------------------------------
void motionTask(void* pvParameters) {
  GestureRequest req;
  for (;;) {
    if (xQueueReceive(gestureQueue, &req, portMAX_DELAY) == pdTRUE) {
      if (strcmp(req.name, "wave_right") == 0) {
        playKeyframeSequence(GESTURE_WAVE_RIGHT, sizeof(GESTURE_WAVE_RIGHT) / sizeof(Keyframe));
      } else if (strcmp(req.name, "wave_both") == 0) {
        playKeyframeSequence(GESTURE_WAVE_BOTH, sizeof(GESTURE_WAVE_BOTH) / sizeof(Keyframe));
      } else if (strcmp(req.name, "bow") == 0) {
        playKeyframeSequence(GESTURE_BOW, sizeof(GESTURE_BOW) / sizeof(Keyframe));
      } else if (strcmp(req.name, "shrug") == 0) {
        playKeyframeSequence(GESTURE_SHRUG, sizeof(GESTURE_SHRUG) / sizeof(Keyframe));
      } else if (strcmp(req.name, "point") == 0) {
        playKeyframeSequence(GESTURE_POINT, sizeof(GESTURE_POINT) / sizeof(Keyframe));
      } else if (strcmp(req.name, "full_dance") == 0) {
        playKeyframeSequence(GESTURE_FULL_DANCE, sizeof(GESTURE_FULL_DANCE) / sizeof(Keyframe));
      } else if (strcmp(req.name, "sit") == 0) {
        playKeyframeSequence(GESTURE_SIT, sizeof(GESTURE_SIT) / sizeof(Keyframe));
      } else if (strcmp(req.name, "dab") == 0) {
        playKeyframeSequence(GESTURE_DAB, sizeof(GESTURE_DAB) / sizeof(Keyframe));
      } else if (strcmp(req.name, "flex") == 0) {
        playKeyframeSequence(GESTURE_FLEX, sizeof(GESTURE_FLEX) / sizeof(Keyframe));
      } else if (strcmp(req.name, "floss") == 0) {
        playKeyframeSequence(GESTURE_FLOSS, sizeof(GESTURE_FLOSS) / sizeof(Keyframe));
      } else if (strcmp(req.name, "the_robot") == 0) {
        playKeyframeSequence(GESTURE_THE_ROBOT, sizeof(GESTURE_THE_ROBOT) / sizeof(Keyframe));
      } else if (strcmp(req.name, "mic_drop") == 0) {
        playKeyframeSequence(GESTURE_MIC_DROP, sizeof(GESTURE_MIC_DROP) / sizeof(Keyframe));
      } else if (strcmp(req.name, "finger_guns") == 0) {
        playKeyframeSequence(GESTURE_FINGER_GUNS, sizeof(GESTURE_FINGER_GUNS) / sizeof(Keyframe));
      } else if (strcmp(req.name, "aura_farm") == 0) {
        playKeyframeSequence(GESTURE_AURA_FARM, sizeof(GESTURE_AURA_FARM) / sizeof(Keyframe));
      } else if (strcmp(req.name, "six_seven") == 0) {
        playKeyframeSequence(GESTURE_SIX_SEVEN, sizeof(GESTURE_SIX_SEVEN) / sizeof(Keyframe));
      } else if (strcmp(req.name, "npc_mode") == 0) {
        playKeyframeSequence(GESTURE_NPC_MODE, sizeof(GESTURE_NPC_MODE) / sizeof(Keyframe));
      } else if (strcmp(req.name, "facepalm") == 0) {
        playKeyframeSequence(GESTURE_FACEPALM, sizeof(GESTURE_FACEPALM) / sizeof(Keyframe));
      } else if (strcmp(req.name, "success_pump") == 0) {
        playKeyframeSequence(GESTURE_SUCCESS_PUMP, sizeof(GESTURE_SUCCESS_PUMP) / sizeof(Keyframe));
      } else if (strcmp(req.name, "side_eye") == 0) {
        playKeyframeSequence(GESTURE_SIDE_EYE, sizeof(GESTURE_SIDE_EYE) / sizeof(Keyframe));
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Core 0: WiFi + polling loop
// ---------------------------------------------------------------------------
void pollCloudForCommand() {
  // Polls /next_motion frequently — this endpoint only ever holds a
  // gesture name and is never blocked by the cloud function's LLM call,
  // since the cloud side enqueues motion before calling the model.
  HTTPClient http;
  http.begin(String(CLOUD_BASE_URL) + "/next_motion");
  int code = http.GET();
  if (code == 200) {
    String body = http.getString();
    StaticJsonDocument<512> doc;
    if (deserializeJson(doc, body) == DeserializationError::Ok) {
      if (doc["pending"] == true) {
        String gesture = doc["gesture"].as<String>();
        String cmdId = doc["id"].as<String>();

        bool queued = true;
        if (gesture.length() > 0) {
          GestureRequest req;
          gesture.toCharArray(req.name, sizeof(req.name));
          // 0-tick timeout: never block the poll loop. If motionTask is
          // still mid-gesture and gestureQueue (depth 4) is full, this
          // fails — only ack when it actually succeeded, otherwise the
          // cloud side marks the item delivered and never resends it,
          // silently dropping the gesture.
          queued = xQueueSend(gestureQueue, &req, 0) == pdTRUE;
          if (!queued) {
            Serial.println("gestureQueue full, dropping poll — will retry next cycle");
          }
        }

        if (queued) {
          HTTPClient ackHttp;
          ackHttp.begin(String(CLOUD_BASE_URL) + "/ack_motion");
          ackHttp.addHeader("Content-Type", "application/json");
          String payload = "{\"id\":\"" + cmdId + "\"}";
          ackHttp.POST(payload);
          ackHttp.end();
        }
      }
    }
  }
  http.end();

  // Reply text isn't needed on this board (no speaker/display attached
  // yet) but /next_reply + /ack_reply exist on the cloud function for
  // whenever TTS or a status display is added — poll less often than
  // motion since it's not time-critical.
}

#define WIFI_CONNECT_TIMEOUT_MS 20000

// Blocks up to WIFI_CONNECT_TIMEOUT_MS, returns whether it connected.
// setup() no longer waits forever on a bad SSID/password, and loop()
// reuses this to reconnect after a drop instead of just silently failing
// every poll forever.
bool connectWiFi() {
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED) {
    if (millis() - start > WIFI_CONNECT_TIMEOUT_MS) {
      Serial.println("\nWiFi connect timed out, will keep retrying in loop()");
      return false;
    }
    delay(300);
    Serial.print(".");
  }
  Serial.println("\nWiFi connected: " + WiFi.localIP().toString());
  return true;
}

void setup() {
  Serial.begin(115200);
  pinMode(SERVO_BUS_PIN, OUTPUT);
  digitalWrite(SERVO_BUS_PIN, HIGH);  // idle-high, matches the bus's stop-bit level
  for (int i = 0; i < MAX_CHAIN; i++) chainOutputs[i] = angleToByte(90);

  connectWiFi();

  busMutex = xSemaphoreCreateMutex();
  gestureQueue = xQueueCreate(4, sizeof(GestureRequest));

  xTaskCreatePinnedToCore(motionTask, "MotionTask", 4096, NULL, 2, NULL, 1);
}

void loop() {
  // Without this check, a WiFi drop after boot (router reboot, DHCP lease
  // issue, roaming AP) leaves HTTPClient::begin/GET failing silently
  // forever — the robot goes unresponsive until manually power-cycled.
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi disconnected, reconnecting...");
    connectWiFi();
    delay(POLL_INTERVAL_MS);
    return;
  }

  pollCloudForCommand();
  delay(POLL_INTERVAL_MS);
}

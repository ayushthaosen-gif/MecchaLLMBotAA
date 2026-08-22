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

  PACKET FORMAT WARNING — same as the other firmware in this project:
  Meccano never published the smart-servo protocol; verify the bytes in
  sendServoPacket() against your own logic-analyzer capture.

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

#define SERVO_BUS_TX_PIN 17
#define SERVO_BUS_RX_PIN 16
#define SERVO_BUS_BAUD   9600
#define NUM_SERVOS       4
#define POLL_INTERVAL_MS 1500

HardwareSerial ServoBus(2);

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

volatile int currentAngles[NUM_SERVOS] = {90, 90, 90, 90};
SemaphoreHandle_t busMutex;
QueueHandle_t gestureQueue; // holds pointers to a small struct describing which gesture to play

struct GestureRequest {
  char name[16];
};

// ---------------------------------------------------------------------------
// Packet building — mirrors servo_controller.py's placeholder format.
// ---------------------------------------------------------------------------
void sendServoPacket(uint8_t servoId, uint8_t angle) {
  uint8_t payload[3] = { 0xFF, servoId, angle };
  uint8_t checksum = payload[0] ^ payload[1] ^ payload[2];
  ServoBus.write(payload, 3);
  ServoBus.write(checksum);
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
          sendServoPacket(i, (uint8_t)frameAngle);
        }
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

        if (gesture.length() > 0) {
          GestureRequest req;
          gesture.toCharArray(req.name, sizeof(req.name));
          xQueueSend(gestureQueue, &req, 0);
        }

        HTTPClient ackHttp;
        ackHttp.begin(String(CLOUD_BASE_URL) + "/ack_motion");
        ackHttp.addHeader("Content-Type", "application/json");
        String payload = "{\"id\":\"" + cmdId + "\"}";
        ackHttp.POST(payload);
        ackHttp.end();
      }
    }
  }
  http.end();

  // Reply text isn't needed on this board (no speaker/display attached
  // yet) but /next_reply + /ack_reply exist on the cloud function for
  // whenever TTS or a status display is added — poll less often than
  // motion since it's not time-critical.
}

void setup() {
  Serial.begin(115200);
  ServoBus.begin(SERVO_BUS_BAUD, SERIAL_8N1, SERVO_BUS_RX_PIN, SERVO_BUS_TX_PIN);

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(300);
    Serial.print(".");
  }
  Serial.println("\nWiFi connected: " + WiFi.localIP().toString());

  busMutex = xSemaphoreCreateMutex();
  gestureQueue = xQueueCreate(4, sizeof(GestureRequest));

  xTaskCreatePinnedToCore(motionTask, "MotionTask", 4096, NULL, 2, NULL, 1);
}

void loop() {
  pollCloudForCommand();
  delay(POLL_INTERVAL_MS);
}

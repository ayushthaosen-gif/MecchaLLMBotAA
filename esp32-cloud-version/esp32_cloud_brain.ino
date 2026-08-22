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
const char* ROBOT_ID = "meccanoid-1";
const char* ROBOT_API_TOKEN = "replace-with-a-long-random-token";
// -----------------------

#define SERVO_BUS_TX_PIN 17
#define SERVO_BUS_RX_PIN 16
#define SERVO_BUS_BAUD   9600
#define NUM_SERVOS       4
#define POLL_INTERVAL_MS 1500

// H-bridge pins for the two wheeled-foot DC motors. Verify these against
// the selected motor driver before powering the motors.
#define LEFT_MOTOR_IN1 25
#define LEFT_MOTOR_IN2 26
#define LEFT_MOTOR_PWM 32
#define RIGHT_MOTOR_IN1 27
#define RIGHT_MOTOR_IN2 14
#define RIGHT_MOTOR_PWM 33

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
QueueHandle_t gestureQueue;
volatile bool commandInFlight = false;

struct CommandRequest {
  char kind[12];
  char name[16];
  char id[40];
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

void setMotor(int in1, int in2, int pwmPin, int speed) {
  int power = constrain(abs(speed), 0, 255);
  digitalWrite(in1, speed >= 0 ? HIGH : LOW);
  digitalWrite(in2, speed >= 0 ? LOW : HIGH);
  analogWrite(pwmPin, power);
}

void setDrive(int left, int right) {
  setMotor(LEFT_MOTOR_IN1, LEFT_MOTOR_IN2, LEFT_MOTOR_PWM, left);
  setMotor(RIGHT_MOTOR_IN1, RIGHT_MOTOR_IN2, RIGHT_MOTOR_PWM, right);
}

void stopDrive() {
  analogWrite(LEFT_MOTOR_PWM, 0);
  analogWrite(RIGHT_MOTOR_PWM, 0);
}

bool playLocomotion(const char* name) {
  if (strcmp(name, "forward") == 0) setDrive(190, 190);
  else if (strcmp(name, "backward") == 0) setDrive(-190, -190);
  else if (strcmp(name, "turn_left") == 0) setDrive(-170, 170);
  else if (strcmp(name, "turn_right") == 0) setDrive(170, -170);
  else if (strcmp(name, "turn_around") == 0) setDrive(180, -180);
  else return false;
  vTaskDelay(pdMS_TO_TICKS(strcmp(name, "turn_around") == 0 ? 1200 : 700));
  stopDrive();
  return true;
}

void addCloudHeaders(HTTPClient& http) {
  if (strlen(ROBOT_API_TOKEN) > 0) {
    http.addHeader("Authorization", String("Bearer ") + ROBOT_API_TOKEN);
  }
}

void acknowledgeCommand(const CommandRequest& req) {
  HTTPClient http;
  String endpoint = strcmp(req.kind, "motion") == 0 ? "/ack_motion" : "/ack_locomotion";
  http.begin(String(CLOUD_BASE_URL) + endpoint);
  addCloudHeaders(http);
  http.addHeader("Content-Type", "application/json");
  String payload = String("{\"robot_id\":\"") + ROBOT_ID + "\",\"id\":\"" + req.id + "\"}";
  http.POST(payload);
  http.end();
}

// ---------------------------------------------------------------------------
// Core 1 task: owns all servo bus timing, isolated from WiFi/HTTP on core 0.
// ---------------------------------------------------------------------------
void motionTask(void* pvParameters) {
  CommandRequest req;
  for (;;) {
    if (xQueueReceive(gestureQueue, &req, portMAX_DELAY) != pdTRUE) continue;

    bool completed = false;
    if (strcmp(req.kind, "locomotion") == 0) {
      completed = playLocomotion(req.name);
    } else if (strcmp(req.name, "wave_right") == 0) {
      playKeyframeSequence(GESTURE_WAVE_RIGHT, sizeof(GESTURE_WAVE_RIGHT) / sizeof(Keyframe));
      completed = true;
    } else if (strcmp(req.name, "wave_both") == 0) {
      playKeyframeSequence(GESTURE_WAVE_BOTH, sizeof(GESTURE_WAVE_BOTH) / sizeof(Keyframe));
      completed = true;
    } else if (strcmp(req.name, "bow") == 0) {
      playKeyframeSequence(GESTURE_BOW, sizeof(GESTURE_BOW) / sizeof(Keyframe));
      completed = true;
    } else if (strcmp(req.name, "shrug") == 0) {
      playKeyframeSequence(GESTURE_SHRUG, sizeof(GESTURE_SHRUG) / sizeof(Keyframe));
      completed = true;
    } else if (strcmp(req.name, "point") == 0) {
      playKeyframeSequence(GESTURE_POINT, sizeof(GESTURE_POINT) / sizeof(Keyframe));
      completed = true;
    } else if (strcmp(req.name, "full_dance") == 0) {
      playKeyframeSequence(GESTURE_FULL_DANCE, sizeof(GESTURE_FULL_DANCE) / sizeof(Keyframe));
      completed = true;
    } else if (strcmp(req.name, "sit") == 0) {
      playKeyframeSequence(GESTURE_SIT, sizeof(GESTURE_SIT) / sizeof(Keyframe));
      completed = true;
    }

    // ACK only after a recognized command completes. Unknown commands remain
    // pending for inspection instead of being silently discarded.
    if (completed) acknowledgeCommand(req);
    commandInFlight = false;
  }
}

// ---------------------------------------------------------------------------
// Core 0: WiFi + polling loop
// ---------------------------------------------------------------------------
void pollQueue(const char* endpoint, const char* valueKey, const char* kind) {
  HTTPClient http;
  String url = String(CLOUD_BASE_URL) + endpoint + "?robot_id=" + ROBOT_ID;
  http.begin(url);
  addCloudHeaders(http);
  int code = http.GET();
  if (code == 200) {
    StaticJsonDocument<512> doc;
    if (deserializeJson(doc, http.getString()) == DeserializationError::Ok
        && doc["pending"] == true) {
      CommandRequest req = {};
      String value = doc[valueKey].as<String>();
      String id = doc["id"].as<String>();
      String(kind).toCharArray(req.kind, sizeof(req.kind));
      value.toCharArray(req.name, sizeof(req.name));
      id.toCharArray(req.id, sizeof(req.id));

      // Do not ACK when the local queue is full. The cloud will return the
      // same pending item on the next poll, providing at-least-once delivery.
      if (xQueueSend(gestureQueue, &req, 0) == pdTRUE) commandInFlight = true;
    }
  }
  http.end();
}

void pollCloudForCommand() {
  if (commandInFlight) return;
  pollQueue("/next_motion", "gesture", "motion");
  pollQueue("/next_locomotion", "locomotion", "locomotion");
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

  pinMode(LEFT_MOTOR_IN1, OUTPUT);
  pinMode(LEFT_MOTOR_IN2, OUTPUT);
  pinMode(LEFT_MOTOR_PWM, OUTPUT);
  pinMode(RIGHT_MOTOR_IN1, OUTPUT);
  pinMode(RIGHT_MOTOR_IN2, OUTPUT);
  pinMode(RIGHT_MOTOR_PWM, OUTPUT);
  stopDrive();

  busMutex = xSemaphoreCreateMutex();
  gestureQueue = xQueueCreate(8, sizeof(CommandRequest));

  xTaskCreatePinnedToCore(motionTask, "MotionTask", 4096, NULL, 2, NULL, 1);
}

void loop() {
  pollCloudForCommand();
  delay(POLL_INTERVAL_MS);
}

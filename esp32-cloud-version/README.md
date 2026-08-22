# Meccanoid: ESP32-only + Cloud LLM

No Raspberry Pi in this version — the ESP32 talks to a cloud function
directly. See the main project's README for the alternative Pi-based
architecture; this folder is the standalone ESP32 path we discussed.

## Files
- `esp32_cloud_brain.ino` — ESP32 firmware: connects to WiFi, polls the
  cloud function for commands, drives the Meccanoid servo bus itself
  (timing isolated on core 1, same approach as the Pi project's
  `esp32_servo_bridge.ino`)
- `cloud_function/main.py` — the actual "brain": calls Claude, holds
  conversation memory, does the weather intercept, decides gestures,
  and exposes a small command queue the ESP32 polls
- `simulate_full_run.py` — runs the entire flow with no real hardware,
  WiFi, or cloud deployment, by reusing the tested `servo_controller.py`
  / `motion_engine.py` / `gestures.py` from the Pi project as a stand-in
  for what the ESP32 firmware does
- `run_history.json` — recorded servo angle trajectory from the last
  simulation run

## How the pieces connect

```
Dashboard ──POST /chat──► Cloud Function ──► Claude API
                                │
                        command queue (in-memory here;
                        Firestore/Supabase in production)
                                │
                     ESP32 polls /next_command every ~1.5s
                                │
                         Meccanoid servo bus
```

The ESP32 never receives inbound requests — it only polls outward. That's
deliberate: it sits behind home WiFi/NAT with no public IP, so nothing
could reach it directly even if you wanted to.

## Deploying the cloud function for real

```bash
pip install functions-framework flask requests anthropic
export ANTHROPIC_API_KEY=sk-ant-...

# Local test server:
functions-framework --target=chat --debug

# Deploy to Google Cloud Functions (or adapt for Firebase/Supabase Edge Functions):
gcloud functions deploy chat --runtime=python312 --trigger-http --allow-unauthenticated
gcloud functions deploy next_command --runtime=python312 --trigger-http --allow-unauthenticated
gcloud functions deploy ack --runtime=python312 --trigger-http --allow-unauthenticated
```

Then fill in `CLOUD_BASE_URL`, `WIFI_SSID`, `WIFI_PASSWORD` at the top of
`esp32_cloud_brain.ino` and flash it.

**Never put `ANTHROPIC_API_KEY` in the .ino file** — it stays server-side
in the cloud function's environment variables. That's the whole point of
this split: a lost or dumped ESP32 never exposes your API key.

## Swapping in real persistent memory

`MemoryStore` in `cloud_function/main.py` is an in-memory list for local
testing. Replace its two methods (`append`, `recent_text`) with real
Firestore or Supabase calls for production — nothing else in the file
needs to change.

## Running the simulation yourself

```bash
python3 simulate_full_run.py
```

Prints a full log of dashboard messages, cloud function replies/gesture
decisions, the simulated ESP32 poll loop picking each up, and the
resulting servo angle history — same data used to check that gestures
land in the right time window relative to the message that triggered
them.

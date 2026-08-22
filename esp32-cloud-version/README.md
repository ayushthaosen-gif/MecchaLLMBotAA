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

## Swapping in real persistent memory (Firestore)

`MemoryStore` in `cloud_function/main.py` is an in-memory list — fine for
local testing, but a deployed Cloud Function's process memory is wiped on
every cold start, and there can be several instances at once, so it isn't
actually "permanent memory that survives reboots" until it's backed by
real storage.

`firestore_memory.py` implements the same two-method interface
(`append`, `recent_text`) against Firestore instead, so no other file
needs to change — `main.py` picks the backend via `build_memory_store()`:

```bash
pip install google-cloud-firestore

export MEMORY_BACKEND=firestore                    # default is "memory"
export FIRESTORE_MEMORY_COLLECTION=meccanoid_memory # optional, this is the default
export GOOGLE_CLOUD_PROJECT=your-project-id         # picked up automatically when deployed
```

Auth uses Application Default Credentials:
- Deployed on Cloud Functions/Cloud Run — automatic, nothing to set up.
- Local testing against a real project: `gcloud auth application-default login`
- Local testing without touching real cloud resources — the Firestore
  emulator:
  ```bash
  gcloud emulators firestore start --host-port=localhost:8080
  export FIRESTORE_EMULATOR_HOST=localhost:8080
  export MEMORY_BACKEND=firestore
  ```

Firestore setup (one-time, in the Firebase/GCP console or `gcloud`):
```bash
gcloud firestore databases create --location=nam5   # or your preferred region
```
No manual collection/schema creation needed — `firestore_memory.py`
creates the `meccanoid_memory` collection on first write.

## Dashboard hosting (Firebase Hosting)

`docs/dashboard.html` is a static page with no build step — set its
"Link configuration" panel to your deployed `/chat` URL and it talks
directly to the cloud function (see `simulate_full_run.py`'s "How the
pieces connect" diagram above). Firebase Hosting is a natural fit since
you're likely already using a Firebase project for Firestore:

```bash
npm install -g firebase-tools
firebase login
firebase init hosting     # public directory: docs
firebase deploy --only hosting
```

That gives the dashboard a stable `https://your-project.web.app` URL to
open from a phone — the endpoint field is saved in the browser's
`localStorage`, so it only needs to be set once per device.

## Gemini 2.5 setup

`LLM_PROVIDER=gemini` is the default (see `cloud_llm_backends.py`), using
`gemini-2.5-flash` — fast and cheap, a good match for a chat companion
robot that may get messaged all day:

```bash
export GEMINI_API_KEY=...            # from https://aistudio.google.com/apikey
export GEMINI_MODEL=gemini-2.5-flash # override if you want a different tier
```

Swap to Claude at any time with `LLM_PROVIDER=claude` + `ANTHROPIC_API_KEY`
— nothing else in the stack (memory, gestures, dashboard) needs to change,
same swappable-backend pattern as the Pi project's `llm_backend.py`.

## Running the simulation yourself

```bash
python3 simulate_full_run.py
```

Prints a full log of dashboard messages, cloud function replies/gesture
decisions, the simulated ESP32 poll loop picking each up, and the
resulting servo angle history — same data used to check that gestures
land in the right time window relative to the message that triggered
them.

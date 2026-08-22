# Meccanoid Cloud-AI Brain — Raspberry Pi side

## Files
- `servo_controller.py` — serial bus abstraction, runs in **simulation mode** by default
- `gestures.py` — named movement sequences (wave, dance, look_around, sit) + keyword triggers
- `motion_engine.py` — background thread that plays gestures without blocking Claude calls
- `brain.py` — Flask server the dashboard talks to; calls Claude, persists memory, does the weather intercept
- `requirements.txt`

## Before running on real hardware

**The servo packet format in `servo_controller.py` is a placeholder, not a
confirmed protocol.** Meccano never published one. Capture the real bytes
yourself with a logic analyzer between a stock MeccaBrain and the servo
chain, then replace `_build_packet()` in `servo_controller.py` to match.
Until then, leave `simulate=True` — the rest of the stack (threading,
gestures, Flask routes, Claude calls) works identically against the
simulator, so you can build and test everything else first.

## Setup

### Option A — free, local, runs entirely on the Pi (default)

Uses [Ollama](https://ollama.com) to run a small open model on-device. No
API key, no per-message cost, no internet needed for chat itself.

```bash
# one-time: install Ollama and pull a small model
curl -fsSL https://ollama.com/install.sh | sh
ollama pull gemma3:1b        # fast: ~18-22 tok/s on a Pi 5 8GB, good default
# ollama pull qwen2.5:3b      # slower (~4-7 tok/s) but noticeably smarter
ollama serve &                # starts the local API on port 11434

cd meccanoid-brain
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt --break-system-packages

export LLM_BACKEND=ollama                       # default, can omit
export OLLAMA_MODEL=gemma3:1b                   # match whatever you pulled
export DASHBOARD_TOKEN=pick-a-shared-secret     # optional, but recommended since
                                                  # the dashboard is reachable by anyone
                                                  # with the URL
export WEATHER_LOCATION="40.71,-74.01"          # lat,lon for the weather intercept
export SIMULATE_SERVOS=1                        # keep at 1 until the protocol is verified

python3 brain.py
```

Trade-off to expect: local models this small are fine for greetings, small
talk, and simple Q&A, but noticeably weaker than a cloud model on anything
that needs real reasoning. A Pi 5 with 8GB RAM is the realistic minimum;
4GB works only with the smallest (1B) models.

### Option B — paid, calls the Claude API

More capable replies, costs per token, needs internet.

```bash
export LLM_BACKEND=claude
export ANTHROPIC_API_KEY=sk-ant-...
export CLAUDE_MODEL=claude-haiku-4-5-20251001   # or claude-sonnet-5 for more depth
pip install anthropic --break-system-packages
python3 brain.py
```

You can switch between the two at any time by changing `LLM_BACKEND` and
restarting `brain.py` — nothing else in the stack (gestures, memory,
dashboard) needs to change.

The server listens on port 5000. On the dashboard's "Link configuration"
panel, set:
- Endpoint: `http://<your-pi-ip>:5000/chat`
- Auth token: whatever you set `DASHBOARD_TOKEN` to

## Wiring (once protocol bytes are confirmed)

```
Pi GPIO TX/RX  --> 3.3V-to-5V logic level shifter --> Meccanoid servo bus
Pi 5V/GND      --> only for the Pi itself
Servo power    --> separate 5V/3A power bank (isolated from the Pi's supply)
4.7k pull-ups  --> on the servo data line per your captured protocol's needs
```

Keeping servo power on its own bank (not sharing the Pi's supply) is what
the isolation in the architecture diagram is for — motor stall current
can brown out a Pi sharing the same rail.

## Testing without hardware

```bash
python3 servo_controller.py   # simulated angle sweep
python3 motion_engine.py      # proves wave -> dance queue non-blocking
```

Both print `[SIM]` lines instead of touching a serial port.

## Split architecture: Pi + ESP32 (optional, recommended for reliability)

By default (`transport="direct"` in `ServoBusConfig`) the Pi drives the
Meccanoid bus itself — simplest wiring, but servo command timing shares
the Pi's CPU with everything else (Flask, the LLM, JSON parsing), and
`stress_test_gpio_timing.py` shows that can jitter under load (steps
meant to be 20ms apart spiked past 60ms in testing).

Setting `transport="esp32"` instead has the Pi send batched plain-text
angle commands over USB serial to an ESP32, which builds the real
Meccanoid bus packets and drives their timing from a FreeRTOS task
pinned to its own core — completely decoupled from whatever Python is
doing. Nothing else in the stack changes: `motion_engine.py`,
`gestures.py`, and `brain.py` are unaware of which transport is active.

```python
bus = ServoBus(ServoBusConfig(
    simulate=False,
    transport="esp32",
    esp32_port="/dev/ttyUSB0",   # wherever the ESP32 enumerates over USB
    esp32_baud=115200,
    calibration_offsets={2: -3},  # still applied on the Pi side before sending
))
```

Firmware: `esp32_servo_bridge/esp32_servo_bridge.ino` — flash with the
Arduino IDE (ESP32 board support installed) or `arduino-cli`. Same
packet-format caveat applies as `servo_controller.py`: verify the actual
bytes against your own logic-analyzer capture before trusting it on
real servos.

Wiring for this option:
```
Pi USB port  ───── USB cable ─────  ESP32 USB port  (commands + power)
ESP32 GPIO17 (TX2) ──► level shifter ──► Meccanoid servo bus data in
ESP32 GPIO16 (RX2) ◄── level shifter ◄── Meccanoid servo bus data out
ESP32 GND          ──► level shifter GND (shared with servo power bank GND)
```
This replaces the Pi-GPIO wiring in `pinout.md` — the Pi no longer
touches the servo bus directly at all in this mode; its GPIO header is
free for a future camera or sensor instead.



`motion_engine.py` interpolates every gesture — it eases each servo from
its current angle to the target instead of snapping, capped at
`max_speed_deg_per_sec` (default 220°/s). Tune that number down for
gentler motion, up for snappier. `servo_controller.py` also supports
per-servo calibration offsets:

```python
bus = ServoBus(ServoBusConfig(
    simulate=True,
    servo_count=4,
    calibration_offsets={2: -3},   # servo 2's true center is 3° off
))
```

Test both together without hardware:

```bash
python3 -c "
from servo_controller import ServoBus, ServoBusConfig
from motion_engine import MotionEngine
import time
bus = ServoBus(ServoBusConfig(simulate=True, servo_count=4))
engine = MotionEngine(bus)
engine.start()
engine.play('wave')
time.sleep(3)
engine.stop()
"
```


- Real news lookup: fill in `fetch_headlines()` in `brain.py` with a News
  API / GNews key, same pattern as the weather fetch.
- Cloud sync: `append_memory()` is the one place to also push to
  Firebase/Supabase if you want the dashboard reading live state instead
  of only the local `.txt` file.
- More gestures: add entries to `GESTURES` and `KEYWORD_TRIGGERS` in
  `gestures.py` — no other file needs to change.

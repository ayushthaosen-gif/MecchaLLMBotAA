"""
brain.py
--------
The Raspberry Pi "brain" process. Exposes POST /chat for the web
dashboard, calls the Claude API, appends every exchange to a local
memory file (and optionally a cloud DB), intercepts weather/news
questions with a real web lookup before asking Claude, and fires
motor gestures via motion_engine when the message matches a trigger
phrase (see gestures.py).

Run with:
    export ANTHROPIC_API_KEY=sk-ant-...
    export DASHBOARD_TOKEN=some-shared-secret     # optional but recommended
    python3 brain.py

The dashboard's "Link configuration" panel should point at:
    http://<pi-ip>:5000/chat
"""

import os
import json
import time
import datetime
from pathlib import Path

from flask import Flask, request, jsonify
import requests

from servo_controller import ServoBus, ServoBusConfig
from motion_engine import MotionEngine
from gestures import match_gesture_from_text
from llm_backend import build_llm_backend
from mirror_control import MirrorController

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# LLM_BACKEND = "ollama" (free, runs on the Pi, default) or "claude" (paid API)
LLM_BACKEND = os.environ.get("LLM_BACKEND", "ollama")
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN")  # if set, /chat requires it
MEMORY_FILE = Path(os.environ.get("MEMORY_FILE", "robot_memory.txt"))
SIMULATE_SERVOS = os.environ.get("SIMULATE_SERVOS", "1") != "0"
WEATHER_LOCATION = os.environ.get("WEATHER_LOCATION", "")  # e.g. "40.71,-74.01"
ENABLE_MIRROR_CONTROL = os.environ.get("ENABLE_MIRROR_CONTROL", "0") == "1"
MIRROR_ALLOWED_ORIGIN = os.environ.get("MIRROR_ALLOWED_ORIGIN", "")

llm = build_llm_backend(LLM_BACKEND)

bus = ServoBus(ServoBusConfig(simulate=SIMULATE_SERVOS, servo_count=4))
motion = MotionEngine(bus)
motion.start()
mirror = MirrorController(bus) if ENABLE_MIRROR_CONTROL else None

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Persistent memory — appended, never overwritten, survives power resets
# ---------------------------------------------------------------------------

def load_recent_memory(max_chars: int = 4000) -> str:
    """Return the tail of the memory file so it fits in the Claude prompt
    without growing unbounded as the file gets larger over months of use."""
    if not MEMORY_FILE.exists():
        return ""
    try:
        text = MEMORY_FILE.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # A corrupted/binary/non-UTF8 file (e.g. from a crash mid-write)
        # must not permanently break every /chat request — degrade to no
        # memory context instead of raising.
        print(f"[memory] failed to read {MEMORY_FILE}: {exc}")
        return ""
    return text[-max_chars:]


def append_memory(role: str, text: str) -> None:
    timestamp = datetime.datetime.now().isoformat(timespec="seconds")
    with MEMORY_FILE.open("a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {role}: {text}\n")


# ---------------------------------------------------------------------------
# Internet tool use — simple keyword intercept, real HTTP lookup, then the
# result gets folded into the Claude prompt as extra context.
# ---------------------------------------------------------------------------

def maybe_fetch_tool_context(message: str) -> str:
    lower = message.lower()

    if "weather" in lower:
        return fetch_weather()

    if "news" in lower or "headlines" in lower:
        return fetch_headlines()

    return ""


def fetch_weather() -> str:
    if not WEATHER_LOCATION:
        return "(Weather requested, but WEATHER_LOCATION isn't configured on the Pi.)"
    try:
        lat, lon = WEATHER_LOCATION.split(",")
        # Open-Meteo needs no API key — good fit for an on-device tool call.
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}&current_weather=true"
        )
        resp = requests.get(url, timeout=5)
        data = resp.json()
        cw = data.get("current_weather", {})
        return (
            f"Live weather lookup: {cw.get('temperature')}°C, "
            f"windspeed {cw.get('windspeed')} km/h."
        )
    except Exception as exc:
        return f"(Weather lookup failed: {exc})"


def fetch_headlines() -> str:
    # Placeholder for a real news API (e.g. NewsAPI, GNews) — plug in a key
    # via an environment variable the same way ANTHROPIC_API_KEY is handled.
    return "(News lookup not wired up yet — add a news API key to fetch_headlines().)"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/chat", methods=["POST"])
def chat():
    if DASHBOARD_TOKEN:
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {DASHBOARD_TOKEN}":
            return jsonify({"error": "unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    message = (body.get("message") or "").strip()
    if not message:
        return jsonify({"error": "missing 'message'"}), 400

    append_memory("you", message)

    # Fire a physical gesture in the background — non-blocking, so it plays
    # while we wait on the Claude API call below.
    gesture = match_gesture_from_text(message)
    if gesture and mirror and mirror.active:
        gesture = None  # direct pose control owns the arm bus until its dead-man fires
    if gesture:
        motion.play(gesture)

    tool_context = maybe_fetch_tool_context(message)
    memory_context = load_recent_memory()

    system_prompt = (
        "You are the voice of a physical Meccanoid robot with a chest-mounted "
        "Raspberry Pi brain. Keep replies short and speakable out loud — one "
        "or two sentences. Here is recent conversation history for context:\n\n"
        f"{memory_context}"
    )
    if tool_context:
        system_prompt += f"\n\nLive tool result to use in your answer:\n{tool_context}"

    try:
        reply_text = llm.chat(system_prompt=system_prompt, user_message=message)
    except Exception as exc:
        return jsonify({"error": f"LLM call failed: {exc}"}), 502

    append_memory("robot", reply_text)

    return jsonify({
        "reply": reply_text,
        "gesture_triggered": gesture,
        "tool_used": bool(tool_context),
    })

@app.after_request
def mirror_cors(response):
    if request.path == "/mirror_pose" and MIRROR_ALLOWED_ORIGIN:
        response.headers["Access-Control-Allow-Origin"] = MIRROR_ALLOWED_ORIGIN
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        response.headers["Vary"] = "Origin"
    return response


@app.route("/mirror_pose", methods=["POST", "OPTIONS"])
def mirror_pose():
    if request.method == "OPTIONS":
        return ("", 204)
    if mirror is None:
        return jsonify({"error": "mirror control disabled"}), 503
    if not DASHBOARD_TOKEN:
        return jsonify({"error": "DASHBOARD_TOKEN is required for mirror control"}), 503
    if request.headers.get("Authorization", "") != f"Bearer {DASHBOARD_TOKEN}":
        return jsonify({"error": "unauthorized"}), 401
    if motion.is_busy:
        return jsonify({"error": "gesture playback currently owns the arm bus"}), 409
    body = request.get_json(silent=True) or {}
    try:
        applied = mirror.apply(body.get("pose"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if applied is None:
        return jsonify({"error": "pose updates are limited to 20 Hz"}), 429
    return jsonify({"applied": applied})

@app.route("/status", methods=["GET"])
def status():
    return jsonify({
        "busy": motion.is_busy,
        "servo_count": bus.config.servo_count,
        "simulated": bus.config.simulate,
        "mirror_enabled": mirror is not None,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

"""
cloud_function/main.py
------------------------
Deployable as a Firebase Cloud Function, Supabase Edge Function (Python
runtime), or a plain Cloud Run / Google Cloud Functions service — it's
written against `functions_framework` so it works the same way locally
and in any of those.

This replaces brain.py's job when there's no Raspberry Pi: the ESP32
has no capable LLM of its own, so this cloud function calls the real
model (Gemini 2.5 by default, Claude as a swap-in option — see
cloud_llm_backends.py), keeps conversation memory, does the weather
intercept, and decides which gesture to fire. The ESP32 never talks to
the cloud LLM directly — it only:
  1. POSTs the dashboard's message here (/chat)
  2. Polls here for the next gesture command (/next_command)
  3. ACKs once it's finished playing a gesture (/ack)

This pull model (ESP32 polls, rather than the cloud pushing to the
ESP32) is deliberate: the ESP32 sits behind home WiFi/NAT with no public
IP, so nothing can reach it directly. Polling avoids needing port
forwarding or a persistent inbound connection.

Persistent memory: this file uses an in-memory dict for simplicity/local
testing (see simulate_full_run.py). In real deployment, swap
`MemoryStore` for actual Firestore or Supabase calls — the interface is
kept intentionally small so that's a one-class swap, not a rewrite.
"""

import os
from typing import Optional

import requests

from cloud_llm_backends import build_llm_backend
from state_backend import build_state_backend

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "gemini")  # "gemini" or "claude"
WEATHER_LOCATION = os.environ.get("WEATHER_LOCATION", "")
ROBOT_API_TOKEN = os.environ.get("ROBOT_API_TOKEN", "")
DEFAULT_ROBOT_ID = os.environ.get("ROBOT_ID", "meccanoid-1")


# ---------------------------------------------------------------------------
# Memory — swap this class's internals for Firestore/Supabase in production.
# The rest of this file only calls append() / recent_text(), so that's the
# only class that needs to change.
# ---------------------------------------------------------------------------

class SimpleQueue:
    """Compatibility adapter around a robot-scoped state backend."""

    def __init__(self, backend, robot_id: str, queue_name: str):
        self.backend = backend
        self.robot_id = robot_id
        self.queue_name = queue_name

    def enqueue(self, value: str):
        return self.backend.enqueue(self.robot_id, self.queue_name, value)

    def next_undelivered(self):
        return self.backend.next_item(self.robot_id, self.queue_name)

    def ack(self, item_id: str) -> bool:
        return self.backend.ack(self.robot_id, self.queue_name, item_id)


# ---------------------------------------------------------------------------
# Gesture keyword matching — same trigger words as the Pi version, kept
# here since the ESP32 only executes a gesture *name*, it doesn't decide
# which one to run.
# ---------------------------------------------------------------------------

KEYWORD_TRIGGERS = {
    "wave_right": ["wave", "hello", "hi there"],
    "wave_both": ["wave both", "wave with both", "big wave"],
    "bow": ["bow", "take a bow"],
    "shrug": ["shrug", "i don't know", "dunno"],
    "point": ["point at", "point over there"],
    "full_dance": ["dance", "boogie", "groove"],
    "sit": ["sit down", "rest", "power down"],
}

# Locomotion is a SEPARATE hardware system from the arm servos — the 2
# wheeled-foot DC motors, not the smart-servo bus. Kept as its own
# trigger table since a message could plausibly want both at once (e.g.
# "come here and wave hello").
LOCOMOTION_TRIGGERS = {
    "forward": ["come here", "come forward", "move forward", "move closer"],
    "backward": ["back up", "move back", "go backward", "step back"],
    "turn_left": ["turn left"],
    "turn_right": ["turn right"],
    "turn_around": ["turn around", "spin around"],
}


def match_locomotion(text: str) -> Optional[str]:
    """Same longest-match specificity rule as match_gesture below."""
    lower = text.lower()
    best_name = None
    best_len = -1
    for name, phrases in LOCOMOTION_TRIGGERS.items():
        for p in phrases:
            if p in lower and len(p) > best_len:
                best_name = name
                best_len = len(p)
    return best_name


def match_gesture(text: str) -> Optional[str]:
    """Picks the most specific (longest) matching phrase rather than the
    first gesture in dict order — otherwise "wave" (wave_right) would
    shadow "wave with both" (wave_both) whenever both appear in a message.
    Same fix as rig_gestures.py on the Pi-based project."""
    lower = text.lower()
    best_gesture = None
    best_len = -1
    for gesture, phrases in KEYWORD_TRIGGERS.items():
        for p in phrases:
            if p in lower and len(p) > best_len:
                best_gesture = gesture
                best_len = len(p)
    return best_gesture


# ---------------------------------------------------------------------------
# Weather intercept — same pattern as brain.py's version
# ---------------------------------------------------------------------------

def maybe_fetch_tool_context(message: str) -> str:
    if "weather" not in message.lower():
        return ""
    if not WEATHER_LOCATION:
        return "(Weather requested, but WEATHER_LOCATION isn't configured.)"
    try:
        lat, lon = WEATHER_LOCATION.split(",")
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
        resp = requests.get(url, timeout=5)
        cw = resp.json().get("current_weather", {})
        return f"Live weather lookup: {cw.get('temperature')}°C, windspeed {cw.get('windspeed')} km/h."
    except Exception as exc:
        return f"(Weather lookup failed: {exc})"


# ---------------------------------------------------------------------------
# Mood classification — a lightweight local heuristic on the LLM's reply
# text, not a change to the LLM prompt/interface. Simpler and more
# predictable than asking every backend (Gemini/Claude) to self-tag in a
# JSON envelope, at the cost of being cruder than true sentiment analysis.
# ---------------------------------------------------------------------------

MOOD_KEYWORDS = {
    "happy": ["great", "awesome", "glad", "love", "wonderful", "fun", "yay", "!"],
    "concerned": ["sorry", "unfortunately", "can't", "cannot", "problem", "error", "worried"],
    "excited": ["let's", "amazing", "wow", "exciting"],
    "calm": ["okay", "sure", "certainly", "of course"],
}


def classify_mood(reply_text: str) -> str:
    lower = reply_text.lower()
    scores = {mood: sum(1 for kw in kws if kw in lower) for mood, kws in MOOD_KEYWORDS.items()}
    best_mood = max(scores, key=scores.get)
    return best_mood if scores[best_mood] > 0 else "neutral"


# ---------------------------------------------------------------------------
# Core chat handler — the actual "brain" logic, framework-agnostic so it
# can be unit tested (see simulate_full_run.py) without deploying anything.
# ---------------------------------------------------------------------------

class RobotBrainService:
    def __init__(self, llm_client=None, eyes=None, backend=None, robot_id=DEFAULT_ROBOT_ID):
        self.backend = backend or build_state_backend()
        self.robot_id = robot_id
        self.motion_queue = SimpleQueue(self.backend, robot_id, "motion")
        self.locomotion_queue = SimpleQueue(self.backend, robot_id, "locomotion")
        self.reply_queue = SimpleQueue(self.backend, robot_id, "reply")
        self._llm_client = llm_client  # injected stub for testing; real
                                        # deployment builds an anthropic.Anthropic()
        self.eyes = eyes  # optional EyeModule — status/mood calls are no-ops if None

    def handle_chat(self, message: str) -> dict:
        message = (message or "").strip()
        if not message:
            return {"error": "empty message"}

        if self.eyes:
            self.eyes.set_status("listening")

        self.backend.append_memory(self.robot_id, "you", message)

        # Gesture AND locomotion matching are both instant — enqueue both
        # immediately, before the slow LLM call, so movement of either
        # kind never waits on the network/model.
        gesture = match_gesture(message)
        if gesture:
            self.motion_queue.enqueue(gesture)

        locomotion = match_locomotion(message)
        if locomotion:
            self.locomotion_queue.enqueue(locomotion)

        tool_context = maybe_fetch_tool_context(message)

        system_prompt = (
            "You are the voice of a physical Meccanoid robot whose brain is "
            "an ESP32 talking to you over the cloud. Keep replies short and "
            "speakable aloud. Recent conversation:\n\n" + self.backend.recent_memory(self.robot_id)
        )
        if tool_context:
            system_prompt += f"\n\nLive tool result:\n{tool_context}"

        if self.eyes:
            self.eyes.set_status("thinking")  # instant, BEFORE the slow call below

        reply_text = self._call_llm(system_prompt, message)  # slow — network/model call
        self.backend.append_memory(self.robot_id, "robot", reply_text)
        self.reply_queue.enqueue(reply_text)

        mood = classify_mood(reply_text)
        if self.eyes:
            self.eyes.set_mood(mood)
            self.eyes.set_status("speaking")

        return {"reply": reply_text, "gesture": gesture, "locomotion": locomotion, "mood": mood}

    def _call_llm(self, system_prompt: str, message: str) -> str:
        if self._llm_client is not None:
            return self._llm_client.chat(system_prompt, message)
        # Lazily built so importing this module never requires an API key —
        # only actually calling _call_llm without an injected stub does.
        if not hasattr(self, "_real_backend"):
            self._real_backend = build_llm_backend(LLM_PROVIDER)
        return self._real_backend.chat(system_prompt, message)


# ---------------------------------------------------------------------------
# functions_framework entry points — this is what Firebase/Cloud
# Functions/Cloud Run actually invoke. A single shared service instance
# per deployed instance is fine here since each Meccanoid is one robot.
# ---------------------------------------------------------------------------

try:
    from eyes import EyeModule
    _eyes = EyeModule(simulate=True)
except ImportError:
    _eyes = None  # eyes.py not present — fine, RobotBrainService no-ops without it

_backend = build_state_backend()
_services = {}


def get_service(robot_id: str) -> RobotBrainService:
    robot_id = (robot_id or DEFAULT_ROBOT_ID).strip()
    if not robot_id or len(robot_id) > 64 or not all(
        char.isalnum() or char in "-_" for char in robot_id
    ):
        raise ValueError("invalid robot_id")
    if robot_id not in _services:
        _services[robot_id] = RobotBrainService(
            eyes=_eyes, backend=_backend, robot_id=robot_id
        )
    return _services[robot_id]


try:
    import functions_framework
    from flask import jsonify

    def _authorized(req):
        return (
            not ROBOT_API_TOKEN
            or req.headers.get("Authorization", "") == f"Bearer {ROBOT_API_TOKEN}"
        )

    def _request_service(req, body=None):
        body = body or {}
        robot_id = body.get("robot_id") or req.args.get("robot_id") or DEFAULT_ROBOT_ID
        return get_service(robot_id)

    @functions_framework.http
    def chat(req):
        if not _authorized(req):
            return jsonify({"error": "unauthorized"}), 401
        body = req.get_json(silent=True) or {}
        try:
            result = _request_service(req, body).handle_chat(body.get("message", ""))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(result)

    def _next(req, queue_name, value_key):
        if not _authorized(req):
            return jsonify({"error": "unauthorized"}), 401
        try:
            item = getattr(_request_service(req), f"{queue_name}_queue").next_undelivered()
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        if item is None:
            return jsonify({"pending": False})
        return jsonify({"pending": True, "id": item.id, value_key: item.value})

    def _ack(req, queue_name):
        if not _authorized(req):
            return jsonify({"error": "unauthorized"}), 401
        body = req.get_json(silent=True) or {}
        try:
            queue = getattr(_request_service(req, body), f"{queue_name}_queue")
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        ok = queue.ack(body.get("id", ""))
        return jsonify({"ok": ok}), (200 if ok else 404)

    @functions_framework.http
    def next_motion(req):
        return _next(req, "motion", "gesture")

    @functions_framework.http
    def ack_motion(req):
        return _ack(req, "motion")

    @functions_framework.http
    def next_locomotion(req):
        return _next(req, "locomotion", "locomotion")

    @functions_framework.http
    def ack_locomotion(req):
        return _ack(req, "locomotion")

    @functions_framework.http
    def next_reply(req):
        return _next(req, "reply", "reply")

    @functions_framework.http
    def ack_reply(req):
        return _ack(req, "reply")

except ImportError:
    # functions_framework isn't installed in this environment — fine for
    # local testing via simulate_full_run.py, which imports RobotBrainService
    # directly and never hits these HTTP wrappers.
    pass

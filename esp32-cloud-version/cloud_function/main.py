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
import re
import time
import uuid
import datetime
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import requests

from cloud_llm_backends import build_llm_backend

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "gemini")  # "gemini" or "claude"
WEATHER_LOCATION = os.environ.get("WEATHER_LOCATION", "")
MEMORY_BACKEND = os.environ.get("MEMORY_BACKEND", "memory")  # "memory" or "firestore"


# ---------------------------------------------------------------------------
# Memory — swap this class's internals for Firestore/Supabase in production.
# The rest of this file only calls append() / recent_text(), so that's the
# only class that needs to change.
# ---------------------------------------------------------------------------

class MemoryStore:
    # Caps growth on a long-lived warm Cloud Function instance — without a
    # bound, _log and the string recent_text() rebuilds every call both grow
    # unboundedly with conversation length. 500 turns is comfortably more
    # than recent_text()'s own 4000-char tail ever uses.
    MAX_ENTRIES = 500

    def __init__(self):
        self._log = deque(maxlen=self.MAX_ENTRIES)  # (timestamp, role, text)
                         # — replace with a Firestore collection or Supabase
                         # table in prod
        self._lock = threading.Lock()

    def append(self, role: str, text: str) -> None:
        with self._lock:
            self._log.append((datetime.datetime.now().isoformat(timespec="seconds"), role, text))

    def recent_text(self, max_chars: int = 4000) -> str:
        with self._lock:
            entries = list(self._log)
        blob = "\n".join(f"[{ts}] {role}: {text}" for ts, role, text in entries)
        return blob[-max_chars:]


def build_memory_store():
    """Picks the memory backend from MEMORY_BACKEND. Default 'memory' keeps
    local testing (simulate_full_run.py, test_real_modules.py) dependency-
    free; 'firestore' gives real cross-cold-start persistence in production
    — see firestore_memory.py's module docstring for setup. The Firestore
    import is lazy so it's only a hard dependency when actually selected.
    Re-reads the env var on every call (rather than the module-level
    default captured at import time) so tests can select a backend without
    needing to reimport this module."""
    backend = os.environ.get("MEMORY_BACKEND", MEMORY_BACKEND)
    if backend == "firestore":
        from firestore_memory import FirestoreMemoryStore
        return FirestoreMemoryStore()
    if backend == "memory":
        return MemoryStore()
    raise ValueError(f"Unknown MEMORY_BACKEND '{backend}' — use 'memory' or 'firestore'")


# ---------------------------------------------------------------------------
# Two separate queues — this is the actual fix for a real bug: gestures
# were previously only enqueued AFTER the slow LLM call finished, even
# though gesture matching itself is instant. That defeated the whole
# "move while thinking" premise from the original architecture. Splitting
# motion from reply lets the ESP32's poll loop see a queued gesture the
# moment handle_chat() computes it — well before the LLM call returns —
# since a concurrent poll request isn't blocked by the /chat request
# still running the LLM call.
# ---------------------------------------------------------------------------

@dataclass
class QueuedItem:
    id: str
    value: Optional[str]
    created_at: float = field(default_factory=time.time)
    delivered: bool = False


class SimpleQueue:
    """Thread-safe: /chat (enqueue) and /next_*+/ack_* (poll/ack) are
    designed to run concurrently — see module docstring — so mutation of
    _pending must be locked, not just relying on CPython's GIL to serialize
    individual list ops (which isn't a real guarantee, e.g. under a
    multi-process WSGI server)."""

    # Bounds memory growth and keeps next_undelivered()'s scan cheap on a
    # long-lived warm instance: delivered items are dropped once the
    # backlog exceeds this, instead of being kept forever.
    MAX_DELIVERED_BACKLOG = 100

    def __init__(self):
        self._pending: list[QueuedItem] = []
        self._lock = threading.Lock()

    def enqueue(self, value: Optional[str]) -> QueuedItem:
        item = QueuedItem(id=str(uuid.uuid4()), value=value)
        with self._lock:
            self._pending.append(item)
        return item

    def next_undelivered(self) -> Optional[QueuedItem]:
        with self._lock:
            for item in self._pending:
                if not item.delivered:
                    return item
        return None

    def ack(self, item_id: str) -> None:
        with self._lock:
            for item in self._pending:
                if item.id == item_id:
                    item.delivered = True
            self._prune_locked()

    def _prune_locked(self) -> None:
        delivered = [i for i in self._pending if i.delivered]
        if len(delivered) > self.MAX_DELIVERED_BACKLOG:
            excess = len(delivered) - self.MAX_DELIVERED_BACKLOG
            drop_ids = {i.id for i in delivered[:excess]}
            self._pending = [i for i in self._pending if i.id not in drop_ids]


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


def _phrase_matches(phrase: str, lower_text: str) -> bool:
    """Word-boundary match, not a bare substring test — otherwise 'bow'
    would fire inside 'rainbow'/'elbow', 'sit down' inside 'visit downtown',
    'come here' inside 'welcome here', etc."""
    return re.search(r"\b" + re.escape(phrase) + r"\b", lower_text) is not None


def match_locomotion(text: str) -> Optional[str]:
    """Same longest-match specificity rule as match_gesture below."""
    lower = text.lower()
    best_name = None
    best_len = -1
    for name, phrases in LOCOMOTION_TRIGGERS.items():
        for p in phrases:
            if _phrase_matches(p, lower) and len(p) > best_len:
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
            if _phrase_matches(p, lower) and len(p) > best_len:
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
    def __init__(self, llm_client=None, eyes=None, memory=None):
        self.memory = memory if memory is not None else build_memory_store()
        self.motion_queue = SimpleQueue()      # arm gestures
        self.locomotion_queue = SimpleQueue()  # wheel drive commands
        self.reply_queue = SimpleQueue()
        self._llm_client = llm_client  # injected stub for testing; real
                                        # deployment builds an anthropic.Anthropic()
        self.eyes = eyes  # optional EyeModule — status/mood calls are no-ops if None

    def handle_chat(self, message: str) -> dict:
        message = (message or "").strip()
        if not message:
            return {"error": "empty message"}

        if self.eyes:
            self.eyes.set_status("listening")

        self.memory.append("you", message)

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
            "speakable aloud. Recent conversation:\n\n" + self.memory.recent_text()
        )
        if tool_context:
            system_prompt += f"\n\nLive tool result:\n{tool_context}"

        if self.eyes:
            self.eyes.set_status("thinking")  # instant, BEFORE the slow call below

        try:
            reply_text = self._call_llm(system_prompt, message)  # slow — network/model call
        except Exception as exc:
            # Mirrors brain.py's error handling: don't leave the robot
            # having moved/turned but never having "spoken" — surface a
            # clear error instead of an unhandled 500 with no reply_queue
            # entry at all.
            return {"error": f"LLM call failed: {exc}", "gesture": gesture, "locomotion": locomotion}

        self.memory.append("robot", reply_text)
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

_service = RobotBrainService(eyes=_eyes)

try:
    import functions_framework
    from flask import jsonify, request

    @functions_framework.http
    def chat(req):
        body = req.get_json(silent=True) or {}
        result = _service.handle_chat(body.get("message", ""))
        if "error" in result:
            return jsonify(result), 502
        return jsonify(result)

    @functions_framework.http
    def next_motion(req):
        """Fast, frequent poll target — only ever contains a gesture name,
        never waits on the LLM."""
        item = _service.motion_queue.next_undelivered()
        if item is None:
            return jsonify({"pending": False})
        return jsonify({"pending": True, "id": item.id, "gesture": item.value})

    @functions_framework.http
    def ack_motion(req):
        body = req.get_json(silent=True) or {}
        _service.motion_queue.ack(body.get("id", ""))
        return jsonify({"ok": True})

    @functions_framework.http
    def next_reply(req):
        """Separate, can be polled less aggressively — only ever contains
        text, for logging/TTS/display, arrives after the LLM finishes."""
        item = _service.reply_queue.next_undelivered()
        if item is None:
            return jsonify({"pending": False})
        return jsonify({"pending": True, "id": item.id, "reply": item.value})

    @functions_framework.http
    def ack_reply(req):
        body = req.get_json(silent=True) or {}
        _service.reply_queue.ack(body.get("id", ""))
        return jsonify({"ok": True})

except ImportError:
    # functions_framework isn't installed in this environment — fine for
    # local testing via simulate_full_run.py, which imports RobotBrainService
    # directly and never hits these HTTP wrappers.
    pass

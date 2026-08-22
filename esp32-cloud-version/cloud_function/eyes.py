"""
eyes.py
-------
The Meccanoid's LED eye module — confirmed real hardware, same "smart
module" daisy-chain family as the arm servos (see
alexfrederiksen/MeccanoidForArduino's MeccanoLed class). Not guesswork:
this is documented in Meccano's own Smart Module Protocol.

Real capability, and its real limit: color (RGB, 0-7 brightness per
channel) and fade timing (0-7, immediate to ~4 seconds) — that's it.
No shape, no screen, no actual "expression." Personality here comes
entirely from *when* colors change and *how fast*, not from any visual
form.

Two uses built here:
  1. STATUS colors — idle/listening/thinking/speaking, so the currently
     invisible "waiting on the cloud LLM" moment has visual feedback
  2. MOOD colors — an optional tag from the LLM's reply, so a happy
     reply looks different from a concerned one

Both queue instantly, same non-blocking principle as gestures/locomotion
elsewhere in this project — status should never wait on the LLM call
that it's representing the wait for.
"""

import time
from typing import Tuple, Optional

# (r, g, b) each 0-7, matching real hardware's brightness levels
STATUS_COLORS = {
    "idle":      (0, 0, 4),   # dim blue — powered, waiting
    "listening": (0, 5, 0),   # green — heard something, processing locally
    "thinking":  (5, 3, 0),   # amber — cloud/LLM call in flight
    "speaking":  (0, 5, 5),   # cyan — reply ready, "talking"
    "error":     (6, 0, 0),   # red — something failed
}

MOOD_COLORS = {
    "happy":     (6, 5, 0),   # warm yellow
    "calm":      (0, 3, 6),   # soft blue
    "excited":   (6, 0, 4),   # pink/magenta
    "concerned": (6, 2, 0),   # orange
    "neutral":   (3, 3, 3),   # warm white
}


# Gesture cues are deliberately single, non-blocking color/fade commands.
# The body engine owns timing; a real LED transport can apply the same cue
# immediately when the matching gesture is queued.
GESTURE_EYE_CUES = {
    "dab": ((7, 7, 7), 0, 1.5),
    "flex": ((6, 5, 0), 1, 1.8),
    "floss": ((6, 0, 5), 0, 2.0),
    "the_robot": ((0, 6, 6), 0, 2.0),
    "mic_drop": ((7, 7, 7), 0, 1.7),
    "finger_guns": ((0, 6, 6), 0, 1.4),
    "aura_farm": ((5, 0, 7), 6, 2.0),
    "six_seven": ((0, 7, 7), 1, 1.5),
    "npc_mode": ((0, 7, 0), 0, 1.6),
    "facepalm": ((7, 0, 0), 2, 1.7),
    "success_pump": ((7, 6, 0), 0, 1.6),
    "side_eye": ((6, 2, 0), 4, 1.7),
}

# fade speed: 0 = instant, 7 = ~4 second transition (per real hardware)
FADE_INSTANT = 0
FADE_SLOW = 6


class EyeModule:
    """Simulated LED module — no hardware, prints [SIM/eyes] like the
    other simulated modules in this project."""

    def __init__(self, simulate: bool = True):
        self.simulate = simulate
        self.current_color: Tuple[int, int, int] = (0, 0, 0)
        self.history = []
        self._t0 = time.perf_counter()
        self._gesture_cue_until = 0.0

    def set_color(self, r: int, g: int, b: int, fade: int = FADE_INSTANT):
        r, g, b, fade = (max(0, min(7, v)) for v in (r, g, b, fade))
        self.current_color = (r, g, b)
        elapsed = time.perf_counter() - self._t0
        self.history.append((elapsed, (r, g, b), fade))
        if self.simulate:
            print(f"[SIM/eyes] t={elapsed:.3f}s color=({r},{g},{b}) fade={fade}")

    def _cue_active(self) -> bool:
        return time.perf_counter() < self._gesture_cue_until

    def set_status(self, status: str, fade: int = FADE_INSTANT):
        if status not in STATUS_COLORS:
            raise ValueError(f"unknown status {status!r} — use one of {list(STATUS_COLORS)}")
        if self._cue_active() and status != "error":
            return
        self.set_color(*STATUS_COLORS[status], fade=fade)

    def set_mood(self, mood: str, fade: int = FADE_SLOW):
        if self._cue_active():
            return
        if mood not in MOOD_COLORS:
            mood = "neutral"
        self.set_color(*MOOD_COLORS[mood], fade=fade)

    def set_gesture_cue(self, gesture: str) -> Optional[str]:
        cue = GESTURE_EYE_CUES.get(gesture)
        if cue is None:
            return None
        color, fade, lease_s = cue
        self.set_color(*color, fade=fade)
        self._gesture_cue_until = time.perf_counter() + lease_s
        return gesture

    def blink(self, color: Tuple[int, int, int], times: int = 2, on_s: float = 0.15, off_s: float = 0.1):
        for _ in range(times):
            self.set_color(*color, fade=FADE_INSTANT)
            time.sleep(on_s)
            self.set_color(0, 0, 0, fade=FADE_INSTANT)
            time.sleep(off_s)


if __name__ == "__main__":
    eyes = EyeModule()
    print("--- status sequence ---")
    eyes.set_status("idle")
    eyes.set_status("listening")
    eyes.set_status("thinking")
    time.sleep(0.05)
    eyes.set_status("speaking")
    eyes.set_status("idle", fade=FADE_SLOW)

    print("\n--- mood colors ---")
    for mood in MOOD_COLORS:
        eyes.set_mood(mood)

    print("\n--- error blink ---")
    eyes.blink(STATUS_COLORS["error"], times=3)

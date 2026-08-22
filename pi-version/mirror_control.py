"""Safe live pose mirroring for the four-arm-servo Personal Robot 2.0.

Also carries two optional, deliberately separate side-channels from the
same /mirror_pose feed, each with its own (looser) rate limit since
neither is safety-critical the way live arm angles are:
  - apply_mood(): forwards a face-expression tag to the eye LEDs
  - apply_locomotion(): drives the wheels in short, self-bounding pulses
    for forward-only "follow mode" (see docs/index.html's crossed-arms detection) —
    never a continuous/open-ended motor command.
"""
import math
import threading
import time
from typing import Dict, Mapping, Optional

JOINT_TO_SERVO = {
    "right_shoulder": 0, "right_elbow": 1,
    "left_shoulder": 2, "left_elbow": 3,
}
REST = {0: 90, 1: 90, 2: 90, 3: 90}
LOCOMOTION_ACTIONS = {"forward", "stop"}

class MirrorController:
    """Rate-, range-, and step-limited live targets with a dead-man reset."""
    MIN_INTERVAL_S = 0.05
    DEADMAN_S = 0.75
    MAX_STEP_DEG = 12
    MIN_ANGLE = 15
    MAX_ANGLE = 165

    # Mood/locomotion are cosmetic/coarse compared to arm angles, so they
    # get their own looser rate limits rather than sharing the 20Hz arm
    # limit — no need to re-command an unchanged LED color or motor pulse
    # every single poll.
    MOOD_MIN_INTERVAL_S = 0.3
    LOCOMOTION_MIN_INTERVAL_S = 0.5
    LOCOMOTION_SPEED = 150   # gentler than locomotion.py's default 200 —
                             # this is live teleop, not a scripted routine
    LOCOMOTION_PULSE_S = 0.4  # each call is a short, self-bounding pulse;
                              # DriveMotors._run() stops the motors itself
                              # when the pulse ends, so there's no separate
                              # locomotion watchdog needed the way the arm
                              # angles above need one

    def __init__(self, bus, eyes=None, drive=None):
        self.bus = bus
        self.eyes = eyes    # optional EyeModule — apply_mood() no-ops without one
        self.drive = drive  # optional DriveMotors — apply_locomotion() no-ops without one
        self._lock = threading.Lock()
        self._side_lock = threading.Lock()
        self._last_update = 0.0
        self._last_targets = dict(REST)
        self._rested = True
        self._closed = threading.Event()
        self._watchdog = threading.Thread(target=self._watchdog_loop, daemon=True)
        self._watchdog.start()
        self._last_mood_update = 0.0
        self._last_locomotion_update = 0.0
        self._last_locomotion = "stop"

    def apply(self, joints: Mapping[str, object]) -> Dict[int, int]:
        now = time.monotonic()
        with self._lock:
            if now - self._last_update < self.MIN_INTERVAL_S:
                raise RuntimeError("pose updates are limited to 20 Hz")
            requested = {}
            if set(joints) != set(JOINT_TO_SERVO):
                raise ValueError("all four named arm joints are required")
            for name, servo_id in JOINT_TO_SERVO.items():
                value = joints[name]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError(f"{name} must be numeric")
                target = max(self.MIN_ANGLE, min(self.MAX_ANGLE, round(value)))
                previous = self._last_targets[servo_id]
                target = max(previous - self.MAX_STEP_DEG, min(previous + self.MAX_STEP_DEG, target))
                requested[servo_id] = target
            self.bus.set_angles(requested)
            self._last_targets = requested
            self._last_update = now
            self._rested = False
            return dict(requested)

    def apply_mood(self, mood: Optional[str]) -> Optional[str]:
        """Forwards a face-expression tag to the eye LEDs. Returns the
        mood actually applied, or None if skipped (no eyes wired up,
        no mood given, or rate-limited — the caller doesn't need to
        treat a skip as an error, unlike apply()'s strict validation,
        since a stale eye color is harmless)."""
        if not self.eyes or not mood:
            return None
        with self._side_lock:
            now = time.monotonic()
            if now - self._last_mood_update < self.MOOD_MIN_INTERVAL_S:
                return None
            self._last_mood_update = now
            self.eyes.set_mood(mood)  # eyes.py itself falls back to "neutral" for unknown moods
            return mood

    def apply_locomotion(self, action: Optional[str]) -> Optional[str]:
        """Drives the wheels in a single short, self-bounding pulse for
        "follow mode." Never a continuous command — DriveMotors._run()
        stops the motors itself once LOCOMOTION_PULSE_S elapses, so a
        client that stops polling simply stops getting new pulses rather
        than needing a separate watchdog to catch a runaway motor."""
        if not self.drive or action not in LOCOMOTION_ACTIONS:
            return None
        with self._side_lock:
            now = time.monotonic()
            if action == self._last_locomotion and now - self._last_locomotion_update < self.LOCOMOTION_MIN_INTERVAL_S:
                return None
            self._last_locomotion_update = now
            self._last_locomotion = action
            if action == "forward":
                self.drive.forward(speed=self.LOCOMOTION_SPEED, duration_s=self.LOCOMOTION_PULSE_S)
            else:
                self.drive.stop()
            return action

    @property
    def active(self) -> bool:
        with self._lock:
            return not self._rested

    def _watchdog_loop(self):
        while not self._closed.wait(0.1):
            with self._lock:
                if not self._rested and time.monotonic() - self._last_update > self.DEADMAN_S:
                    self.bus.set_angles(REST)
                    self._last_targets = dict(REST)
                    self._rested = True

    def close(self):
        self._closed.set()
        self._watchdog.join(timeout=1)
        with self._lock:
            if not self._rested:
                self.bus.set_angles(REST)
                self._last_targets = dict(REST)
                self._rested = True
        if self.drive:
            self.drive.stop()

"""Rate-limited, fail-safe direct pose control for camera mirroring."""

from __future__ import annotations
import math
import threading
import time

JOINTS = {
    "right_shoulder": 0,
    "right_elbow": 1,
    "left_shoulder": 2,
    "left_elbow": 3,
}
REST = {0: 90, 1: 90, 2: 90, 3: 90}


class MirrorController:
    MIN_INTERVAL_S = 0.05
    DEADMAN_S = 0.75
    MAX_STEP_DEG = 12
    MIN_ANGLE = 15
    MAX_ANGLE = 165

    def __init__(self, bus):
        self.bus = bus
        self._lock = threading.Lock()
        self._last_update = 0.0
        self._angles = dict(REST)
        self._armed = False
        self._closed = threading.Event()
        self._watchdog = threading.Thread(target=self._watch, daemon=True)
        self._watchdog.start()

    def apply(self, pose: dict) -> dict | None:
        if not isinstance(pose, dict) or set(pose) != set(JOINTS):
            raise ValueError(f"pose must contain exactly: {', '.join(JOINTS)}")
        requested = {}
        for name, value in pose.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number")
            requested[JOINTS[name]] = round(max(self.MIN_ANGLE, min(self.MAX_ANGLE, value)))

        now = time.monotonic()
        with self._lock:
            if now - self._last_update < self.MIN_INTERVAL_S:
                return None
            applied = {
                servo: max(old - self.MAX_STEP_DEG, min(old + self.MAX_STEP_DEG, requested[servo]))
                for servo, old in self._angles.items()
            }
            self.bus.set_angles(applied)
            self._angles = applied
            self._last_update = now
            self._armed = True
        return {name: applied[servo] for name, servo in JOINTS.items()}

    @property
    def active(self) -> bool:
        with self._lock:
            return self._armed

    def _watch(self):
        while not self._closed.wait(0.05):
            with self._lock:
                if self._armed and time.monotonic() - self._last_update >= self.DEADMAN_S:
                    self.bus.set_angles(REST)
                    self._angles = dict(REST)
                    self._armed = False

    def close(self):
        self._closed.set()
        self._watchdog.join(timeout=1)
        with self._lock:
            if self._armed:
                self.bus.set_angles(REST)
                self._armed = False
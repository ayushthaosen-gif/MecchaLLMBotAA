"""Safe live pose mirroring for the four-arm-servo Personal Robot 2.0."""
import math
import threading
import time
from typing import Dict, Mapping

JOINT_TO_SERVO = {
    "right_shoulder": 0, "right_elbow": 1,
    "left_shoulder": 2, "left_elbow": 3,
}
REST = {0: 90, 1: 90, 2: 90, 3: 90}

class MirrorController:
    """Rate-, range-, and step-limited live targets with a dead-man reset."""
    MIN_INTERVAL_S = 0.05
    DEADMAN_S = 0.75
    MAX_STEP_DEG = 12
    MIN_ANGLE = 15
    MAX_ANGLE = 165

    def __init__(self, bus):
        self.bus = bus
        self._lock = threading.Lock()
        self._last_update = 0.0
        self._last_targets = dict(REST)
        self._rested = True
        self._closed = threading.Event()
        self._watchdog = threading.Thread(target=self._watchdog_loop, daemon=True)
        self._watchdog.start()

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

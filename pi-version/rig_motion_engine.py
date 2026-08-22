"""
rig_motion_engine.py
----------------------
Non-blocking gesture playback for the real 3-chain, 8-servo rig
(rig.py) — same interpolation/easing/speed-cap approach as
motion_engine.py, generalized to work across joint names spanning
multiple independent buses instead of a single flat servo list.
"""

import queue
import threading
import time
from typing import Optional, Dict, List, Tuple

from rig import MeccanoidRig


def _ease_in_out(t: float) -> float:
    return t * t * (3 - 2 * t)


# A gesture is a list of (partial_joint_angles, hold_seconds) keyframes.
# Joints NOT mentioned in a keyframe simply stay wherever they already
# are — gestures don't need to specify all 8 servos every time, which is
# what makes combining e.g. an arm gesture with an independent head
# movement possible.
Keyframe = Tuple[Dict[str, int], float]
Gesture = List[Keyframe]


class RigMotionEngine:
    DEFAULT_MAX_SPEED = 220.0
    STEP_INTERVAL = 0.02
    # See motion_engine.py's DEAD_BAND_DEG — same rationale, ported here.
    DEAD_BAND_DEG = 1.5

    def __init__(self, rig: MeccanoidRig, max_speed_deg_per_sec: float = DEFAULT_MAX_SPEED):
        self.rig = rig
        self.max_speed = max_speed_deg_per_sec
        self._queue: "queue.Queue[Gesture]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._stop_event = threading.Event()
        self._busy = threading.Event()
        self.on_frame = None  # optional callback(elapsed, {joint: angle}) for instrumentation

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout=2)

    def play(self, gesture: Gesture) -> bool:
        if not self._running:
            return False
        self._queue.put(gesture)
        return True

    @property
    def is_busy(self) -> bool:
        return self._busy.is_set()

    def _worker(self) -> None:
        while self._running:
            try:
                gesture = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if gesture is None:
                break
            self._busy.set()
            try:
                for target_joints, hold_seconds in gesture:
                    if self._stop_event.is_set():
                        break
                    self._move_to(target_joints, hold_seconds)
            except Exception as exc:
                print(f"[rig_motion_engine] gesture failed: {exc}")
            finally:
                self._busy.clear()

    def _move_to(self, target_joints: Dict[str, int], hold_seconds: float) -> None:
        start_joints = {j: self.rig.get_joint_angle(j) for j in target_joints}
        max_delta = max(
            (abs(target_joints[j] - start_joints[j]) for j in target_joints), default=0
        )

        if max_delta < self.DEAD_BAND_DEG:
            self.rig.set_joint_angles(dict(target_joints))
            if self.on_frame:
                self.on_frame(0.0, dict(target_joints))
            self._stop_event.wait(max(hold_seconds, 0.05))
            return

        speed_duration = max_delta / self.max_speed if self.max_speed > 0 else 0
        duration = max(speed_duration, hold_seconds, 0.05)
        steps = max(int(duration / self.STEP_INTERVAL), 1)

        t0 = time.perf_counter()
        for step in range(1, steps + 1):
            if self._stop_event.is_set():
                return
            t = _ease_in_out(step / steps)
            frame = {
                j: round(start_joints[j] + (target_joints[j] - start_joints[j]) * t)
                for j in target_joints
            }
            self.rig.set_joint_angles(frame)
            if self.on_frame:
                self.on_frame(time.perf_counter() - t0, dict(frame))
            if self._stop_event.wait(self.STEP_INTERVAL):
                return

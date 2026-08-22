"""
motion_engine.py
-----------------
Runs gestures on a background thread so the robot can wave/dance while
the main thread is blocked waiting on a Claude API response or a web
search. This is the piece that makes movement "non-blocking."

Usage:
    engine = MotionEngine(servo_bus)
    engine.start()
    engine.play("wave")          # returns immediately, plays in background
    ...
    engine.play("dance")         # queued: plays after "wave" finishes
    engine.stop()                # clean shutdown
"""

import queue
import threading
import time
from typing import Optional

from servo_controller import ServoBus
from gestures import GESTURES


def _ease_in_out(t: float) -> float:
    """Smoothstep easing: slow-fast-slow instead of constant speed, so
    movement looks less like a stepper motor and more like a joint."""
    return t * t * (3 - 2 * t)


class MotionEngine:
    # Cap on how fast any single servo is allowed to move, in degrees/second.
    # Lower = smoother/gentler, higher = snappier. Tune to your servos.
    DEFAULT_MAX_SPEED = 220.0
    # How often we send an updated position while interpolating. Lower =
    # smoother motion but more packets on the bus.
    STEP_INTERVAL = 0.02  # seconds

    def __init__(self, bus: ServoBus, max_speed_deg_per_sec: float = DEFAULT_MAX_SPEED):
        self.bus = bus
        self.max_speed = max_speed_deg_per_sec
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._busy = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self._queue.put("__STOP__")
        if self._thread:
            self._thread.join(timeout=2)

    def play(self, gesture_name: str) -> bool:
        """Queue a gesture by name. Returns False if the name is unknown."""
        if gesture_name not in GESTURES and gesture_name != "__STOP__":
            return False
        self._queue.put(gesture_name)
        return True

    @property
    def is_busy(self) -> bool:
        return self._busy.is_set()

    # -- worker thread ------------------------------------------------------

    def _worker(self) -> None:
        while self._running:
            try:
                gesture_name = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if gesture_name == "__STOP__":
                break

            self._busy.set()
            try:
                self._run_gesture(gesture_name)
            except Exception as exc:  # keep the thread alive on servo errors
                print(f"[motion_engine] gesture '{gesture_name}' failed: {exc}")
            finally:
                self._busy.clear()

    def _run_gesture(self, gesture_name: str) -> None:
        for angles, hold_seconds in GESTURES[gesture_name]:
            self._move_to(angles, hold_seconds)

    def _move_to(self, target_angles: dict, hold_seconds: float) -> None:
        """Smoothly interpolate every named servo from its current angle to
        the target, instead of snapping — this is the main "make it look
        better" lever. Duration is whichever is larger: the time the
        farthest-moving servo needs at max_speed, or the gesture's own
        hold_seconds (so slow deliberate keyframes aren't rushed)."""
        start_angles = {sid: self.bus.get_angle(sid) for sid in target_angles}
        max_delta = max(
            (abs(target_angles[sid] - start_angles[sid]) for sid in target_angles),
            default=0,
        )
        speed_duration = max_delta / self.max_speed if self.max_speed > 0 else 0
        duration = max(speed_duration, hold_seconds, 0.05)

        steps = max(int(duration / self.STEP_INTERVAL), 1)
        for step in range(1, steps + 1):
            t = _ease_in_out(step / steps)
            frame = {
                sid: round(start_angles[sid] + (target_angles[sid] - start_angles[sid]) * t)
                for sid in target_angles
            }
            self.bus.set_angles(frame)
            time.sleep(self.STEP_INTERVAL)


# ---------------------------------------------------------------------------
# Manual test: `python motion_engine.py`
# Demonstrates that queuing "dance" right after "wave" plays both in order
# without blocking the main thread.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from servo_controller import ServoBusConfig

    bus = ServoBus(ServoBusConfig(simulate=True, servo_count=4))
    engine = MotionEngine(bus)
    engine.start()

    print("Queuing wave, then dance — main thread stays free the whole time.")
    engine.play("wave")
    engine.play("dance")

    start = time.time()
    while engine.is_busy or not engine._queue.empty():
        print(f"  main thread doing other work at t={time.time()-start:.1f}s")
        time.sleep(0.5)

    engine.stop()
    print("All queued gestures finished.")

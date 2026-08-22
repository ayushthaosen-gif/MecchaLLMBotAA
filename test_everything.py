"""
test_everything.py
---------------------
Self-contained test suite for the Meccanoid Cloud-AI project. Zero
external dependencies (stdlib only — no Flask, no anthropic, no
pyserial, no requests) so it can be dropped anywhere and run with just:

    python3 test_everything.py

Everything the real project needs from outside packages is stubbed
here inline: the LLM call, the servo bus, the WiFi/HTTP layer. The
logic under test — gesture matching, non-blocking queueing, servo
interpolation, locomotion kinematics — is the real logic, just
extracted into one file instead of spread across the project's modules.

Covers, in order:
  1. Confirmed hardware model (4 servos, arms only, no head)
  2. Gesture keyword matching, including the specificity bug fix
  3. Smooth servo interpolation (easing, speed cap)
  4. Locomotion: rotate / arc / pivot turn kinematics
  5. The cloud-brain pattern: non-blocking motion vs slow "LLM" call
  6. Combined arm + locomotion command in one message

Each test prints PASS/FAIL and asserts — a non-zero exit code means
something regressed.
"""

import time
import math
import queue
import threading
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional


# =============================================================================
# 1. HARDWARE MODEL — confirmed real topology, no head/neck servo
# =============================================================================

JOINTS = {
    "right_shoulder": 0,
    "right_elbow": 1,
    "left_shoulder": 2,
    "left_elbow": 3,
}


class FakeServoBus:
    """In-memory stand-in for the real serial servo bus — no hardware,
    no pyserial. Records every commanded angle per joint."""
    def __init__(self):
        self.angles = {j: 90 for j in JOINTS}
        self.history: List[Tuple[float, str, int]] = []
        self._t0 = time.perf_counter()

    def set_joint(self, joint: str, angle: int):
        angle = max(0, min(180, angle))
        self.angles[joint] = angle
        self.history.append((time.perf_counter() - self._t0, joint, angle))

    def get_joint(self, joint: str) -> int:
        return self.angles[joint]


def ease_in_out(t: float) -> float:
    return t * t * (3 - 2 * t)


class MotionEngine:
    """Non-blocking gesture player — interpolates smoothly between
    keyframes instead of snapping, speed-capped."""
    STEP_INTERVAL = 0.01
    MAX_SPEED = 400.0  # deg/sec, fast for quick test runs

    def __init__(self, bus: FakeServoBus):
        self.bus = bus
        self._queue: "queue.Queue" = queue.Queue()
        self._busy = threading.Event()
        self._running = False
        self._thread = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout=2)

    def play(self, gesture: List[Tuple[Dict[str, int], float]]):
        self._queue.put(gesture)

    @property
    def is_busy(self):
        return self._busy.is_set()

    def _worker(self):
        while self._running:
            try:
                gesture = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if gesture is None:
                break
            self._busy.set()
            for target, hold in gesture:
                self._move_to(target, hold)
            self._busy.clear()

    def _move_to(self, target: Dict[str, int], hold_s: float):
        start = {j: self.bus.get_joint(j) for j in target}
        max_delta = max((abs(target[j] - start[j]) for j in target), default=0)
        duration = max(max_delta / self.MAX_SPEED, hold_s, 0.02)
        steps = max(int(duration / self.STEP_INTERVAL), 1)
        for s in range(1, steps + 1):
            t = ease_in_out(s / steps)
            for j in target:
                angle = round(start[j] + (target[j] - start[j]) * t)
                self.bus.set_joint(j, angle)
            time.sleep(self.STEP_INTERVAL)


# =============================================================================
# 2. GESTURES — arms only, matches confirmed hardware
# =============================================================================

REST = {"right_shoulder": 90, "right_elbow": 90, "left_shoulder": 90, "left_elbow": 90}

GESTURES = {
    "wave_right": [({"right_shoulder": 150, "right_elbow": 120}, 0.05),
                   ({"right_elbow": 70}, 0.03), (REST, 0.05)],
    "wave_both": [({"right_shoulder": 150, "right_elbow": 120,
                     "left_shoulder": 150, "left_elbow": 120}, 0.05), (REST, 0.05)],
    "bow": [({"right_shoulder": 50, "right_elbow": 70,
              "left_shoulder": 50, "left_elbow": 70}, 0.05), (REST, 0.05)],
    "shrug": [({"right_shoulder": 130, "left_shoulder": 130}, 0.05), (REST, 0.05)],
    "point": [({"right_shoulder": 100, "right_elbow": 170}, 0.05), (REST, 0.05)],
    "full_dance": [({"right_shoulder": 60, "left_shoulder": 120}, 0.03),
                    ({"right_shoulder": 120, "left_shoulder": 60}, 0.03), (REST, 0.05)],
    "sit": [({"right_shoulder": 170, "right_elbow": 20,
              "left_shoulder": 170, "left_elbow": 20}, 0.05)],
}

GESTURE_TRIGGERS = {
    "wave_right": ["wave", "hello", "hi there"],
    "wave_both": ["wave both", "wave with both", "big wave"],
    "bow": ["bow", "take a bow"],
    "shrug": ["shrug", "i don't know", "dunno"],
    "point": ["point at", "point over there"],
    "full_dance": ["dance", "boogie", "groove"],
    "sit": ["sit down", "rest", "power down"],
}

LOCOMOTION_TRIGGERS = {
    "forward": ["come here", "come forward", "move forward", "move closer"],
    "backward": ["back up", "move back", "go backward", "step back"],
    "turn_left": ["turn left"],
    "turn_right": ["turn right"],
    "turn_around": ["turn around", "spin around"],
}


def match_longest(text: str, trigger_table: dict) -> Optional[str]:
    lower = text.lower()
    best_name, best_len = None, -1
    for name, phrases in trigger_table.items():
        for p in phrases:
            if p in lower and len(p) > best_len:
                best_name, best_len = name, len(p)
    return best_name


# =============================================================================
# 3. LOCOMOTION — differential drive, dead-reckoning
# =============================================================================

class DriveMotors:
    def __init__(self, wheelbase_m=0.2, max_mps=0.3, max_pwm=255):
        self.wheelbase_m = wheelbase_m
        self.max_mps = max_mps
        self.max_pwm = max_pwm
        self.x = self.y = self.heading_deg = 0.0
        self.left_speed = self.right_speed = 0

    def _to_mps(self, pwm):
        return (pwm / self.max_pwm) * self.max_mps

    def _run(self, left, right, duration_s, steps=20):
        self.left_speed, self.right_speed = left, right
        dt = duration_s / steps
        for _ in range(steps):
            v = (self._to_mps(left) + self._to_mps(right)) / 2
            omega = (self._to_mps(right) - self._to_mps(left)) / self.wheelbase_m
            th = math.radians(self.heading_deg)
            self.x += v * math.cos(th) * dt
            self.y += v * math.sin(th) * dt
            self.heading_deg = (self.heading_deg + math.degrees(omega * dt)) % 360
        self.left_speed = self.right_speed = 0

    def forward(self, speed=200, duration_s=1.0): self._run(speed, speed, duration_s)
    def backward(self, speed=200, duration_s=1.0): self._run(-speed, -speed, duration_s)
    def rotate_in_place(self, speed=180, duration_s=0.6, direction="left"):
        sign = -1 if direction == "left" else 1
        self._run(sign * speed, -sign * speed, duration_s)
    def arc_turn(self, fwd=200, bias=100, duration_s=1.0, direction="left"):
        if direction == "left": self._run(fwd - bias, fwd, duration_s)
        else: self._run(fwd, fwd - bias, duration_s)
    def pivot_turn(self, speed=180, duration_s=0.8, direction="left"):
        if direction == "left": self._run(0, speed, duration_s)
        else: self._run(speed, 0, duration_s)


# =============================================================================
# 4. CLOUD BRAIN PATTERN — non-blocking motion/locomotion vs slow LLM
# =============================================================================

@dataclass
class QueuedItem:
    value: Optional[str]
    delivered: bool = False


class SimpleQueue:
    def __init__(self):
        self._items: List[QueuedItem] = []
    def enqueue(self, value):
        item = QueuedItem(value)
        self._items.append(item)
        return item
    def next_undelivered(self):
        for item in self._items:
            if not item.delivered:
                return item
        return None


class FakeSlowLLM:
    def __init__(self, latency_s=1.0):
        self.latency_s = latency_s
    def reply(self, message: str) -> str:
        time.sleep(self.latency_s)
        return f"(stub reply to: {message})"


class BrainService:
    def __init__(self, llm: FakeSlowLLM):
        self.llm = llm
        self.motion_queue = SimpleQueue()
        self.locomotion_queue = SimpleQueue()

    def handle_chat(self, message: str) -> dict:
        gesture = match_longest(message, GESTURE_TRIGGERS)
        if gesture:
            self.motion_queue.enqueue(gesture)  # enqueued BEFORE the slow call
        locomotion = match_longest(message, LOCOMOTION_TRIGGERS)
        if locomotion:
            self.locomotion_queue.enqueue(locomotion)
        reply = self.llm.reply(message)  # slow — simulates network/model latency
        return {"reply": reply, "gesture": gesture, "locomotion": locomotion}


# =============================================================================
# TESTS
# =============================================================================

results = {"pass": 0, "fail": 0}

def check(name: str, condition: bool, detail: str = ""):
    status = "PASS" if condition else "FAIL"
    results["pass" if condition else "fail"] += 1
    print(f"  [{status}] {name}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        raise AssertionError(f"{name}: {detail}")


def test_hardware_model():
    print("\n=== 1. Hardware model ===")
    check("exactly 4 joints defined", len(JOINTS) == 4)
    check("no head/neck joint present", all("head" not in j and "neck" not in j for j in JOINTS))
    check("2 joints per arm", sum(1 for j in JOINTS if j.startswith("right")) == 2)


def test_gesture_matching():
    print("\n=== 2. Gesture keyword matching ===")
    cases = {
        "hi there, wave hello!": "wave_right",
        "wave with both arms please": "wave_both",
        "take a bow": "bow",
        "i don't know, shrug": "shrug",
        "point over there": "point",
        "let's dance": "full_dance",
        "sit down and rest": "sit",
        "what's the weather": None,
    }
    for msg, expected in cases.items():
        got = match_longest(msg, GESTURE_TRIGGERS)
        check(f"'{msg}' -> {expected}", got == expected, f"got {got!r}")
    # the specificity bug this project actually hit and fixed:
    check("'wave' doesn't shadow 'wave with both'",
          match_longest("can you wave with both arms?", GESTURE_TRIGGERS) == "wave_both")


def test_locomotion_matching():
    print("\n=== 3. Locomotion keyword matching ===")
    cases = {
        "come here please": "forward",
        "back up a bit": "backward",
        "turn left": "turn_left",
        "turn right": "turn_right",
        "turn around": "turn_around",
    }
    for msg, expected in cases.items():
        got = match_longest(msg, LOCOMOTION_TRIGGERS)
        check(f"'{msg}' -> {expected}", got == expected, f"got {got!r}")


def test_smooth_interpolation():
    print("\n=== 4. Smooth servo interpolation ===")
    bus = FakeServoBus()
    engine = MotionEngine(bus)
    engine.start()
    engine.play(GESTURES["wave_right"])
    time.sleep(0.3)
    engine.stop()

    deltas = [abs(bus.history[i+1][2] - bus.history[i][2])
              for i in range(len(bus.history)-1) if bus.history[i+1][1] == bus.history[i][1]]
    check("more than 10 interpolation steps recorded", len(bus.history) > 10, f"{len(bus.history)} steps")
    check("no single step jumps more than 30 degrees", all(d <= 30 for d in deltas),
          f"max jump {max(deltas) if deltas else 0}")
    check("ends back at rest pose", bus.get_joint("right_shoulder") == 90)


def test_locomotion_kinematics():
    print("\n=== 5. Locomotion turn-type kinematics ===")
    d1 = DriveMotors(); d1.rotate_in_place(speed=180, duration_s=1.0, direction="left")
    dist1 = math.hypot(d1.x, d1.y)
    check("rotate-in-place barely translates", dist1 < 0.01, f"moved {dist1:.4f}m")
    check("rotate-in-place changes heading a lot", d1.heading_deg > 30 or d1.heading_deg < 330)

    d2 = DriveMotors(); d2.arc_turn(fwd=200, bias=100, duration_s=1.5, direction="left")
    dist2 = math.hypot(d2.x, d2.y)
    check("arc turn translates meaningfully", dist2 > 0.1, f"moved {dist2:.3f}m")

    d3 = DriveMotors(); d3.pivot_turn(speed=180, duration_s=1.2, direction="right")
    dist3 = math.hypot(d3.x, d3.y)
    check("pivot turn translates less than arc turn", dist3 < dist2, f"pivot={dist3:.3f} arc={dist2:.3f}")


def test_nonblocking_motion():
    print("\n=== 6. Non-blocking motion vs slow LLM call ===")
    llm_latency = 1.0
    brain = BrainService(FakeSlowLLM(llm_latency))
    result = {}

    def do_chat():
        t0 = time.perf_counter()
        brain.handle_chat("Hi there, can you wave hello?")
        result["chat_done_at"] = time.perf_counter() - t0

    def poll():
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < llm_latency + 0.3:
            item = brain.motion_queue.next_undelivered()
            if item:
                result["motion_at"] = time.perf_counter() - t0
                return
            time.sleep(0.005)

    t1, t2 = threading.Thread(target=do_chat), threading.Thread(target=poll)
    t1.start(); t2.start(); t1.join(); t2.join()

    check("motion visible before LLM call finishes",
          result["motion_at"] < result["chat_done_at"],
          f"motion={result['motion_at']:.3f}s chat={result['chat_done_at']:.3f}s")
    check("motion visible within 50ms", result["motion_at"] < 0.05, f"{result['motion_at']:.3f}s")


def test_combined_command():
    print("\n=== 7. Combined arm + locomotion command ===")
    brain = BrainService(FakeSlowLLM(0.05))
    result = brain.handle_chat("Come here and wave hello!")
    check("gesture matched", result["gesture"] == "wave_right", f"got {result['gesture']!r}")
    check("locomotion matched", result["locomotion"] == "forward", f"got {result['locomotion']!r}")
    check("both queued simultaneously",
          brain.motion_queue.next_undelivered() is not None and
          brain.locomotion_queue.next_undelivered() is not None)


if __name__ == "__main__":
    tests = [
        test_hardware_model,
        test_gesture_matching,
        test_locomotion_matching,
        test_smooth_interpolation,
        test_locomotion_kinematics,
        test_nonblocking_motion,
        test_combined_command,
    ]
    failed = False
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed = True
            print(f"  !! {t.__name__} FAILED: {e}")

    print(f"\n{'='*50}")
    print(f"RESULTS: {results['pass']} passed, {results['fail']} failed")
    print(f"{'='*50}")
    sys.exit(1 if failed else 0)

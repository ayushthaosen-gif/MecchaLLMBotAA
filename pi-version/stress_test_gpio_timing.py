"""
stress_test_gpio_timing.py
---------------------------
Demonstrates a real failure mode for this architecture: motion_engine.py
uses Python's time.sleep() to pace servo commands at STEP_INTERVAL (20ms).
Under heavy load — many concurrent Flask requests, JSON parsing, or any
CPU-bound Python code — the GIL and OS scheduler can delay the motion
thread's wake-up, producing jittery, inconsistent timing on what should be
a smooth 20ms cadence.

This does NOT touch real GPIO (no hardware here) — it measures the same
thing that would matter on real hardware: how consistently the code
issues each servo command, using perf_counter() as a stand-in for a
logic analyzer.

Two runs:
  BASELINE — motion engine alone, nothing else running
  LOADED   — motion engine + simulated heavy load (concurrent "requests"
             doing CPU-bound work, like several people hitting the
             dashboard while Claude/Ollama calls and JSON handling churn)
"""

import os
import sys
import time
import threading
import statistics

sys.path.insert(0, os.path.dirname(__file__))

from servo_controller import ServoBus, ServoBusConfig
from motion_engine import MotionEngine, _ease_in_out
from gestures import GESTURES


# ---------------------------------------------------------------------------
# Instrumented motion engine: records the actual wall-clock gap between
# each servo command instead of trusting that time.sleep(0.02) means
# exactly 20ms passed.
# ---------------------------------------------------------------------------

class InstrumentedMotionEngine(MotionEngine):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.deltas = []  # measured seconds between consecutive servo writes

    def _move_to(self, target_angles, hold_seconds):
        start_angles = {sid: self.bus.get_angle(sid) for sid in target_angles}
        max_delta = max(
            (abs(target_angles[sid] - start_angles[sid]) for sid in target_angles),
            default=0,
        )
        speed_duration = max_delta / self.max_speed if self.max_speed > 0 else 0
        duration = max(speed_duration, hold_seconds, 0.05)
        steps = max(int(duration / self.STEP_INTERVAL), 1)

        last_ts = time.perf_counter()
        for step in range(1, steps + 1):
            t = _ease_in_out(step / steps)
            frame = {
                sid: round(start_angles[sid] + (target_angles[sid] - start_angles[sid]) * t)
                for sid in target_angles
            }
            self.bus.set_angles(frame)
            now = time.perf_counter()
            self.deltas.append(now - last_ts)
            last_ts = now
            time.sleep(self.STEP_INTERVAL)


# ---------------------------------------------------------------------------
# Simulated heavy load: CPU-bound work standing in for concurrent dashboard
# requests, JSON (de)serialization, and general Pi business, all fighting
# for the GIL/CPU at the same time as the motion thread.
# ---------------------------------------------------------------------------

def cpu_bound_load(stop_event, worker_id):
    """Busy work with no I/O waits — the worst case for a Python thread
    sharing a GIL with something timing-sensitive."""
    n = 0
    while not stop_event.is_set():
        # Deliberately GIL-bound: pure Python arithmetic, no numpy release.
        n = (n * 1103515245 + 12345) % (2**31)
        _ = sum(i * i for i in range(500))


def run_trial(label: str, load_threads: int, duration_s: float = 4.0):
    bus = ServoBus(ServoBusConfig(simulate=True, servo_count=4))

    # Silence [SIM] prints during the trial so they don't themselves skew
    # timing via terminal I/O; re-enabled after.
    import servo_controller as sc
    original_transmit = sc.ServoBus._transmit
    sc.ServoBus._transmit = lambda self, sid, angle, packet: None

    engine = InstrumentedMotionEngine(bus, max_speed_deg_per_sec=220)
    engine.start()

    stop_event = threading.Event()
    workers = []
    for i in range(load_threads):
        th = threading.Thread(target=cpu_bound_load, args=(stop_event, i), daemon=True)
        workers.append(th)
        th.start()

    start = time.perf_counter()
    while time.perf_counter() - start < duration_s:
        engine.play("dance")
        time.sleep(0.05)

    time.sleep(1.0)  # let queued gestures drain
    stop_event.set()
    for th in workers:
        th.join(timeout=1)
    engine.stop()
    sc.ServoBus._transmit = original_transmit

    deltas_ms = [d * 1000 for d in engine.deltas]
    target_ms = engine.STEP_INTERVAL * 1000
    overruns = [d for d in deltas_ms if d > target_ms * 1.5]  # >50% late

    print(f"\n=== {label} ({load_threads} load threads) ===")
    print(f"  samples:        {len(deltas_ms)}")
    print(f"  target step:    {target_ms:.1f} ms")
    print(f"  mean step:      {statistics.mean(deltas_ms):.2f} ms")
    print(f"  median step:    {statistics.median(deltas_ms):.2f} ms")
    print(f"  max step:       {max(deltas_ms):.2f} ms")
    print(f"  stdev:          {statistics.stdev(deltas_ms):.2f} ms")
    print(f"  overruns >1.5x: {len(overruns)} / {len(deltas_ms)} "
          f"({100*len(overruns)/len(deltas_ms):.1f}%)")
    return deltas_ms


if __name__ == "__main__":
    print("Measuring servo command timing jitter (simulation, no real GPIO).")
    print("1 CPU core available in this environment (a Pi 5 has 4 — see notes after).")

    baseline = run_trial("BASELINE — motion engine alone", load_threads=0)
    loaded = run_trial("LOADED — motion engine + heavy CPU-bound threads", load_threads=3)

    print("\n=== COMPARISON ===")
    print(f"  baseline max jitter: {max(baseline):.2f} ms")
    print(f"  loaded max jitter:   {max(loaded):.2f} ms")
    print(f"  baseline stdev:      {statistics.stdev(baseline):.2f} ms")
    print(f"  loaded stdev:        {statistics.stdev(loaded):.2f} ms")

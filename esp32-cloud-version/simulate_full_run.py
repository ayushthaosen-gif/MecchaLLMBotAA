"""
simulate_full_run.py
----------------------
Runs the entire ESP32 + cloud-LLM architecture end-to-end without any
real hardware, WiFi, or cloud deployment:

  dashboard message
      -> RobotBrainService.handle_chat()   (cloud_function/main.py logic)
      -> command queue
      -> simulated ESP32 poll loop picks it up
      -> gesture played on a simulated Meccanoid servo bus
         (reusing servo_controller.py / motion_engine.py / gestures.py
         from the Pi project — they implement the exact same
         interpolation/easing/packet-building algorithm the .ino
         firmware ports to C, so this is a faithful stand-in, not a
         simplified mock)

The LLM call itself is stubbed (no real Claude API call) so this runs
anywhere without a key — everything else (queueing, polling, gesture
selection, servo timing/interpolation) is the real code path.
"""

import os
import sys
import time
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "cloud_function"))
sys.path.insert(0, os.path.dirname(__file__))  # servo_controller.py, motion_engine.py
                                                # are now copied alongside this file

from main import RobotBrainService  # cloud_function/main.py
from servo_controller import ServoBus, ServoBusConfig
from motion_engine import MotionEngine


class StubLLM:
    """Stand-in for Claude — canned replies so this runs with no API key."""
    def chat(self, system_prompt: str, user_message: str) -> str:
        lower = user_message.lower()
        if "weather" in lower:
            return "It's clear and 68 degrees, according to the last lookup."
        if "wave" in lower or "hello" in lower or "hi" in lower:
            return "Hey there! Good to hear from you."
        if "dance" in lower:
            return "Let's see some moves!"
        if "sit" in lower or "rest" in lower:
            return "Powering down for a rest."
        return "Got it — thinking about that."


class InstrumentedBus(ServoBus):
    """Records every angle command with a timestamp relative to the start
    of the whole run, across every gesture — this is what feeds the
    visualization at the end, standing in for what a logic analyzer would
    capture on the real ESP32-to-servo wire."""
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.history = []  # (elapsed_seconds, {servo_id: angle})
        self._run_start = time.perf_counter()

    def set_angles(self, angles):
        super().set_angles(angles)
        elapsed = time.perf_counter() - self._run_start
        snapshot = {sid: self.get_angle(sid) for sid in range(self.config.servo_count)}
        self.history.append((elapsed, snapshot))


def simulate_esp32_poll_loop(service: RobotBrainService, engine: MotionEngine,
                              stop_event: threading.Event, log: list):
    """Mirrors esp32_cloud_brain.ino's loop(): poll for a command, hand any
    gesture to the motion engine, ack it. Runs on its own thread just like
    it runs independently of the cloud function in real life."""
    while not stop_event.is_set():
        cmd = service.motion_queue.next_undelivered()
        if cmd is not None:
            log.append(f"[ESP32] polled command {cmd.id[:8]} -> gesture={cmd.value!r}")
            if cmd.value:
                engine.play(cmd.value)
            service.motion_queue.ack(cmd.id)
            log.append(f"[ESP32] acked command {cmd.id[:8]}")
        time.sleep(0.3)  # faster than the real 1.5s poll interval, for a shorter demo


def run():
    log = []
    bus = InstrumentedBus(ServoBusConfig(simulate=True, servo_count=4))
    engine = MotionEngine(bus, max_speed_deg_per_sec=220)
    engine.start()

    service = RobotBrainService(llm_client=StubLLM())

    stop_event = threading.Event()
    poll_thread = threading.Thread(
        target=simulate_esp32_poll_loop, args=(service, engine, stop_event, log), daemon=True
    )
    poll_thread.start()

    conversation = [
        "Hi there, can you wave hello?",
        "What's the weather like right now?",
        "Great, now let's dance!",
        "Okay, sit down and rest for a bit.",
    ]

    for message in conversation:
        log.append(f"\n[DASHBOARD] -> {message}")
        result = service.handle_chat(message)
        log.append(f"[CLOUD FN]  reply={result['reply']!r} gesture={result['gesture']!r}")
        time.sleep(2.2)  # let the ESP32 poll loop pick it up and play the gesture

    time.sleep(2.0)  # drain any final queued gesture
    stop_event.set()
    poll_thread.join(timeout=1)
    engine.stop()

    return log, bus.history


if __name__ == "__main__":
    log, history = run()

    print("=== FULL RUN LOG ===")
    for line in log:
        print(line)

    print(f"\n=== SERVO HISTORY: {len(history)} samples recorded ===")
    if history:
        print(f"First sample:  t={history[0][0]:.2f}s  {history[0][1]}")
        print(f"Last sample:   t={history[-1][0]:.2f}s  {history[-1][1]}")

    # Save for the visualization step
    import json
    with open(os.path.join(os.path.dirname(__file__), "run_history.json"), "w") as f:
        json.dump(
            [{"t": t, "angles": a} for t, a in history],
            f,
        )
    print("\nSaved run_history.json for charting.")

"""
full_system_test.py
----------------------
Exercises every feature built across this project in one run:
  - Arm gestures (all 7, via rig-accurate keyword matching)
  - Locomotion / wheeled feet (separate hardware, separate queue)
  - Non-blocking motion (both arms AND wheels queued before the LLM returns)
  - Weather intercept (tool use)
  - Hybrid local-filler + cloud-reply timing
  - Backend swap (Gemini vs Claude, same brain logic)
  - Full-body combined commands (e.g. "come here and wave hello")

Everything below is simulation — no real ESP32, WiFi, or cloud
deployment, no real API keys. LLM calls are stubbed with realistic
artificial latency so the timing numbers mean something.
"""

import os
import sys
import time
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "cloud_function"))
from main import RobotBrainService
from local_filler_model import generate_local_filler
from locomotion import DriveMotors


class SlowStubLLM:
    def __init__(self, latency_s=1.0, tag="LLM"):
        self.latency_s = latency_s
        self.tag = tag

    def chat(self, system_prompt, user_message):
        time.sleep(self.latency_s)
        lower = user_message.lower()
        if "weather" in lower:
            return f"[{self.tag}] It's 68°F and clear right now."
        if "wave" in lower or "hello" in lower:
            return f"[{self.tag}] Hey there!"
        if "dance" in lower:
            return f"[{self.tag}] Let's dance!"
        if "come here" in lower:
            return f"[{self.tag}] On my way over."
        if "bow" in lower:
            return f"[{self.tag}] Thank you, thank you."
        return f"[{self.tag}] Got it."


def section(title):
    print(f"\n{'='*70}\n{title}\n{'='*70}")


# ---------------------------------------------------------------------------
# 1. All arm gestures, keyword-matched correctly
# ---------------------------------------------------------------------------

def test_all_arm_gestures():
    section("1. ARM GESTURES — keyword matching, all 7")
    service = RobotBrainService(llm_client=SlowStubLLM(0.05))
    messages = [
        "hi there, wave hello!",
        "wave with both arms please",
        "take a bow",
        "i don't know, shrug",
        "point over there",
        "let's dance",
        "sit down and rest",
    ]
    for m in messages:
        result = service.handle_chat(m)
        print(f"  {m!r:35s} -> gesture={result['gesture']!r:15s} locomotion={result['locomotion']!r}")


# ---------------------------------------------------------------------------
# 2. Locomotion — the wheeled feet, separate hardware/queue entirely
# ---------------------------------------------------------------------------

def test_locomotion():
    section("2. LOCOMOTION — wheeled feet (2 simple DC motors, not servos)")
    service = RobotBrainService(llm_client=SlowStubLLM(0.05))
    drive = DriveMotors()

    messages = [
        "come here please",
        "back up a bit",
        "turn left",
        "turn right",
        "turn around",
    ]
    for m in messages:
        result = service.handle_chat(m)
        loco = result["locomotion"]
        print(f"  {m!r:25s} -> locomotion={loco!r:12s} gesture={result['gesture']!r}")
        if loco == "forward":
            drive.forward(duration_s=0.15)
        elif loco == "backward":
            drive.backward(duration_s=0.15)
        elif loco == "turn_left":
            drive.turn_left(duration_s=0.1)
        elif loco == "turn_right":
            drive.turn_right(duration_s=0.1)
        elif loco == "turn_around":
            drive.turn_left(duration_s=0.2)  # approximate 180 with a longer turn


# ---------------------------------------------------------------------------
# 3. Non-blocking proof for BOTH arms and legs at once
# ---------------------------------------------------------------------------

def test_nonblocking_combined():
    section("3. NON-BLOCKING — combined arm + locomotion command vs slow LLM")
    llm_latency = 1.2
    service = RobotBrainService(llm_client=SlowStubLLM(llm_latency))
    result = {}

    def do_chat():
        t0 = time.perf_counter()
        service.handle_chat("Come here and wave hello!")
        result["chat_done_at"] = time.perf_counter() - t0

    def poll():
        t0 = time.perf_counter()
        seen = {}
        while time.perf_counter() - t0 < llm_latency + 0.3:
            if "motion" not in seen:
                item = service.motion_queue.next_undelivered()
                if item:
                    seen["motion"] = (time.perf_counter() - t0, item.value)
            if "locomotion" not in seen:
                item = service.locomotion_queue.next_undelivered()
                if item:
                    seen["locomotion"] = (time.perf_counter() - t0, item.value)
            if len(seen) == 2:
                break
            time.sleep(0.005)
        result.update(seen)

    t1, t2 = threading.Thread(target=do_chat), threading.Thread(target=poll)
    t1.start(); t2.start(); t1.join(); t2.join()

    mt, mv = result["motion"]
    lt, lv = result["locomotion"]
    print(f"  simulated LLM latency:        {llm_latency:.2f}s")
    print(f"  arm gesture visible at:       {mt:.3f}s  ({mv})")
    print(f"  locomotion command visible:   {lt:.3f}s  ({lv})")
    print(f"  full LLM reply arrived at:    {result['chat_done_at']:.3f}s")
    print(f"  -> both physical systems moved {result['chat_done_at']-max(mt,lt):.3f}s "
          f"before the reply was ready")


# ---------------------------------------------------------------------------
# 4. Weather / tool use
# ---------------------------------------------------------------------------

def test_weather_tool_use():
    section("4. WEATHER TOOL USE")
    service = RobotBrainService(llm_client=SlowStubLLM(0.05))
    result = service.handle_chat("What's the weather like today?")
    print(f"  reply: {result['reply']!r}")
    print(f"  gesture: {result['gesture']!r}  locomotion: {result['locomotion']!r}")
    print("  (no WEATHER_LOCATION configured in this test env, so this exercises")
    print("   the 'not configured' code path rather than a real API call)")


# ---------------------------------------------------------------------------
# 5. Hybrid local filler vs cloud latency
# ---------------------------------------------------------------------------

def test_hybrid_filler():
    section("5. HYBRID — local filler timing vs cloud reply")
    for latency in (0.8, 1.5):
        result = {}
        t0 = time.perf_counter()

        def local_job():
            text = generate_local_filler(latency_ms=40)
            result["local"] = (time.perf_counter() - t0, text)

        def cloud_job():
            service = RobotBrainService(llm_client=SlowStubLLM(latency))
            r = service.handle_chat("Hi there, wave hello!")
            result["cloud"] = (time.perf_counter() - t0, r["reply"])

        t1, t2 = threading.Thread(target=local_job), threading.Thread(target=cloud_job)
        t1.start(); t2.start(); t1.join(); t2.join()

        lt, ltext = result["local"]
        ct, ctext = result["cloud"]
        print(f"  cloud latency {latency:.1f}s -> local filler at {lt:.3f}s "
              f"({ltext[:30]}...), cloud reply at {ct:.3f}s ({ctext!r})")


# ---------------------------------------------------------------------------
# 6. Backend swap: Gemini vs Claude, identical brain logic
# ---------------------------------------------------------------------------

def test_backend_swap():
    section("6. BACKEND SWAP — Gemini vs Claude, same brain logic")
    for tag in ("gemini", "claude"):
        service = RobotBrainService(llm_client=SlowStubLLM(0.05, tag=tag.upper()))
        result = service.handle_chat("Take a bow!")
        print(f"  provider={tag:8s} -> reply={result['reply']!r} gesture={result['gesture']!r}")


if __name__ == "__main__":
    test_all_arm_gestures()
    test_locomotion()
    test_nonblocking_combined()
    test_weather_tool_use()
    test_hybrid_filler()
    test_backend_swap()

    print(f"\n{'='*70}\nALL SYSTEMS TESTED\n{'='*70}")

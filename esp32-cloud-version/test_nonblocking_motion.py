"""
test_nonblocking_motion.py
-----------------------------
Proves the actual fix: gesture commands are visible in motion_queue
BEFORE the slow LLM call finishes, not after. This is the real
"movement while thinking" guarantee the whole architecture claims —
this test is what makes that a verified fact instead of an assumption.
"""

import os
import sys
import time
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "cloud_function"))
from main import RobotBrainService


class SlowStubLLM:
    def __init__(self, latency_s: float):
        self.latency_s = latency_s

    def chat(self, system_prompt: str, user_message: str) -> str:
        time.sleep(self.latency_s)
        return "Here's my answer, finally."


def test_motion_available_before_llm_returns():
    llm_latency = 1.0
    service = RobotBrainService(llm_client=SlowStubLLM(llm_latency))

    result = {}

    def do_chat():
        t0 = time.perf_counter()
        service.handle_chat("Hi there, can you wave hello?")
        result["chat_done_at"] = time.perf_counter() - t0

    def poll_motion_queue():
        # Poll aggressively, like the ESP32 would, and record the first
        # moment the gesture becomes visible.
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < llm_latency + 0.5:
            item = service.motion_queue.next_undelivered()
            if item is not None:
                result["motion_visible_at"] = time.perf_counter() - t0
                result["motion_gesture"] = item.value
                return
            time.sleep(0.01)
        result["motion_visible_at"] = None

    chat_thread = threading.Thread(target=do_chat)
    poll_thread = threading.Thread(target=poll_motion_queue)
    chat_thread.start()
    poll_thread.start()
    chat_thread.join()
    poll_thread.join()

    print("=== Non-blocking motion test ===")
    print(f"  simulated LLM latency:      {llm_latency:.2f}s")
    print(f"  motion visible in queue at: {result['motion_visible_at']:.3f}s "
          f"(gesture={result['motion_gesture']!r})")
    print(f"  full chat call finished at: {result['chat_done_at']:.3f}s")

    gap = result["chat_done_at"] - result["motion_visible_at"]
    print(f"  -> motion was queued {gap:.3f}s BEFORE the LLM call returned")

    assert result["motion_visible_at"] < result["chat_done_at"], \
        "BUG: motion wasn't visible until after the LLM call finished"
    assert result["motion_gesture"] == "wave_right"
    print("  PASS: movement is genuinely decoupled from LLM latency")


if __name__ == "__main__":
    test_motion_available_before_llm_returns()

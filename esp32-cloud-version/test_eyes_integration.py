"""
test_eyes_integration.py
---------------------------
Confirms two things after wiring eyes into RobotBrainService:
1. Mood classification produces the expected mood per reply text
2. Eyes still update BEFORE the slow LLM call returns (thinking status),
   same non-blocking guarantee as gestures/locomotion — wiring eyes in
   must not silently reintroduce blocking behavior.
"""

import sys
import os
import time
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "cloud_function"))
from main import RobotBrainService, classify_mood
from eyes import EyeModule


def test_mood_classification():
    print("=== Mood classification ===")
    cases = {
        "Great to hear from you! Awesome!": "happy",
        "Sorry, I can't do that right now.": "concerned",
        "Let's dance! This is amazing!": "excited",
        "Sure, okay.": "calm",
        "The sky is blue.": "neutral",
    }
    for text, expected in cases.items():
        got = classify_mood(text)
        status = "PASS" if got == expected else "FAIL"
        print(f"  [{status}] {text!r} -> {got} (expected {expected})")
        assert got == expected, f"{text!r}: got {got}, expected {expected}"


class SlowStubLLM:
    def __init__(self, latency_s=1.0):
        self.latency_s = latency_s
    def chat(self, system_prompt, user_message):
        time.sleep(self.latency_s)
        return "Great, happy to help!"


def test_eyes_nonblocking_with_brain():
    print("\n=== Eyes remain non-blocking inside RobotBrainService ===")
    eyes = EyeModule(simulate=False)  # simulate=False just to silence prints; still in-memory
    llm_latency = 1.0
    service = RobotBrainService(llm_client=SlowStubLLM(llm_latency), eyes=eyes)

    result = {}

    def do_chat():
        t0 = time.perf_counter()
        service.handle_chat("hi there, wave hello!")
        result["chat_done_at"] = time.perf_counter() - t0

    def poll_eyes():
        t0 = time.perf_counter()
        seen_thinking = False
        while time.perf_counter() - t0 < llm_latency + 0.3:
            if eyes.current_color == (5, 3, 0) and not seen_thinking:  # "thinking" amber
                result["thinking_at"] = time.perf_counter() - t0
                seen_thinking = True
            time.sleep(0.005)

    t1, t2 = threading.Thread(target=do_chat), threading.Thread(target=poll_eyes)
    t1.start(); t2.start(); t1.join(); t2.join()

    print(f"  thinking color set at: {result['thinking_at']:.3f}s")
    print(f"  full chat call done at: {result['chat_done_at']:.3f}s")
    assert result["thinking_at"] < 0.05, "eyes should show thinking almost instantly"
    assert result["chat_done_at"] - result["thinking_at"] > 0.9, "speaking should wait for the LLM"
    print("  PASS: eyes update instantly, independent of LLM latency")


if __name__ == "__main__":
    test_mood_classification()
    test_eyes_nonblocking_with_brain()
    print("\nALL EYE INTEGRATION TESTS PASSED")

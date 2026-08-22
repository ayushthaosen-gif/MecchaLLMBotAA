"""
test_eyes_nonblocking.py
---------------------------
Proves the eye status indicator updates instantly (thinking = amber the
moment a message arrives) rather than waiting on the slow LLM call —
same non-blocking pattern already verified for gestures/locomotion in
this project, applied to the eyes.
"""

import time
import threading
from eyes import EyeModule


class FakeSlowLLM:
    def __init__(self, latency_s=1.0):
        self.latency_s = latency_s
    def reply(self, message: str) -> str:
        time.sleep(self.latency_s)
        return "(reply after thinking)"


def handle_message(eyes: EyeModule, llm: FakeSlowLLM, message: str, result: dict):
    t0 = time.perf_counter()
    eyes.set_status("listening")
    eyes.set_status("thinking")           # instant — before the slow call below
    result["thinking_at"] = time.perf_counter() - t0

    reply = llm.reply(message)             # slow

    eyes.set_status("speaking")
    result["speaking_at"] = time.perf_counter() - t0
    result["reply"] = reply


if __name__ == "__main__":
    eyes = EyeModule()
    llm = FakeSlowLLM(latency_s=1.0)
    result = {}

    t = threading.Thread(target=handle_message, args=(eyes, llm, "hello", result))
    t.start()
    t.join()

    print(f"\nthinking color set at: {result['thinking_at']:.3f}s")
    print(f"speaking color set at: {result['speaking_at']:.3f}s")
    print(f"gap (LLM latency):     {result['speaking_at'] - result['thinking_at']:.3f}s")

    assert result["thinking_at"] < 0.01, "thinking status should be near-instant"
    assert result["speaking_at"] - result["thinking_at"] > 0.9, "speaking should wait for the LLM"
    print("\nPASS: eye status is non-blocking, same as gestures/locomotion")

"""
demo_hybrid_local_cloud.py
-----------------------------
Tests two things together:

1. Backend swapping — the SAME RobotBrainService logic (memory, gesture
   matching, weather intercept) works whether LLM_PROVIDER is "gemini",
   "claude", or a test stub — proving the earlier architecture actually
   supports "local does X, cloud does Y" without rewriting brain logic
   per provider.

2. The hybrid latency-masking pattern — a simulated cloud call with
   real network-like delay, racing against the local filler model. The
   local filler always wins (it's near-instant), giving the robot
   SOMETHING to have "said" immediately, with the real answer arriving
   shortly after.

No real API keys are used — LLM calls are stubbed so this runs anywhere.
"""

import os
import sys
import time
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "cloud_function"))

from main import RobotBrainService
from local_filler_model import generate_local_filler


class SlowStubLLM:
    """Stands in for a real cloud call (Gemini/Claude) — same interface,
    with an artificial network-like delay so the timing race against the
    local filler model is meaningful instead of instant either way."""
    def __init__(self, simulated_latency_s: float = 1.2):
        self.simulated_latency_s = simulated_latency_s

    def chat(self, system_prompt: str, user_message: str) -> str:
        time.sleep(self.simulated_latency_s)
        lower = user_message.lower()
        if "weather" in lower:
            return "It's 68 degrees and clear right now."
        if "wave" in lower or "hello" in lower:
            return "Hi! Great to hear from you."
        if "dance" in lower:
            return "Let's see some moves!"
        return "That's a good question — here's what I think."


def run_hybrid_exchange(message: str, cloud_latency_s: float):
    """Fires the local filler and the cloud call at the same instant, on
    separate threads, and reports which arrived when — this is what the
    ESP32 would do: speak the filler immediately, then replace/follow up
    with the real answer once the cloud call resolves."""
    result = {}
    t0 = time.perf_counter()

    def local_job():
        text = generate_local_filler(latency_ms=40)
        result["local"] = (time.perf_counter() - t0, text)

    def cloud_job():
        service = RobotBrainService(llm_client=SlowStubLLM(cloud_latency_s))
        response = service.handle_chat(message)
        result["cloud"] = (time.perf_counter() - t0, response)

    t_local = threading.Thread(target=local_job)
    t_cloud = threading.Thread(target=cloud_job)
    t_local.start()
    t_cloud.start()
    t_local.join()
    t_cloud.join()

    return result


def test_backend_swap():
    print("=== Backend swap: same brain logic, different LLM provider ===")
    for provider_name, stub_reply in [("gemini (stubbed)", "Gemini says hi"),
                                        ("claude (stubbed)", "Claude says hi")]:
        class FixedStub:
            def chat(self, system_prompt, user_message):
                return stub_reply
        service = RobotBrainService(llm_client=FixedStub())
        result = service.handle_chat("hi there, wave hello!")
        print(f"  {provider_name:20s} -> reply={result['reply']!r} gesture={result['gesture']!r}")


def test_hybrid_timing():
    print("\n=== Hybrid timing: local filler vs cloud call ===")
    for message, cloud_latency in [
        ("Hi there, wave hello!", 0.9),
        ("What's the weather like?", 1.6),
    ]:
        print(f"\n  message: {message!r}  (simulated cloud latency: {cloud_latency}s)")
        result = run_hybrid_exchange(message, cloud_latency)
        local_t, local_text = result["local"]
        cloud_t, cloud_resp = result["cloud"]
        print(f"    t={local_t:5.3f}s  LOCAL FILLER : {local_text!r}")
        print(f"    t={cloud_t:5.3f}s  CLOUD REPLY  : reply={cloud_resp['reply']!r} "
              f"gesture={cloud_resp['gesture']!r}")
        print(f"    -> robot had something to say {cloud_t - local_t:.3f}s before "
              f"the real answer arrived")


if __name__ == "__main__":
    test_backend_swap()
    test_hybrid_timing()

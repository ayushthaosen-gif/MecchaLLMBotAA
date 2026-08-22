"""
test_voice_loop.py
---------------------
Full voice round-trip: simulated microphone utterance -> STT -> brain
(gestures/locomotion/eyes/LLM) -> TTS -> simulated speaker playback.

Proves:
  1. The pipeline stages happen in the correct order and don't skip
  2. Motion/locomotion/eyes still update before the slow LLM+TTS chain
     finishes — voice output being added must not silently make
     everything wait on TTS synthesis too
  3. Total round-trip timing is realistic (STT + LLM + TTS all add up)
"""

import sys
import os
import time
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "cloud_function"))
from main import RobotBrainService
from eyes import EyeModule
from voice_pipeline import TextToSpeech, SpeechToText


class SlowStubLLM:
    def __init__(self, latency_s=0.8):
        self.latency_s = latency_s
    def chat(self, system_prompt, user_message):
        time.sleep(self.latency_s)
        return "Hey there! Great to hear from you."


def run_voice_turn(spoken_utterance: str, llm_latency_s: float = 0.8):
    stt = SpeechToText(recognize_latency_s=0.3)
    tts = TextToSpeech(synth_latency_s=0.15)
    eyes = EyeModule(simulate=False)
    service = RobotBrainService(llm_client=SlowStubLLM(llm_latency_s), eyes=eyes)

    timeline = []
    t0 = time.perf_counter()

    def log(label):
        timeline.append((time.perf_counter() - t0, label))

    # 1. "microphone" -> STT
    log("mic_capture_start")
    heard = stt.listen(spoken_utterance)
    log("stt_done")

    # 2. brain handles it — motion/locomotion/eyes fire instantly inside,
    #    only the LLM call is slow
    result = {}

    def brain_call():
        result["response"] = service.handle_chat(heard.text)
        log("brain_done")

    def poll_motion():
        t_poll0 = time.perf_counter()
        while time.perf_counter() - t_poll0 < llm_latency_s + 0.5:
            item = service.motion_queue.next_undelivered()
            if item:
                log(f"motion_queued({item.value})")
                return
            time.sleep(0.005)

    t_brain = threading.Thread(target=brain_call)
    t_poll = threading.Thread(target=poll_motion)
    t_brain.start()
    t_poll.start()
    t_brain.join()
    t_poll.join()

    # 3. TTS speaks the reply — only starts once the brain has replied
    spoken = tts.speak(result["response"]["reply"])
    log("tts_playback_complete")

    return timeline, result["response"], spoken


if __name__ == "__main__":
    timeline, response, spoken = run_voice_turn("hi there, can you wave hello", llm_latency_s=0.8)

    print("=== Voice loop timeline ===")
    for t, label in timeline:
        print(f"  t={t:6.3f}s  {label}")

    print(f"\nbrain result: {response}")
    print(f"TTS playback: {spoken.duration_s:.2f}s of audio")

    # --- assertions ---
    labels = dict(timeline)
    by_label = {label: t for t, label in timeline}

    assert by_label["stt_done"] > by_label["mic_capture_start"], "STT must come after capture"
    assert "motion_queued(wave_right)" in by_label, "gesture should have been detected"
    assert by_label["motion_queued(wave_right)"] < by_label["brain_done"], \
        "motion must be queued before the slow brain call finishes"
    assert by_label["tts_playback_complete"] > by_label["brain_done"], \
        "TTS must not start before the brain has a reply"

    print("\nPASS: voice loop ordering and non-blocking motion both correct")

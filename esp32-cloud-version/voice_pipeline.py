"""
voice_pipeline.py
--------------------
TTS (speak the reply) and STT (transcribe what was heard) pipeline
stubs. This sandbox has no real audio hardware or engine access, so
both sides are SIMULATED: realistic latency and data flow are modeled,
but no actual audio is synthesized or recognized. On real hardware:

  TTS: Piper (offline, runs on Pi) or a cloud TTS API, feeding PCM
       audio to the MAX98357A over I2S (see audio-pinout.md)
  STT: Whisper.cpp (offline) or a cloud STT API, fed PCM audio captured
       from the INMP441 over I2S

What's real here: the pipeline shape, timing behavior, and the
interface these two real engines would be dropped into — swap
TextToSpeech._synthesize / SpeechToText._recognize for real engine
calls and everything else (queuing, integration with RobotBrainService)
is unchanged.
"""

import time
import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class TTSResult:
    text: str
    duration_s: float  # how long real playback would take


class TextToSpeech:
    """Simulated TTS: estimates spoken duration from word count at a
    realistic speaking rate (~150 words/minute) instead of synthesizing
    real audio, since no TTS engine is available in this sandbox."""

    WORDS_PER_MINUTE = 150

    def __init__(self, synth_latency_s: float = 0.15):
        # synth_latency_s stands in for real engine startup/inference time
        # (Piper is fast, ~100-300ms first-audio latency on a Pi)
        self.synth_latency_s = synth_latency_s
        self.history = []

    def speak(self, text: str) -> TTSResult:
        """Blocks for the simulated synthesis time, then returns metadata
        about the (simulated) playback — mirrors calling a real TTS
        engine and waiting for the first audio chunk."""
        time.sleep(self.synth_latency_s)
        word_count = max(len(text.split()), 1)
        duration_s = (word_count / self.WORDS_PER_MINUTE) * 60
        result = TTSResult(text=text, duration_s=duration_s)
        self.history.append((time.perf_counter(), result))
        return result


@dataclass
class STTResult:
    text: str
    confidence: float


class SpeechToText:
    """Simulated STT: since there's no real audio input, this accepts a
    pre-written string standing in for "what a real STT engine would
    have transcribed" and adds realistic processing latency. This is
    the seam where a real Whisper.cpp/cloud STT call replaces the stub."""

    def __init__(self, recognize_latency_s: float = 0.3):
        self.recognize_latency_s = recognize_latency_s
        self.history = []

    def listen(self, simulated_utterance: str) -> STTResult:
        time.sleep(self.recognize_latency_s)
        # A real engine would return a confidence score based on audio
        # quality; here it's a fixed high value since the "audio" is
        # already-clean text.
        result = STTResult(text=simulated_utterance, confidence=0.95)
        self.history.append((time.perf_counter(), result))
        return result


if __name__ == "__main__":
    tts = TextToSpeech()
    stt = SpeechToText()

    heard = stt.listen("hi there, can you wave hello")
    print(f"STT heard: {heard.text!r} (confidence {heard.confidence})")

    spoken = tts.speak("Hey there! Great to hear from you.")
    print(f"TTS speaking: {spoken.text!r} (~{spoken.duration_s:.2f}s playback)")

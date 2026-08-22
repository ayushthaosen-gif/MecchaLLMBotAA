"""
local_filler_model.py
------------------------
Stand-in for a real tiny local model on the ESP32 (e.g. the 28.9M or
260K parameter TinyStories-trained models that have been demonstrated
running on an ESP32-S3 at ~9-33 tokens/sec — see slvDev's esp32-ai and
DaveBben's esp32-llm projects). Those models can't answer questions or
hold a conversation — they only generate short, simple, TinyStories-
style text. That's honestly represented here: FILLERS below are the
kind of output such a model actually produces, not real replies.

The real use for a model like this isn't answering — it's covering the
network round-trip to the cloud LLM with SOMETHING immediate, since a
tiny on-chip model responds in milliseconds while a cloud call takes
hundreds of ms to seconds. This module simulates that timing, not the
actual C inference (porting llama2.c and testing it belongs on real
ESP32-S3 hardware, not in this sandbox).
"""

import random
import time

# Representative of what a TinyStories-trained tiny model actually
# outputs — simple, story-like, NOT a contextual acknowledgment. A real
# implementation would run inference per-call; these are fixed examples
# standing in for that variability.
FILLER_SNIPPETS = [
    "Once there was a little robot who loved to help.",
    "The small robot looked around and waited patiently.",
    "A little robot began to think about what to say next.",
    "One day, a robot wanted to answer a question.",
]


def generate_local_filler(latency_ms: float = 40.0) -> str:
    """Simulates the ESP32's local model: near-instant, not contextual."""
    time.sleep(latency_ms / 1000.0)
    return random.choice(FILLER_SNIPPETS)

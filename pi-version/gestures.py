"""
gestures.py
-----------
CORRECTED: originally written against a placeholder 4-servo layout
(left/right shoulder, torso rotate, head pan) before the real hardware
was confirmed. The Meccanoid Personal Robot 2.0 has NO torso rotation
and NO head/neck servo — see rig.py / rig_gestures.py for the full
explanation and the current, more detailed rig-based gesture system.

This file is kept for compatibility with the simpler flat
ServoBus/MotionEngine pair (used by simulate_full_run.py's non-rig
demo path) and now uses the SAME gesture names and real joint mapping
as rig_gestures.py and cloud_function/main.py's KEYWORD_TRIGGERS, so a
gesture decided in the cloud function actually matches a key here.

Servo IDs (matches esp32_cloud_brain.ino's comment mapping):
    0 = right_shoulder
    1 = right_elbow
    2 = left_shoulder
    3 = left_elbow
"""

from typing import Dict, List, Tuple, Optional

Keyframe = Tuple[Dict[int, int], float]  # (angles, hold_seconds)
Gesture = List[Keyframe]

REST = {0: 90, 1: 90, 2: 90, 3: 90}

GESTURES: Dict[str, Gesture] = {
    "wave_right": [
        ({0: 150, 1: 120, 2: 90, 3: 90}, 0.4),
        ({0: 150, 1: 70, 2: 90, 3: 90}, 0.25),
        ({0: 150, 1: 120, 2: 90, 3: 90}, 0.25),
        (REST, 0.4),
    ],
    "wave_both": [
        ({0: 150, 1: 120, 2: 150, 3: 120}, 0.4),
        ({0: 150, 1: 70, 2: 150, 3: 70}, 0.3),
        ({0: 150, 1: 120, 2: 150, 3: 120}, 0.3),
        (REST, 0.4),
    ],
    "bow": [
        ({0: 50, 1: 70, 2: 50, 3: 70}, 0.6),
        ({0: 50, 1: 70, 2: 50, 3: 70}, 0.8),
        (REST, 0.6),
    ],
    "shrug": [
        ({0: 130, 1: 60, 2: 130, 3: 120}, 0.5),
        ({0: 130, 1: 60, 2: 130, 3: 120}, 0.5),
        (REST, 0.5),
    ],
    "point": [
        ({0: 100, 1: 170, 2: 90, 3: 90}, 0.6),
        ({0: 100, 1: 170, 2: 90, 3: 90}, 0.8),
        (REST, 0.6),
    ],
    "full_dance": [
        ({0: 60, 1: 130, 2: 120, 3: 50}, 0.35),
        ({0: 120, 1: 50, 2: 60, 3: 130}, 0.35),
        ({0: 60, 1: 130, 2: 120, 3: 50}, 0.35),
        ({0: 120, 1: 50, 2: 60, 3: 130}, 0.35),
        (REST, 0.5),
    ],
    "sit": [
        ({0: 170, 1: 20, 2: 170, 3: 20}, 0.6),
    ],
}

# Keyword triggers — kept here for standalone use of this module, though
# cloud_function/main.py has its own copy of this logic for the ESP32-cloud
# architecture. Same specificity fix as rig_gestures.py: longest match wins.
KEYWORD_TRIGGERS = {
    "wave_right": ["wave", "hello", "hi there"],
    "wave_both": ["wave both", "wave with both", "big wave"],
    "bow": ["bow", "take a bow"],
    "shrug": ["shrug", "i don't know", "dunno"],
    "point": ["point at", "point over there"],
    "full_dance": ["dance", "boogie", "groove"],
    "sit": ["sit down", "rest", "power down"],
}


def match_gesture_from_text(text: str) -> Optional[str]:
    """Longest-match wins — prevents 'wave' (wave_right) from shadowing
    'wave with both' (wave_both) when both phrases appear in a message."""
    lower = text.lower()
    best_gesture, best_len = None, -1
    for gesture_name, phrases in KEYWORD_TRIGGERS.items():
        for phrase in phrases:
            if phrase in lower and len(phrase) > best_len:
                best_gesture, best_len = gesture_name, len(phrase)
    return best_gesture

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

import re
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

    # --- Dance / meme gestures (same choreography as rig_gestures.py,
    # re-expressed in this file's 0-3 servo-id mapping) ---------------------
    "dab": [
        ({0: 155, 1: 35, 2: 35, 3: 165}, 0.5),
        ({0: 155, 1: 35, 2: 35, 3: 165}, 0.8),
        (REST, 0.6),
    ],
    "flex": [
        ({0: 140, 1: 30, 2: 140, 3: 30}, 0.4),
        ({0: 135, 1: 40, 2: 135, 3: 40}, 0.25),
        ({0: 140, 1: 30, 2: 140, 3: 30}, 0.5),
        (REST, 0.5),
    ],
    "floss": [
        ({0: 130, 1: 150, 2: 55, 3: 45}, 0.22),
        ({0: 55, 1: 45, 2: 130, 3: 150}, 0.22),
        ({0: 130, 1: 150, 2: 55, 3: 45}, 0.22),
        ({0: 55, 1: 45, 2: 130, 3: 150}, 0.22),
        (REST, 0.4),
    ],
    "the_robot": [
        ({0: 140}, 0.15),
        ({1: 60}, 0.15),
        ({0: 90}, 0.15),
        ({1: 90}, 0.15),
        ({2: 140}, 0.15),
        ({3: 60}, 0.15),
        ({2: 90}, 0.15),
        ({3: 90}, 0.15),
        ({0: 120, 2: 120}, 0.2),
        (REST, 0.4),
    ],
    "mic_drop": [
        ({0: 110, 1: 50}, 0.5),
        ({0: 110, 1: 50}, 0.6),
        ({0: 30, 1: 170}, 0.35),
        ({0: 30, 1: 170}, 0.5),
        (REST, 0.5),
    ],
    "finger_guns": [
        ({0: 100, 1: 170, 2: 100, 3: 170}, 0.35),
        ({0: 95, 1: 150, 2: 95, 3: 150}, 0.15),
        ({0: 100, 1: 170, 2: 100, 3: 170}, 0.35),
        ({0: 95, 1: 150, 2: 95, 3: 150}, 0.15),
        (REST, 0.5),
    ],
}

# Modern reaction/meme poses. These stay inside the same conservative
# 15-165 degree envelope as the older routines.
GESTURES.update({
    "aura_farm": [({0: 145, 1: 55, 2: 145, 3: 55}, 0.7), ({0: 155, 1: 75, 2: 155, 3: 75}, 0.7), (REST, 0.5)],
    "six_seven": [({0: 65, 1: 105, 2: 115, 3: 75}, 0.35), ({0: 115, 1: 75, 2: 65, 3: 105}, 0.35), (REST, 0.4)],
    "npc_mode": [({0: 90, 1: 75, 2: 90, 3: 105}, 0.45), ({0: 100, 1: 85, 2: 80, 3: 95}, 0.45), (REST, 0.4)],
    "facepalm": [({0: 125, 1: 35, 2: 90, 3: 90}, 0.55), ({0: 125, 1: 35, 2: 90, 3: 90}, 0.65), (REST, 0.5)],
    "success_pump": [({0: 150, 1: 45, 2: 150, 3: 45}, 0.35), ({0: 125, 1: 70, 2: 125, 3: 70}, 0.2), ({0: 150, 1: 45, 2: 150, 3: 45}, 0.35), (REST, 0.5)],
    "side_eye": [({0: 80, 1: 100, 2: 105, 3: 70}, 0.45), ({0: 80, 1: 100, 2: 105, 3: 70}, 0.7), (REST, 0.45)],
})
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
    "dab": ["dab", "do a dab"],
    "flex": ["flex", "show your muscles", "flex your arms"],
    "floss": ["floss", "do the floss", "floss dance"],
    "the_robot": ["do the robot", "robot dance"],
    "mic_drop": ["mic drop", "drop the mic"],
    "finger_guns": ["finger guns", "pew pew"],
    "aura_farm": ["aura farm", "farm aura", "maximum aura"],
    "six_seven": ["six seven gesture", "67 gesture", "do six seven"],
    "npc_mode": ["npc mode", "act like an npc", "yes yes npc"],
    "facepalm": ["facepalm", "bruh moment"],
    "success_pump": ["success pose", "victory pump", "big win"],
    "side_eye": ["side eye", "suspicious look", "really bro"],
}


def _phrase_matches(phrase: str, lower_text: str) -> bool:
    """Word-boundary match, not a bare substring test — otherwise 'bow'
    would fire inside 'rainbow'/'elbow', 'sit down' inside 'visit downtown',
    'rest' inside 'forest'/'interest', etc."""
    return re.search(r"\b" + re.escape(phrase) + r"\b", lower_text) is not None


def match_gesture_from_text(text: str) -> Optional[str]:
    """Longest-match wins — prevents 'wave' (wave_right) from shadowing
    'wave with both' (wave_both) when both phrases appear in a message."""
    lower = text.lower()
    best_gesture, best_len = None, -1
    for gesture_name, phrases in KEYWORD_TRIGGERS.items():
        for phrase in phrases:
            if _phrase_matches(phrase, lower) and len(phrase) > best_len:
                best_gesture, best_len = gesture_name, len(phrase)
    return best_gesture

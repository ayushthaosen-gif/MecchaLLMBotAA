"""
rig_gestures.py
-----------------
Gestures for the Meccanoid PERSONAL ROBOT 2.0's real body: 4 servos,
2 per arm (shoulder, elbow), NO head/neck motor at all.

CORRECTION: earlier gestures here (bow, shrug, point_and_look, nod,
shake_head) all depended on head_tilt/neck_pan joints that don't exist
on this robot — they've been removed or rewritten as arms-only
approximations. A robot with no head servo cannot nod or turn to look
at something, no matter how the gesture is written; only its arms move
under motor control.
"""

import re
from typing import Dict, List, Optional, Tuple
from rig_motion_engine import Gesture


def _phrase_matches(phrase: str, lower_text: str) -> bool:
    """Word-boundary match, not a bare substring test — otherwise 'bow'
    would fire inside 'rainbow'/'elbow', 'sit down' inside 'visit downtown',
    'rest' inside 'forest'/'interest', etc."""
    return re.search(r"\b" + re.escape(phrase) + r"\b", lower_text) is not None

REST_POSE: Dict[str, int] = {
    "right_shoulder": 90, "right_elbow": 90,
    "left_shoulder": 90, "left_elbow": 90,
}

GESTURES: Dict[str, Gesture] = {

    "wave_right": [
        ({"right_shoulder": 150, "right_elbow": 120}, 0.4),
        ({"right_elbow": 70}, 0.25),
        ({"right_elbow": 120}, 0.25),
        ({"right_elbow": 70}, 0.25),
        ({"right_shoulder": 90, "right_elbow": 90}, 0.4),
    ],

    "wave_both": [
        ({"right_shoulder": 150, "right_elbow": 120,
          "left_shoulder": 150, "left_elbow": 120}, 0.4),
        ({"right_elbow": 70, "left_elbow": 70}, 0.3),
        ({"right_elbow": 120, "left_elbow": 120}, 0.3),
        (REST_POSE, 0.4),
    ],

    # Arms-only approximation of a bow — both arms sweep down and
    # forward together. No head tilt: this robot's head can't move.
    "bow": [
        ({"right_shoulder": 50, "right_elbow": 70,
          "left_shoulder": 50, "left_elbow": 70}, 0.6),
        ({"right_shoulder": 50, "right_elbow": 70,
          "left_shoulder": 50, "left_elbow": 70}, 0.8),
        (REST_POSE, 0.6),
    ],

    # Shrug — both shoulders lift symmetrically, elbows tuck in.
    "shrug": [
        ({"right_shoulder": 130, "right_elbow": 60,
          "left_shoulder": 130, "left_elbow": 120}, 0.5),
        ({"right_shoulder": 130, "left_shoulder": 130}, 0.5),
        (REST_POSE, 0.5),
    ],

    # Point — right arm extends toward one side. No accompanying head
    # turn is possible on this robot (see module docstring), so this is
    # "point" only, not "point and look."
    "point": [
        ({"right_shoulder": 100, "right_elbow": 170}, 0.6),
        ({"right_shoulder": 100, "right_elbow": 170}, 0.8),
        (REST_POSE, 0.6),
    ],

    # Full-body dance, arms-only — both arms alternate on independent
    # schedules across both chains at once.
    "full_dance": [
        ({"right_shoulder": 60, "right_elbow": 130,
          "left_shoulder": 120, "left_elbow": 50}, 0.35),
        ({"right_shoulder": 120, "right_elbow": 50,
          "left_shoulder": 60, "left_elbow": 130}, 0.35),
        ({"right_shoulder": 60, "right_elbow": 130,
          "left_shoulder": 120, "left_elbow": 50}, 0.35),
        ({"right_shoulder": 120, "right_elbow": 50,
          "left_shoulder": 60, "left_elbow": 130}, 0.35),
        (REST_POSE, 0.5),
    ],

    "sit": [
        ({"right_shoulder": 170, "right_elbow": 20,
          "left_shoulder": 170, "left_elbow": 20}, 0.6),
    ],

    # --- Dance / meme gestures --------------------------------------------

    # Dab — right arm bent up across the "face" (no head to tuck into, but
    # the pose reads clearly anyway), left arm extended straight out/down.
    "dab": [
        ({"right_shoulder": 155, "right_elbow": 35,
          "left_shoulder": 35, "left_elbow": 165}, 0.5),
        ({"right_shoulder": 155, "right_elbow": 35,
          "left_shoulder": 35, "left_elbow": 165}, 0.8),
        (REST_POSE, 0.6),
    ],

    # Flex — double-bicep pose with a little pulse (flex, slight release,
    # flex again) instead of a single static hold.
    "flex": [
        ({"right_shoulder": 140, "right_elbow": 30,
          "left_shoulder": 140, "left_elbow": 30}, 0.4),
        ({"right_shoulder": 135, "right_elbow": 40,
          "left_shoulder": 135, "left_elbow": 40}, 0.25),
        ({"right_shoulder": 140, "right_elbow": 30,
          "left_shoulder": 140, "left_elbow": 30}, 0.5),
        (REST_POSE, 0.5),
    ],

    # Floss — arms swing opposite front-back on a fast alternating
    # schedule. No hips to rock, so the approximation leans on the arm
    # swing alone, at a snappier tempo than full_dance to read as "floss"
    # rather than a generic dance.
    "floss": [
        ({"right_shoulder": 130, "right_elbow": 150,
          "left_shoulder": 55, "left_elbow": 45}, 0.22),
        ({"right_shoulder": 55, "right_elbow": 45,
          "left_shoulder": 130, "left_elbow": 150}, 0.22),
        ({"right_shoulder": 130, "right_elbow": 150,
          "left_shoulder": 55, "left_elbow": 45}, 0.22),
        ({"right_shoulder": 55, "right_elbow": 45,
          "left_shoulder": 130, "left_elbow": 150}, 0.22),
        (REST_POSE, 0.4),
    ],

    # "The Robot" — the one dance move this project can do with a
    # straight face: isolated, single-joint moves in sequence (rather
    # than all 4 joints easing together) reads as mechanical/staccato
    # using the exact same interpolation engine as every other gesture,
    # no special-casing needed.
    "the_robot": [
        ({"right_shoulder": 140}, 0.15),
        ({"right_elbow": 60}, 0.15),
        ({"right_shoulder": 90}, 0.15),
        ({"right_elbow": 90}, 0.15),
        ({"left_shoulder": 140}, 0.15),
        ({"left_elbow": 60}, 0.15),
        ({"left_shoulder": 90}, 0.15),
        ({"left_elbow": 90}, 0.15),
        ({"right_shoulder": 120, "left_shoulder": 120}, 0.2),
        (REST_POSE, 0.4),
    ],

    # Mic drop — raise to a "speaking into a mic" pose, hold for the
    # moment, then a fast dramatic drop out and away.
    "mic_drop": [
        ({"right_shoulder": 110, "right_elbow": 50}, 0.5),
        ({"right_shoulder": 110, "right_elbow": 50}, 0.6),
        ({"right_shoulder": 30, "right_elbow": 170}, 0.35),
        ({"right_shoulder": 30, "right_elbow": 170}, 0.5),
        (REST_POSE, 0.5),
    ],

    # Finger guns — both arms point forward with a quick recoil ("pew
    # pew"), twice.
    "finger_guns": [
        ({"right_shoulder": 100, "right_elbow": 170,
          "left_shoulder": 100, "left_elbow": 170}, 0.35),
        ({"right_shoulder": 95, "right_elbow": 150,
          "left_shoulder": 95, "left_elbow": 150}, 0.15),
        ({"right_shoulder": 100, "right_elbow": 170,
          "left_shoulder": 100, "left_elbow": 170}, 0.35),
        ({"right_shoulder": 95, "right_elbow": 150,
          "left_shoulder": 95, "left_elbow": 150}, 0.15),
        (REST_POSE, 0.5),
    ],
}

KEYWORD_TRIGGERS = {
    "wave_right": ["wave", "hello", "hi there"],
    "wave_both": ["wave both", "wave with both", "big wave"],
    "bow": ["bow", "take a bow"],
    "shrug": ["shrug", "i don't know", "dunno"],
    "dab": ["dab", "do a dab"],
    "flex": ["flex", "show your muscles", "flex your arms"],
    "floss": ["floss", "do the floss", "floss dance"],
    "the_robot": ["do the robot", "robot dance"],
    "mic_drop": ["mic drop", "drop the mic"],
    "finger_guns": ["finger guns", "pew pew"],
    "point": ["point at", "point over there"],
    "full_dance": ["dance", "boogie", "groove"],
    "sit": ["sit down", "rest", "power down"],
}


def match_gesture_from_text(text: str) -> Optional[str]:
    """Picks the most specific (longest) matching trigger phrase across all
    gestures, rather than the first gesture checked in dict order."""
    lower = text.lower()
    best_gesture = None
    best_phrase_len = -1
    for name, phrases in KEYWORD_TRIGGERS.items():
        for phrase in phrases:
            if _phrase_matches(phrase, lower) and len(phrase) > best_phrase_len:
                best_gesture = name
                best_phrase_len = len(phrase)
    return best_gesture


# ---------------------------------------------------------------------------
# Gesture chaining — a composite routine is just several gestures'
# keyframe lists concatenated into one Gesture, so RigMotionEngine needs
# no changes at all: engine.play(build_chain(["bow", "wave_both"])) plays
# the whole routine as one continuous queued item.
# ---------------------------------------------------------------------------

GESTURE_CHAINS: Dict[str, List[str]] = {
    "greeting_routine": ["bow", "wave_both"],
    "showoff_routine": ["wave_right", "full_dance", "bow"],
    "goodnight_routine": ["wave_right", "sit"],
    "meme_routine": ["dab", "flex", "finger_guns"],
}

CHAIN_TRIGGERS = {
    "greeting_routine": ["say hello properly", "greet everyone", "big greeting"],
    "showoff_routine": ["show off", "do your thing", "impress me"],
    "meme_routine": ["hit me with the memes", "meme routine", "do a meme"],
    "goodnight_routine": ["say goodnight", "goodnight routine"],
}


def build_chain(gesture_names: List[str]) -> Gesture:
    """Concatenates named gestures' keyframes into one composite Gesture.
    Order matters: whatever pose one gesture ends on becomes the starting
    point for interpolating into the next, so chains read as one
    continuous motion rather than separate disjoint moves."""
    combined: Gesture = []
    for name in gesture_names:
        if name not in GESTURES:
            raise ValueError(f"unknown gesture {name!r} in chain")
        combined.extend(GESTURES[name])
    return combined


def match_chain_from_text(text: str) -> Optional[str]:
    """Same longest-match rule as match_gesture_from_text, checked
    separately since chains and single gestures are conceptually
    different things a caller might want to distinguish."""
    lower = text.lower()
    best_name, best_len = None, -1
    for name, phrases in CHAIN_TRIGGERS.items():
        for phrase in phrases:
            if _phrase_matches(phrase, lower) and len(phrase) > best_len:
                best_name, best_len = name, len(phrase)
    return best_name


# ---------------------------------------------------------------------------
# Song-synced dance routines — ORIGINAL choreography (not reproducing any
# lyrics or the source film's specific choreography), timed to each real
# song's tempo so it can be started alongside the actual track playing
# from the person's own music source.
#
#   "I Want It That Way" (Backstreet Boys)  — 99 BPM  -> beat = 0.606s
#   "Ek Pal Ka Jeena" (Lucky Ali)            — 104 BPM -> beat = 0.577s
#
# Beat duration = 60 / BPM seconds. Each keyframe below is held for a
# whole number of beats so the motion stays on-tempo.
# ---------------------------------------------------------------------------

BEAT_IWITW = 60 / 99    # 0.606s
BEAT_EKPAL = 60 / 104   # 0.577s

GESTURES["dance_iwitw"] = [
    # syncopated pop routine: alternating arm pumps on the beat
    ({"right_shoulder": 140, "right_elbow": 70, "left_shoulder": 90, "left_elbow": 90}, BEAT_IWITW),
    ({"right_shoulder": 90, "right_elbow": 90, "left_shoulder": 140, "left_elbow": 70}, BEAT_IWITW),
    ({"right_shoulder": 140, "right_elbow": 70, "left_shoulder": 90, "left_elbow": 90}, BEAT_IWITW),
    ({"right_shoulder": 90, "right_elbow": 90, "left_shoulder": 140, "left_elbow": 70}, BEAT_IWITW),
    # both arms up together on the strong beat (chorus-style hit)
    ({"right_shoulder": 150, "right_elbow": 130, "left_shoulder": 150, "left_elbow": 130}, BEAT_IWITW * 2),
    ({"right_shoulder": 90, "right_elbow": 90, "left_shoulder": 90, "left_elbow": 90}, BEAT_IWITW),
    ({"right_shoulder": 140, "right_elbow": 70, "left_shoulder": 90, "left_elbow": 90}, BEAT_IWITW),
    ({"right_shoulder": 90, "right_elbow": 90, "left_shoulder": 140, "left_elbow": 70}, BEAT_IWITW),
]

GESTURES["dance_ekpal"] = [
    # graceful, slower sweeping arcs — both arms move together, wide and
    # smooth rather than snappy, held for 2 beats each for a gentle sway
    ({"right_shoulder": 60, "right_elbow": 110, "left_shoulder": 120, "left_elbow": 70}, BEAT_EKPAL * 2),
    ({"right_shoulder": 120, "right_elbow": 70, "left_shoulder": 60, "left_elbow": 110}, BEAT_EKPAL * 2),
    ({"right_shoulder": 150, "right_elbow": 150, "left_shoulder": 150, "left_elbow": 150}, BEAT_EKPAL * 2),
    ({"right_shoulder": 60, "right_elbow": 110, "left_shoulder": 120, "left_elbow": 70}, BEAT_EKPAL * 2),
    ({"right_shoulder": 90, "right_elbow": 90, "left_shoulder": 90, "left_elbow": 90}, BEAT_EKPAL),
]

KEYWORD_TRIGGERS["dance_iwitw"] = [
    "i want it that way", "backstreet boys", "dance to i want it"
]
KEYWORD_TRIGGERS["dance_ekpal"] = [
    "ek pal ka jeena", "bollywood dance", "kaho naa pyaar hai"
]

# ---------------------------------------------------------------------------
# Macarena — 103 BPM. Unlike the other two song routines, this one combines
# arms AND wheels: the famous hip-wiggle and end-of-cycle turn have no arm
# equivalent, so they're approximated with locomotion instead of skipped.
# Each step is (arm_target_or_None, hold_seconds, locomotion_action_or_None).
# Original choreography approximating the classic move pattern with this
# robot's actual joints — not a reproduction of the song's official video
# choreography.
# ---------------------------------------------------------------------------

BEAT_MACARENA = 60 / 103  # 0.5825s

# NOT a Gesture (List[Tuple[Dict[str,int], float]]) — each entry is a
# 3-tuple (angles_or_None, hold_seconds, locomotion_action_or_None), so
# passing MACARENA_ROUTINE straight to RigMotionEngine.play() will raise
# ValueError unpacking a 3-tuple as 2. Use build_macarena_cycle() and drive
# the arm/locomotion steps separately, the way test_macarena.py does.
MacarenaStep = Tuple[Optional[Dict[str, int]], float, Optional[str]]

MACARENA_ROUTINE: List[MacarenaStep] = [
    # 1: right arm out straight
    ({"right_shoulder": 100, "right_elbow": 90}, BEAT_MACARENA, None),
    # 2: left arm out straight
    ({"left_shoulder": 100, "left_elbow": 90}, BEAT_MACARENA, None),
    # 3: right hand to opposite shoulder (elbow bent across)
    ({"right_shoulder": 70, "right_elbow": 150}, BEAT_MACARENA, None),
    # 4: left hand to opposite shoulder
    ({"left_shoulder": 70, "left_elbow": 150}, BEAT_MACARENA, None),
    # 5: right elbow raised high (behind-head approximation, no head exists)
    ({"right_shoulder": 130, "right_elbow": 170}, BEAT_MACARENA, None),
    # 6: left elbow raised high
    ({"left_shoulder": 130, "left_elbow": 170}, BEAT_MACARENA, None),
    # 7: hands to hips — both arms low and bent in
    ({"right_shoulder": 50, "right_elbow": 130, "left_shoulder": 50, "left_elbow": 130},
     BEAT_MACARENA, None),
    # 8: hip wiggle — no hip joint, so the wheels do a rapid tiny rock
    #    instead of skipping this iconic beat of the routine
    (REST_POSE, BEAT_MACARENA, "wiggle"),
    # cycle end: quarter turn, same as the real dance's rotation each verse
    (None, BEAT_MACARENA * 2, "quarter_turn"),
]

CHAIN_TRIGGERS["macarena"] = ["macarena", "do the macarena"]


def build_macarena_cycle():
    """Returns the routine as-is — kept as a function (rather than a bare
    module-level list only) so the docstring above travels with it in
    tooling that introspects functions."""
    return MACARENA_ROUTINE

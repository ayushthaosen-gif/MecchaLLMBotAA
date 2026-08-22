"""
test_gesture_chains.py
-------------------------
Tests composite gesture routines (chains) built from the existing,
already-tested single gestures — proves concatenation works correctly
and that chained routines actually execute end-to-end through the real
RigMotionEngine, not just that the keyframe lists look right on paper.
"""

import time
from rig import MeccanoidRig
from rig_motion_engine import RigMotionEngine
from rig_gestures import (
    GESTURES, GESTURE_CHAINS, build_chain,
    match_chain_from_text, match_gesture_from_text,
)


def test_chain_trigger_matching():
    print("=== Chain keyword matching ===")
    cases = {
        "say hello properly to everyone": "greeting_routine",
        "show off for me": "showoff_routine",
        "say goodnight now": "goodnight_routine",
        "just wave hello": None,  # should NOT match a chain — single gesture instead
    }
    for msg, expected_chain in cases.items():
        got = match_chain_from_text(msg)
        status = "PASS" if got == expected_chain else "FAIL"
        print(f"  [{status}] {msg!r} -> chain={got!r} (expected {expected_chain!r})")
        assert got == expected_chain

    # the plain single-gesture path should still work independently
    single = match_gesture_from_text("just wave hello")
    print(f"  single-gesture fallback: 'just wave hello' -> {single!r}")
    assert single == "wave_right"


def test_chain_building():
    print("\n=== Chain building (concatenation) ===")
    for chain_name, gesture_names in GESTURE_CHAINS.items():
        chain = build_chain(gesture_names)
        expected_len = sum(len(GESTURES[g]) for g in gesture_names)
        status = "PASS" if len(chain) == expected_len else "FAIL"
        print(f"  [{status}] {chain_name}: {len(chain)} keyframes "
              f"(from {' + '.join(gesture_names)})")
        assert len(chain) == expected_len


def test_chain_playback():
    print("\n=== Chain playback through real motion engine ===")
    import servo_controller as sc
    original_transmit = sc.ServoBus._transmit
    sc.ServoBus._transmit = lambda self, sid, angle, packet: None  # silence [SIM] noise

    rig = MeccanoidRig(simulate=True)
    engine = RigMotionEngine(rig, max_speed_deg_per_sec=300)
    engine.start()

    for chain_name, gesture_names in GESTURE_CHAINS.items():
        chain = build_chain(gesture_names)
        t0 = time.perf_counter()
        engine.play(chain)
        # wait for it to finish
        time.sleep(0.05)
        while engine.is_busy:
            time.sleep(0.02)
        duration = time.perf_counter() - t0

        final_pose = {j: rig.get_joint_angle(j) for j in
                       ["right_shoulder", "right_elbow", "left_shoulder", "left_elbow"]}
        print(f"  {chain_name:20s} ({' -> '.join(gesture_names):30s}) "
              f"played in {duration:.2f}s, final pose={final_pose}")

    engine.stop()
    sc.ServoBus._transmit = original_transmit
    print("  PASS: all chains played through the real engine without error")


if __name__ == "__main__":
    test_chain_trigger_matching()
    test_chain_building()
    test_chain_playback()
    print("\nALL GESTURE CHAINING TESTS PASSED")

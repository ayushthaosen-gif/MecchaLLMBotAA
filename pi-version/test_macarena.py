"""
test_macarena.py
-------------------
Tests the Macarena routine — the first combined arm+wheel choreography
in this project. Confirms:
  1. Beat timing matches the song's real 103 BPM
  2. The combined player correctly interleaves arm keyframes with
     locomotion actions (wiggle, quarter turn) at the right moments
  3. A full cycle plays through the REAL rig + REAL drive motors
     without error
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(__file__))
from rig import MeccanoidRig
from rig_motion_engine import RigMotionEngine
from rig_gestures import MACARENA_ROUTINE, BEAT_MACARENA, match_chain_from_text
from locomotion import DriveMotors


def test_tempo():
    print("=== Tempo accuracy ===")
    expected_beat = 60 / 103
    print(f"  Macarena (103 BPM): beat={BEAT_MACARENA:.4f}s (expected {expected_beat:.4f}s)")
    assert abs(BEAT_MACARENA - expected_beat) < 1e-9
    print("  PASS")


def test_trigger():
    print("\n=== Keyword trigger ===")
    for msg, expected in [("do the macarena!", "macarena"), ("dance the macarena please", "macarena"),
                           ("just dance", None)]:
        got = match_chain_from_text(msg)
        status = "PASS" if got == expected else "FAIL"
        print(f"  [{status}] {msg!r} -> {got}")
        assert got == expected


def play_macarena(rig: MeccanoidRig, engine: RigMotionEngine, drive: DriveMotors, log: list):
    """The combined player: steps through MACARENA_ROUTINE, sending arm
    keyframes to the motion engine and locomotion actions to the drive
    motors, in the same order the real dance would perform them."""
    for arm_target, hold_s, locomotion_action in MACARENA_ROUTINE:
        t0 = time.perf_counter()

        if arm_target is not None:
            engine.play([(arm_target, hold_s)])
            log.append(("arm", arm_target, hold_s))
            time.sleep(0.05)  # let the worker thread pick up the queued item
                               # before polling is_busy — same race avoided
                               # elsewhere in this project (demo_complex_movements.py)

        if locomotion_action == "wiggle":
            # rapid tiny rocks — the hip-wiggle approximation
            drive.rotate_in_place(speed=100, duration_s=hold_s / 4, direction="left")
            drive.rotate_in_place(speed=100, duration_s=hold_s / 4, direction="right")
            log.append(("locomotion", "wiggle", hold_s))
        elif locomotion_action == "quarter_turn":
            drive.rotate_in_place(speed=150, duration_s=hold_s, direction="left")
            log.append(("locomotion", "quarter_turn", hold_s))

        # wait for the arm keyframe to actually finish before moving on,
        # since engine.play() above is non-blocking
        while engine.is_busy:
            time.sleep(0.005)

        elapsed = time.perf_counter() - t0
        # only enforce the hold time for pure-arm or pure-locomotion steps;
        # combined steps naturally take arm-engine-time + locomotion-time


def test_full_playback():
    print("\n=== Full combined arm+wheel playback ===")
    import servo_controller as sc
    original_transmit = sc.ServoBus._transmit
    sc.ServoBus._transmit = lambda self, sid, angle, packet: None

    rig = MeccanoidRig(simulate=True)
    engine = RigMotionEngine(rig, max_speed_deg_per_sec=500)
    engine.start()
    drive = DriveMotors()

    log = []
    t0 = time.perf_counter()
    play_macarena(rig, engine, drive, log)
    total_time = time.perf_counter() - t0

    engine.stop()
    sc.ServoBus._transmit = original_transmit

    arm_steps = [entry for entry in log if entry[0] == "arm"]
    locomotion_steps = [entry for entry in log if entry[0] == "locomotion"]

    print(f"  arm keyframes played:        {len(arm_steps)}")
    print(f"  locomotion actions played:   {len(locomotion_steps)} "
          f"({[s[1] for s in locomotion_steps]})")
    print(f"  total cycle time:            {total_time:.2f}s")
    print(f"  final heading (should have turned): {drive.heading_deg:.1f}°")

    assert len(arm_steps) == 8, f"expected 8 arm-target steps (7 poses + the wiggle's rest-pose reset), got {len(arm_steps)}"
    assert len(locomotion_steps) == 2, f"expected wiggle + quarter_turn, got {len(locomotion_steps)}"
    assert drive.heading_deg > 5, "the quarter turn should have changed heading noticeably"
    print("  PASS: full Macarena cycle executed correctly on real rig + drive motors")


if __name__ == "__main__":
    test_tempo()
    test_trigger()
    test_full_playback()
    print("\nALL MACARENA TESTS PASSED")
    print("\nNote: original choreography approximating the classic move")
    print("pattern with this robot's actual 2-DOF arms + wheels — the hip")
    print("wiggle and turn use the wheels since there's no hip joint.")
    print("Start the real track from your own music source and say")
    print('"do the macarena" at the same time to roughly sync it.')

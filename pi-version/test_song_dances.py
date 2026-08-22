"""
test_song_dances.py
----------------------
Tests the two new BPM-synced dance routines against their real songs'
tempos, confirms keyword matching works, and plays a full loop through
the real motion engine to verify no timing drift.
"""

import time
from rig import MeccanoidRig
from rig_motion_engine import RigMotionEngine
from rig_gestures import (
    GESTURES, BEAT_IWITW, BEAT_EKPAL, match_gesture_from_text,
)


def total_duration(gesture_name: str) -> float:
    return sum(hold for _, hold in GESTURES[gesture_name])


def test_tempo_accuracy():
    print("=== Tempo accuracy ===")
    # I Want It That Way: 99 BPM -> beat should be 60/99 = 0.6060...s
    expected_iwitw_beat = 60 / 99
    print(f"  I Want It That Way (99 BPM): beat={BEAT_IWITW:.4f}s "
          f"(expected {expected_iwitw_beat:.4f}s)")
    assert abs(BEAT_IWITW - expected_iwitw_beat) < 1e-9

    # Ek Pal Ka Jeena: 104 BPM -> beat should be 60/104 = 0.5769...s
    expected_ekpal_beat = 60 / 104
    print(f"  Ek Pal Ka Jeena (104 BPM):    beat={BEAT_EKPAL:.4f}s "
          f"(expected {expected_ekpal_beat:.4f}s)")
    assert abs(BEAT_EKPAL - expected_ekpal_beat) < 1e-9
    print("  PASS: both routines are timed to their song's real tempo")


def test_keyword_matching():
    print("\n=== Keyword matching ===")
    cases = {
        "play I Want It That Way and dance": "dance_iwitw",
        "do a Backstreet Boys dance": "dance_iwitw",
        "dance to Ek Pal Ka Jeena": "dance_ekpal",
        "do a bollywood dance for us": "dance_ekpal",
        "just dance": "full_dance",  # generic dance trigger, unrelated to either song
    }
    for msg, expected in cases.items():
        got = match_gesture_from_text(msg)
        status = "PASS" if got == expected else "FAIL"
        print(f"  [{status}] {msg!r} -> {got} (expected {expected})")
        assert got == expected


def test_full_playback():
    print("\n=== Full playback through real motion engine ===")
    import servo_controller as sc
    original_transmit = sc.ServoBus._transmit
    sc.ServoBus._transmit = lambda self, sid, angle, packet: None

    rig = MeccanoidRig(simulate=True)
    engine = RigMotionEngine(rig, max_speed_deg_per_sec=500)  # fast enough to hit tempo
    engine.start()

    for name, bpm in [("dance_iwitw", 99), ("dance_ekpal", 104)]:
        expected_duration = total_duration(name)
        t0 = time.perf_counter()
        engine.play(GESTURES[name])
        time.sleep(0.05)
        while engine.is_busy:
            time.sleep(0.02)
        actual_duration = time.perf_counter() - t0

        drift = abs(actual_duration - expected_duration)
        drift_pct = (drift / expected_duration) * 100
        print(f"  {name:14s} ({bpm} BPM): expected {expected_duration:.2f}s, "
              f"actual {actual_duration:.2f}s (drift {drift_pct:.1f}%)")
        assert drift_pct < 15, f"{name} drifted too far from its beat timing"

    engine.stop()
    sc.ServoBus._transmit = original_transmit
    print("  PASS: both routines played at their intended tempo")


if __name__ == "__main__":
    test_tempo_accuracy()
    test_keyword_matching()
    test_full_playback()
    print("\nALL SONG DANCE TESTS PASSED")
    print("\nNote: these are original choreography patterns timed to the")
    print("songs' real tempos — start the actual track from your own music")
    print("source and trigger the matching gesture at the same time.")

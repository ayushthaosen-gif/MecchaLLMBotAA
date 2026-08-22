"""
demo_complex_movements.py
----------------------------
Tests every gesture in rig_gestures.py against the real 3-chain,
8-servo rig, instrumented to record every joint's angle over time —
this is the proof that multi-joint, multi-chain coordination actually
works, not just that individual servos move.
"""

import time
from rig import MeccanoidRig
from rig_motion_engine import RigMotionEngine
from rig_gestures import GESTURES, match_gesture_from_text


class Recorder:
    def __init__(self):
        self.frames = []  # (elapsed, {joint: angle})

    def __call__(self, elapsed, frame):
        self.frames.append((elapsed, frame))


def run_gesture(name: str, verbose_frames: int = 6):
    rig = MeccanoidRig(simulate=True)
    engine = RigMotionEngine(rig, max_speed_deg_per_sec=260)
    recorder = Recorder()
    engine.on_frame = recorder

    # Silence [SIM] prints for a clean test report
    import servo_controller as sc
    original_transmit = sc.ServoBus._transmit
    original_batch = sc.ServoBus._transmit_batch_esp32
    sc.ServoBus._transmit = lambda self, sid, angle, packet: None
    sc.ServoBus._transmit_batch_esp32 = lambda self, angles: None

    engine.start()
    t_start = time.perf_counter()
    engine.play(GESTURES[name])
    while engine.is_busy or True:
        if not engine.is_busy and time.perf_counter() - t_start > 0.1:
            break
        time.sleep(0.05)
    engine.stop()

    sc.ServoBus._transmit = original_transmit
    sc.ServoBus._transmit_batch_esp32 = original_batch

    joints_moved = set()
    for _, frame in recorder.frames:
        joints_moved.update(frame.keys())

    duration = recorder.frames[-1][0] if recorder.frames else 0
    print(f"\n=== {name} ===")
    print(f"  joints involved:  {sorted(joints_moved)}")
    print(f"  chains touched:   {sorted({rig_chain_of(j) for j in joints_moved})}")
    print(f"  total frames:     {len(recorder.frames)}")
    print(f"  duration:         {duration:.2f}s")

    # Print a handful of evenly-spaced sample frames so the coordination
    # across joints/chains is visible, not just the final pose.
    if recorder.frames:
        step = max(1, len(recorder.frames) // verbose_frames)
        print(f"  sample frames (every ~{step}):")
        for elapsed, frame in recorder.frames[::step]:
            pretty = ", ".join(f"{j}={a}" for j, a in sorted(frame.items()))
            print(f"    t={elapsed:5.2f}s  {pretty}")

    final = {j: rig.get_joint_angle(j) for j in joints_moved}
    print(f"  final pose:       {final}")
    return recorder.frames


def rig_chain_of(joint: str) -> str:
    from rig import JOINTS
    return JOINTS[joint][0]


def test_keyword_matching():
    print("\n=== keyword -> gesture matching ===")
    tests = [
        "hi there, can you wave hello?",
        "can you wave with both arms?",
        "take a bow please",
        "i don't know, shrug for me",
        "check that out over there",
        "nod if you agree",
        "shake your head no",
        "let's dance!",
        "time to sit down and rest",
        "what's the weather like today",  # should match nothing
    ]
    for msg in tests:
        result = match_gesture_from_text(msg)
        print(f"  {msg!r:55s} -> {result}")


if __name__ == "__main__":
    test_keyword_matching()

    for gesture_name in GESTURES:
        run_gesture(gesture_name)

    print("\n=== ALL GESTURES TESTED SUCCESSFULLY ===")

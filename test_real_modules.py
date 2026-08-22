"""
test_real_modules.py
-----------------------
Unlike test_everything.py (a hand-kept-in-sync reimplementation, see its
docstring), this suite imports the REAL production modules directly, so
a regression in the actual code — not a copy of it — gets caught.

Covers:
  1. pi-version/rig_gestures.py — gesture + gesture-chain matching
  2. esp32-cloud-version/gestures.py — the flat non-rig matcher
  3. esp32-cloud-version/cloud_function/main.py — RobotBrainService,
     including the two-queue non-blocking pattern, using a stub LLM
     client (no real network/API calls, no API key needed) — this
     mirrors simulate_full_run.py's approach. Skipped if `requests`
     isn't installed, since main.py imports it unconditionally.
  4. cloud_function/main.py memory backend selection (default vs Firestore,
     the latter against a fake google.cloud.firestore module)
  5. pi-version/servo_controller.py's real SM-protocol frame/checksum
     logic (ported from alexfrederiksen/MeccanoidForArduino) — hand-
     verified frame bytes for a known angle/output-array input, so a
     future edit that silently breaks the checksum math or angle mapping
     gets caught even though nothing "crashes."

Run with:
    python3 test_real_modules.py
"""

import os
import sys
import time
import threading

ROOT = os.path.dirname(os.path.abspath(__file__))
PI_VERSION = os.path.join(ROOT, "pi-version")
ESP32_CLOUD = os.path.join(ROOT, "esp32-cloud-version")
CLOUD_FUNCTION = os.path.join(ESP32_CLOUD, "cloud_function")

results = {"pass": 0, "fail": 0}


def check(name: str, condition: bool, detail: str = ""):
    status = "PASS" if condition else "FAIL"
    results["pass" if condition else "fail"] += 1
    print(f"  [{status}] {name}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        raise AssertionError(f"{name}: {detail}")


def test_rig_gestures():
    print("\n=== 1. pi-version/rig_gestures.py (real module) ===")
    sys.path.insert(0, PI_VERSION)
    try:
        import rig_gestures  # noqa: real import, not a reimplementation
        importlib_reload = getattr(sys.modules.get("importlib"), "reload", None)

        cases = {
            "hi there, wave hello!": "wave_right",
            "can you wave with both arms?": "wave_both",
            "take a bow": "bow",
            "my elbow hurts": None,          # substring false-positive regression
            "let's visit downtown": None,    # substring false-positive regression
            "no interest in that": None,     # substring false-positive regression
        }
        for msg, expected in cases.items():
            got = rig_gestures.match_gesture_from_text(msg)
            check(f"'{msg}' -> {expected}", got == expected, f"got {got!r}")

        check("'greet everyone' matches greeting_routine chain",
              rig_gestures.match_chain_from_text("greet everyone") == "greeting_routine")

        chain = rig_gestures.build_chain(["bow", "wave_both"])
        check("build_chain concatenates keyframes",
              len(chain) == len(rig_gestures.GESTURES["bow"]) + len(rig_gestures.GESTURES["wave_both"]))

        # Dance/meme gestures — matching, and that every joint value in
        # every keyframe stays within the servos' real 0-180 range.
        meme_cases = {"do a dab": "dab", "do the floss": "floss", "mic drop": "mic_drop"}
        for msg, expected in meme_cases.items():
            got = rig_gestures.match_gesture_from_text(msg)
            check(f"'{msg}' -> {expected}", got == expected, f"got {got!r}")
        check("'robot dance' resolves to the_robot, not full_dance",
              rig_gestures.match_gesture_from_text("let's do a robot dance") == "the_robot")

        for name in ("dab", "flex", "floss", "the_robot", "mic_drop", "finger_guns"):
            for angles, _hold in rig_gestures.GESTURES[name]:
                for joint, angle in angles.items():
                    check(f"{name}: {joint}={angle} within [0, 180]", 0 <= angle <= 180,
                          f"gesture {name!r} has {joint}={angle}")

        check("'meme routine' matches meme_routine chain",
              rig_gestures.match_chain_from_text("meme routine") == "meme_routine")
        meme_chain = rig_gestures.build_chain(rig_gestures.GESTURE_CHAINS["meme_routine"])
        check("meme_routine chain concatenates dab+flex+finger_guns",
              len(meme_chain) == sum(len(rig_gestures.GESTURES[g])
                                      for g in ("dab", "flex", "finger_guns")))
    finally:
        sys.path.remove(PI_VERSION)
        sys.modules.pop("rig_gestures", None)
        sys.modules.pop("rig_motion_engine", None)


def test_flat_gestures():
    print("\n=== 2. esp32-cloud-version/gestures.py (real module) ===")
    sys.path.insert(0, ESP32_CLOUD)
    try:
        import gestures  # noqa: real import
        cases = {
            "wave with both arms please": "wave_both",
            "welcome here, everyone": None,  # substring false-positive regression
            "do a dab": "dab",
            "finger guns": "finger_guns",
        }
        for msg, expected in cases.items():
            got = gestures.match_gesture_from_text(msg)
            check(f"'{msg}' -> {expected}", got == expected, f"got {got!r}")
        for name in ("dab", "flex", "floss", "the_robot", "mic_drop", "finger_guns"):
            for angles, _hold in gestures.GESTURES[name]:
                for servo_id, angle in angles.items():
                    check(f"{name}: servo {servo_id}={angle} within [0, 180]", 0 <= angle <= 180,
                          f"gesture {name!r} has servo {servo_id}={angle}")
    finally:
        sys.path.remove(ESP32_CLOUD)
        sys.modules.pop("gestures", None)


def test_robot_brain_service():
    print("\n=== 3. cloud_function/main.py: RobotBrainService (real module) ===")
    try:
        import requests  # noqa: main.py imports this unconditionally
    except ImportError:
        print("  [SKIP] `requests` not installed — cannot import cloud_function/main.py")
        return

    sys.path.insert(0, CLOUD_FUNCTION)
    try:
        import main as cloud_main  # noqa: real import

        class StubLLM:
            def __init__(self, latency_s=0.05, reply="Sure, here you go!"):
                self.latency_s = latency_s
                self.reply_text = reply

            def chat(self, system_prompt, user_message):
                time.sleep(self.latency_s)
                return self.reply_text

        service = cloud_main.RobotBrainService(llm_client=StubLLM())
        result = service.handle_chat("Come here and wave hello!")
        check("gesture matched via real match_gesture", result.get("gesture") == "wave_right",
              f"got {result}")
        check("locomotion matched via real match_locomotion", result.get("locomotion") == "forward",
              f"got {result}")
        check("motion enqueued in the real SimpleQueue",
              service.motion_queue.next_undelivered() is not None)

        # Non-blocking: motion should be visible in the queue well before
        # the (stubbed) slow LLM call returns.
        service2 = cloud_main.RobotBrainService(llm_client=StubLLM(latency_s=0.5))
        motion_seen_at = {}

        def do_chat():
            service2.handle_chat("wave hello")

        def poll():
            t0 = time.perf_counter()
            while time.perf_counter() - t0 < 0.6:
                if service2.motion_queue.next_undelivered() is not None:
                    motion_seen_at["t"] = time.perf_counter() - t0
                    return
                time.sleep(0.005)

        t1, t2 = threading.Thread(target=do_chat), threading.Thread(target=poll)
        t1.start(); t2.start(); t1.join(); t2.join()
        check("motion visible in real queue before slow LLM call returns",
              motion_seen_at.get("t", 999) < 0.4, f"{motion_seen_at}")

        # LLM failure must not raise unhandled and must still report an error.
        class FailingLLM:
            def chat(self, system_prompt, user_message):
                raise RuntimeError("simulated network failure")

        service3 = cloud_main.RobotBrainService(llm_client=FailingLLM())
        result3 = service3.handle_chat("hello there")
        check("LLM failure returns an error envelope instead of raising",
              "error" in result3, f"got {result3}")

        # Substring false-positive regression, via the real matcher.
        check("'elbow' doesn't falsely trigger bow (real match_gesture)",
              cloud_main.match_gesture("my elbow hurts") is None)
    finally:
        sys.path.remove(CLOUD_FUNCTION)
        sys.modules.pop("main", None)
        sys.modules.pop("cloud_llm_backends", None)


def test_dead_band_smoothing():
    print("\n=== 3b. pi-version/motion_engine.py: dead-band smoothing (real module) ===")
    sys.path.insert(0, PI_VERSION)
    try:
        import servo_controller as sc
        import motion_engine as me

        bus = sc.ServoBus(sc.ServoBusConfig(simulate=True, servo_count=4))
        engine = me.MotionEngine(bus)

        # A move smaller than DEAD_BAND_DEG should snap in one command, not
        # spend several interpolation steps easing an invisible correction.
        bus.set_angle(0, 90)
        before = len(bus._outputs)  # sanity: bus still usable after direct set_angle
        check("bus usable before dead-band check", before == sc.MAX_CHAIN)

        calls = []
        original_set_angles = bus.set_angles
        bus.set_angles = lambda angles: (calls.append(dict(angles)), original_set_angles(angles))[-1]
        engine._move_to({0: 91}, 0.05)  # 1 degree — below DEAD_BAND_DEG (1.5)
        check("sub-dead-band move issues exactly one set_angles call",
              len(calls) == 1, f"got {len(calls)} calls: {calls}")
        check("sub-dead-band move lands exactly on target",
              bus.get_angle(0) == 91)

        calls.clear()
        engine._move_to({0: 150}, 0.05)  # 59 degrees — well above the dead-band
        check("above-dead-band move issues more than one set_angles call (real interpolation)",
              len(calls) > 1, f"got {len(calls)} calls")
        check("above-dead-band move lands exactly on target",
              bus.get_angle(0) == 150)
    finally:
        sys.path.remove(PI_VERSION)
        sys.modules.pop("servo_controller", None)
        sys.modules.pop("motion_engine", None)
        sys.modules.pop("gestures", None)


def test_memory_backend_selection():
    print("\n=== 4. cloud_function/main.py: memory backend selection (real module) ===")
    try:
        import requests  # noqa: main.py imports this unconditionally
    except ImportError:
        print("  [SKIP] `requests` not installed — cannot import cloud_function/main.py")
        return

    sys.path.insert(0, CLOUD_FUNCTION)
    try:
        os.environ.pop("MEMORY_BACKEND", None)
        for mod in ("main", "firestore_memory"):
            sys.modules.pop(mod, None)
        import main as cloud_main  # noqa: real import

        check("default MEMORY_BACKEND builds the in-process MemoryStore",
              isinstance(cloud_main.build_memory_store(), cloud_main.MemoryStore))

        # Exercise FirestoreMemoryStore's query/format logic against a fake
        # `google.cloud.firestore` module, so this doesn't need a real GCP
        # project, network access, or the google-cloud-firestore package
        # installed to catch a broken query/formatting change.
        fake_firestore = _build_fake_firestore_module()
        sys.modules["google.cloud.firestore"] = fake_firestore
        sys.modules.setdefault("google", type(sys)("google"))
        sys.modules.setdefault("google.cloud", type(sys)("google.cloud"))
        sys.modules["google.cloud"].firestore = fake_firestore
        sys.modules.pop("firestore_memory", None)

        os.environ["MEMORY_BACKEND"] = "firestore"
        store = cloud_main.build_memory_store()
        check("MEMORY_BACKEND=firestore builds a FirestoreMemoryStore",
              type(store).__name__ == "FirestoreMemoryStore")

        store.append("you", "hello there")
        store.append("robot", "hi! good to hear from you")
        text = store.recent_text()
        check("FirestoreMemoryStore.recent_text formats appended turns",
              "you: hello there" in text and "robot: hi! good to hear from you" in text,
              f"got {text!r}")
    finally:
        os.environ.pop("MEMORY_BACKEND", None)
        sys.path.remove(CLOUD_FUNCTION)
        for mod in ("main", "cloud_llm_backends", "firestore_memory",
                    "google.cloud.firestore", "google.cloud", "google"):
            sys.modules.pop(mod, None)


def _build_fake_firestore_module():
    """Minimal stand-in for google.cloud.firestore covering only what
    FirestoreMemoryStore actually calls: Client().collection(...).add(...)
    and .order_by(...).limit(...).stream()."""
    import types
    import datetime as _dt

    class _FakeDoc:
        def __init__(self, data):
            self._data = data

        def to_dict(self):
            return self._data

    class _FakeQuery:
        def __init__(self, docs):
            self._docs = docs

        def order_by(self, *a, **k):
            return self

        def limit(self, *a, **k):
            return self

        def stream(self):
            return [_FakeDoc(d) for d in reversed(self._docs)]  # newest first

    class _FakeCollection:
        def __init__(self):
            self._docs = []

        def add(self, data):
            data = dict(data)
            if data.get("created_at") == "__SERVER_TIMESTAMP__":
                data["created_at"] = _dt.datetime.now(_dt.timezone.utc)
            self._docs.append(data)

        def order_by(self, *a, **k):
            return _FakeQuery(self._docs)

    class _FakeClient:
        def __init__(self):
            self._collections = {}

        def collection(self, name):
            return self._collections.setdefault(name, _FakeCollection())

    mod = types.ModuleType("google.cloud.firestore")
    mod.Client = _FakeClient
    mod.SERVER_TIMESTAMP = "__SERVER_TIMESTAMP__"

    class _Query:
        DESCENDING = "DESCENDING"

    mod.Query = _Query
    return mod


def test_servo_protocol():
    print("\n=== 5. pi-version/servo_controller.py: real SM protocol (real module) ===")
    sys.path.insert(0, PI_VERSION)
    try:
        import servo_controller as sc  # noqa: real import

        # Angle <-> byte mapping (SERVO_MIN=0x18, SERVO_MAX=0xE8).
        check("_angle_to_byte(0) == SERVO_MIN", sc._angle_to_byte(0) == sc.SERVO_MIN)
        check("_angle_to_byte(180) == SERVO_MAX", sc._angle_to_byte(180) == sc.SERVO_MAX)
        check("_angle_to_byte(90) == 0x80 (mid-travel)", sc._angle_to_byte(90) == 0x80)
        check("_byte_to_angle is the inverse of _angle_to_byte",
              sc._byte_to_angle(sc._angle_to_byte(120)) == 120)

        # Checksum — hand-verified: outputs all 0x80 (angle 90 on all 4
        # slots), poll_index 0 -> 0x20; poll_index 1 -> 0x21 (only the low
        # nibble changes). Worked out by hand from Chain::calculateCheckSum:
        # sum=0x200; +=(sum>>8)=0x202; +=(sum<<4)=0x202+0x2020=0x2222;
        # &0xF0=0x20; |poll_index.
        outputs_all_90 = [0x80, 0x80, 0x80, 0x80]
        check("_checksum(all-90 outputs, poll=0) == 0x20",
              sc._checksum(outputs_all_90, 0) == 0x20,
              f"got 0x{sc._checksum(outputs_all_90, 0):02X}")
        check("_checksum(all-90 outputs, poll=1) == 0x21",
              sc._checksum(outputs_all_90, 1) == 0x21,
              f"got 0x{sc._checksum(outputs_all_90, 1):02X}")

        # Full frame via the real ServoBus, in simulation.
        bus = sc.ServoBus(sc.ServoBusConfig(simulate=True, servo_count=4))
        bus.set_angle(0, 90)
        frame = bus._build_frame()
        check("frame starts with HEADER_BYTE", frame[0] == sc.HEADER_BYTE)
        check("frame is 6 bytes (header + 4 outputs + checksum)", len(frame) == 6)
        check("frame's checksum matches _checksum() on current outputs",
              frame[5] == sc._checksum(bus._outputs, bus._poll_index),
              f"frame={frame.hex()}")

        # Real transport='direct' + simulate=False must fail loudly, not
        # silently write wrong bytes at real servos over pyserial.
        raised = False
        try:
            sc.ServoBus(sc.ServoBusConfig(simulate=False, transport="direct"))
        except NotImplementedError:
            raised = True
        check("real transport='direct' raises NotImplementedError", raised)
    finally:
        sys.path.remove(PI_VERSION)
        sys.modules.pop("servo_controller", None)


def test_eyes_sync_and_expression_colors():
    print("\n=== 6. eyes.py sync + expression color palette (real modules) ===")
    import filecmp
    paths = [
        os.path.join(PI_VERSION, "eyes.py"),
        os.path.join(ROOT, "standalone-tools", "eyes.py"),
        os.path.join(ESP32_CLOUD, "cloud_function", "eyes.py"),
    ]
    check("all three eyes.py copies are byte-identical",
          filecmp.cmp(paths[0], paths[1], shallow=False) and filecmp.cmp(paths[0], paths[2], shallow=False))

    sys.path.insert(0, PI_VERSION)
    try:
        import eyes as eyes_mod  # noqa: real import
        expected = {"happy", "calm", "excited", "concerned", "neutral",
                    "angry", "sad", "surprised", "fear", "disgust"}
        check("MOOD_COLORS has all LLM-reply + face-expression moods",
              expected.issubset(set(eyes_mod.MOOD_COLORS)),
              f"missing {expected - set(eyes_mod.MOOD_COLORS)}")
        for mood, rgb in eyes_mod.MOOD_COLORS.items():
            check(f"{mood}: color {rgb} within 0-7 per channel",
                  all(0 <= c <= 7 for c in rgb), f"got {rgb}")
        # Expression colors must be distinct hues from each other and from
        # the LLM-mood set — the whole point of designing them separately.
        expression_moods = ["angry", "sad", "surprised", "fear", "disgust"]
        colors = [eyes_mod.MOOD_COLORS[m] for m in expression_moods]
        check("all 5 expression colors are pairwise distinct",
              len(set(colors)) == len(colors), f"got {colors}")

        module = eyes_mod.EyeModule(simulate=True)
        applied = module.set_mood("surprised")
        check("set_mood('surprised') stores the surprised color",
              module.current_color == eyes_mod.MOOD_COLORS["surprised"])
        module.set_mood("not_a_real_mood")
        check("unknown mood falls back to neutral",
              module.current_color == eyes_mod.MOOD_COLORS["neutral"])
    finally:
        sys.path.remove(PI_VERSION)
        sys.modules.pop("eyes", None)


def test_mirror_control_mood_and_locomotion():
    print("\n=== 7. pi-version/mirror_control.py: mood + follow-mode locomotion (real module) ===")
    sys.path.insert(0, PI_VERSION)
    try:
        import servo_controller as sc
        import eyes as eyes_mod
        import locomotion as loco_mod
        from mirror_control import MirrorController, LOCOMOTION_ACTIONS

        bus = sc.ServoBus(sc.ServoBusConfig(simulate=True, servo_count=4))
        eye = eyes_mod.EyeModule(simulate=True)
        drive = loco_mod.DriveMotors(loco_mod.DriveConfig(simulate=True))
        mc = MirrorController(bus, eyes=eye, drive=drive)

        check("apply_mood applies and returns the mood",
              mc.apply_mood("happy") == "happy")
        check("apply_mood is rate-limited on immediate repeat",
              mc.apply_mood("angry") is None)
        check("eye color actually changed to happy's color",
              eye.current_color == eyes_mod.MOOD_COLORS["happy"])

        check("apply_locomotion('forward') applies and returns the action",
              mc.apply_locomotion("forward") == "forward")
        check("apply_locomotion is rate-limited on immediate identical repeat",
              mc.apply_locomotion("forward") is None)
        check("apply_locomotion rejects an invalid action",
              mc.apply_locomotion("sideways") is None)
        check("LOCOMOTION_ACTIONS is exactly forward/backward/stop",
              LOCOMOTION_ACTIONS == {"forward", "backward", "stop"})

        # No eyes/drive wired up -> both must no-op cleanly, never raise.
        bare = MirrorController(sc.ServoBus(sc.ServoBusConfig(simulate=True, servo_count=4)))
        check("apply_mood with no eyes wired up returns None, doesn't raise",
              bare.apply_mood("happy") is None)
        check("apply_locomotion with no drive wired up returns None, doesn't raise",
              bare.apply_locomotion("forward") is None)
        bare.close()
        mc.close()
    finally:
        sys.path.remove(PI_VERSION)
        for m in ("servo_controller", "eyes", "locomotion", "mirror_control"):
            sys.modules.pop(m, None)


if __name__ == "__main__":
    tests = [test_rig_gestures, test_flat_gestures, test_robot_brain_service,
              test_dead_band_smoothing, test_memory_backend_selection, test_servo_protocol,
              test_eyes_sync_and_expression_colors, test_mirror_control_mood_and_locomotion]
    failed = False
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed = True
            print(f"  !! {t.__name__} FAILED: {e}")
        except Exception as e:
            failed = True
            print(f"  !! {t.__name__} ERRORED: {e!r}")

    print(f"\n{'='*50}")
    print(f"RESULTS: {results['pass']} passed, {results['fail']} failed")
    print(f"{'='*50}")
    sys.exit(1 if failed else 0)

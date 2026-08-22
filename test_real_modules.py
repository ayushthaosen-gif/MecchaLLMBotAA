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
        }
        for msg, expected in cases.items():
            got = gestures.match_gesture_from_text(msg)
            check(f"'{msg}' -> {expected}", got == expected, f"got {got!r}")
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


if __name__ == "__main__":
    tests = [test_rig_gestures, test_flat_gestures, test_robot_brain_service,
              test_memory_backend_selection]
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

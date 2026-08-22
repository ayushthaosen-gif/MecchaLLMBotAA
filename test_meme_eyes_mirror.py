import importlib.util
import time
from pathlib import Path

ROOT = Path(__file__).parent
NEW = {"aura_farm", "six_seven", "npc_mode", "facepalm", "success_pump", "side_eye"}
CLAUDE = {"dab", "flex", "floss", "the_robot", "mic_drop", "finger_guns"}


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_new_gestures_match_and_remain_safe():
    for label, path in (
        ("pi", "pi-version/gestures.py"),
        ("cloud", "esp32-cloud-version/gestures.py"),
    ):
        module = load("gestures_" + label, path)
        assert NEW <= module.GESTURES.keys()
        assert module.match_gesture_from_text("please farm aura") == "aura_farm"
        assert module.match_gesture_from_text("bruh moment") == "facepalm"
        for name in NEW:
            for pose, hold in module.GESTURES[name]:
                assert hold > 0
                assert all(15 <= angle <= 165 for angle in pose.values())


def test_eye_cues_cover_old_and_new_memes_and_hold_priority():
    eyes_module = load("eye_cues", "esp32-cloud-version/cloud_function/eyes.py")
    assert NEW | CLAUDE <= eyes_module.GESTURE_EYE_CUES.keys()
    eyes = eyes_module.EyeModule(simulate=False)
    assert eyes.set_gesture_cue("aura_farm") == "aura_farm"
    assert eyes.current_color == (5, 0, 7)
    history_len = len(eyes.history)
    eyes.set_status("thinking")
    eyes.set_mood("happy")
    assert len(eyes.history) == history_len
    eyes.set_status("error")
    assert eyes.current_color == eyes_module.STATUS_COLORS["error"]
    assert eyes.set_gesture_cue("unknown") is None


class FakeBus:
    def __init__(self):
        self.calls = []

    def set_angles(self, angles):
        self.calls.append(dict(angles))


def test_mirror_rate_step_validation_and_deadman():
    module = load("mirror_control", "pi-version/mirror_control.py")
    bus = FakeBus()
    mirror = module.MirrorController(bus)
    mirror.DEADMAN_S = 0.05
    try:
        result = mirror.apply({
            "right_shoulder": 180,
            "right_elbow": 0,
            "left_shoulder": 120,
            "left_elbow": 60,
        })
        assert result == {
            "right_shoulder": 102,
            "right_elbow": 78,
            "left_shoulder": 102,
            "left_elbow": 78,
        }
        assert mirror.apply({
            "right_shoulder": 90,
            "right_elbow": 90,
            "left_shoulder": 90,
            "left_elbow": 90,
        }) is None
        time.sleep(0.15)
        assert bus.calls[-1] == module.REST
        try:
            mirror.apply({"right_shoulder": 90})
            raise AssertionError("incomplete pose accepted")
        except ValueError:
            pass
    finally:
        mirror.close()


if __name__ == "__main__":
    test_new_gestures_match_and_remain_safe()
    test_eye_cues_cover_old_and_new_memes_and_hold_priority()
    test_mirror_rate_step_validation_and_deadman()
    print("3 feature tests passed")
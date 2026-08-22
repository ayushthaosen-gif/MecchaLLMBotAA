"""Regression checks for reconciled meme gestures, eye cues, and pose mirroring."""
import importlib
import sys
import time
from pathlib import Path

ROOT=Path(__file__).parent
PI=str(ROOT/"pi-version")
CLOUD=str(ROOT/"esp32-cloud-version")
CF=str(ROOT/"esp32-cloud-version"/"cloud_function")
MODERN={"aura_farm","six_seven","npc_mode","facepalm","success_pump","side_eye"}

def load_from(path,name):
    sys.path.insert(0,path)
    try:return importlib.import_module(name)
    finally:sys.path.remove(path)

def test_modern_gestures_match_and_stay_safe():
    for path in (PI,CLOUD):
        sys.modules.pop("gestures",None)
        g=load_from(path,"gestures")
        assert g.match_gesture_from_text("show me maximum aura")=="aura_farm"
        assert g.match_gesture_from_text("do the 67 gesture")=="six_seven"
        assert g.match_gesture_from_text("bruh moment")=="facepalm"
        for name in MODERN:
            assert name in g.GESTURES
            assert all(15<=angle<=165 for frame,_ in g.GESTURES[name] for angle in frame.values())

def test_eye_cues_cover_claude_and_modern_memes():
    sys.modules.pop("eyes",None)
    eyes_mod=load_from(CF,"eyes")
    expected=MODERN|{"dab","flex","floss","the_robot","mic_drop","finger_guns"}
    assert expected<=set(eyes_mod.GESTURE_EYE_CUES)
    eyes=eyes_mod.EyeModule(simulate=False)
    assert eyes.set_gesture_cue("aura_farm")=="aura_farm"
    assert eyes.current_color==(5,0,7)
    history_len=len(eyes.history)
    eyes.set_status("thinking");eyes.set_mood("happy")
    assert len(eyes.history)==history_len
    eyes.set_status("error")
    assert eyes.current_color==eyes_mod.STATUS_COLORS["error"]
    assert eyes.set_gesture_cue("unknown") is None

class FakeBus:
    def __init__(self):self.frames=[]
    def set_angles(self,angles):self.frames.append(dict(angles))

def test_mirror_limits_and_deadman():
    sys.modules.pop("mirror_control",None)
    m=load_from(PI,"mirror_control")
    bus=FakeBus();ctl=m.MirrorController(bus);ctl.MIN_INTERVAL_S=0;ctl.DEADMAN_S=.05
    applied=ctl.apply({"right_shoulder":180,"right_elbow":0,"left_shoulder":120,"left_elbow":60})
    assert applied=={0:102,1:78,2:102,3:78}
    assert ctl.active
    try:ctl.apply({"right_shoulder":90})
    except ValueError:pass
    else:raise AssertionError("incomplete pose accepted")
    try:ctl.apply({"right_shoulder":float("nan"),"right_elbow":90,"left_shoulder":90,"left_elbow":90})
    except ValueError:pass
    else:raise AssertionError("non-finite pose accepted")
    time.sleep(.16)
    assert bus.frames[-1]==m.REST
    assert not ctl.active
    ctl.close()

if __name__=="__main__":
    test_modern_gestures_match_and_stay_safe()
    test_eye_cues_cover_claude_and_modern_memes()
    test_mirror_limits_and_deadman()
    print("meme/eye/mirror tests passed")

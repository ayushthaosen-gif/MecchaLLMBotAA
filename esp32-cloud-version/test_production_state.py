"""Production-module tests for robot-scoped cloud state and command delivery."""

import sys
from pathlib import Path

CLOUD_DIR = Path(__file__).parent / "cloud_function"
sys.path.insert(0, str(CLOUD_DIR))

from main import RobotBrainService, get_service  # noqa: E402
from state_backend import InMemoryStateBackend  # noqa: E402


class FakeLLM:
    def chat(self, system_prompt, message):
        return f"reply to {message}"


def test_queue_ack_removes_item():
    backend = InMemoryStateBackend()
    item = backend.enqueue("robot-a", "motion", "wave_right")
    assert backend.next_item("robot-a", "motion") == item
    assert backend.ack("robot-a", "motion", item.id) is True
    assert backend.next_item("robot-a", "motion") is None
    assert backend.ack("robot-a", "motion", item.id) is False


def test_robot_state_is_isolated():
    backend = InMemoryStateBackend()
    first = RobotBrainService(FakeLLM(), backend=backend, robot_id="robot-a")
    second = RobotBrainService(FakeLLM(), backend=backend, robot_id="robot-b")

    result = first.handle_chat("come here and wave hello")
    assert result["gesture"] == "wave_right"
    assert result["locomotion"] == "forward"
    assert first.motion_queue.next_undelivered().value == "wave_right"
    assert first.locomotion_queue.next_undelivered().value == "forward"
    assert second.motion_queue.next_undelivered() is None
    assert second.locomotion_queue.next_undelivered() is None
    assert "come here" in backend.recent_memory("robot-a")
    assert backend.recent_memory("robot-b") == ""


def test_robot_id_validation():
    try:
        get_service("../not-safe")
    except ValueError:
        pass
    else:
        raise AssertionError("unsafe robot id was accepted")


if __name__ == "__main__":
    tests = [test_queue_ack_removes_item, test_robot_state_is_isolated, test_robot_id_validation]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")

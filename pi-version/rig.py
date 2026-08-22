"""
rig.py
------
Models the Meccanoid PERSONAL ROBOT 2.0 (2ft, non-XL) servo topology,
confirmed from Meccano's own product listing and matching retail
bills-of-materials: 4 smart servos total, ALL in the arms — 2 per arm
(shoulder, elbow).

CORRECTION: an earlier version of this file modeled the XL 2.0 (4ft,
8 servos including a head/neck pair) before the exact product was
confirmed to be the smaller Personal Robot 2.0. That version is wrong
for this robot and has been replaced.

  Right arm: 2 servos (shoulder, elbow)
  Left arm:  2 servos (shoulder, elbow)

  (plus 2 simple DC gearbox motors for the wheeled feet — not smart
  servos, not modeled here)

IMPORTANT: this model has NO head or neck servo. Its head does not
move under motor control at all — any gesture implying head motion
(nodding, shaking, looking toward something) is not physically possible
on this specific robot. Gestures in rig_gestures.py only use the 4
joints that actually exist.
"""

from typing import Dict, Tuple, Optional

from servo_controller import ServoBus, ServoBusConfig

# joint name -> (chain name, local servo id within that chain's bus)
JOINTS: Dict[str, Tuple[str, int]] = {
    "right_shoulder": ("right_arm", 0),
    "right_elbow":    ("right_arm", 1),
    "left_shoulder":  ("left_arm", 0),
    "left_elbow":     ("left_arm", 1),
}

CHAIN_SERVO_COUNTS = {"right_arm": 2, "left_arm": 2}


class MeccanoidRig:
    """Owns two ServoBus instances (one per real arm chain) and lets
    calling code think in named joints instead of (chain, id) pairs."""

    def __init__(
        self,
        simulate: bool = True,
        transport: str = "direct",
        calibration_offsets: Optional[Dict[str, Dict[int, int]]] = None,
        esp32_ports: Optional[Dict[str, str]] = None,
    ):
        calibration_offsets = calibration_offsets or {}
        esp32_ports = esp32_ports or {}

        self.chains: Dict[str, ServoBus] = {}
        for chain_name, count in CHAIN_SERVO_COUNTS.items():
            self.chains[chain_name] = ServoBus(ServoBusConfig(
                simulate=simulate,
                servo_count=count,
                transport=transport,
                calibration_offsets=calibration_offsets.get(chain_name, {}),
                esp32_port=esp32_ports.get(chain_name, "/dev/ttyUSB0"),
            ))

    def set_joint_angles(self, joint_angles: Dict[str, int]) -> None:
        """Groups the requested joints by chain so each chain gets one
        batched call, letting both arms update in the same instant
        instead of one after another."""
        by_chain: Dict[str, Dict[int, int]] = {}
        for joint, angle in joint_angles.items():
            chain, sid = JOINTS[joint]
            by_chain.setdefault(chain, {})[sid] = angle
        for chain, angles in by_chain.items():
            self.chains[chain].set_angles(angles)

    def get_joint_angle(self, joint: str) -> int:
        chain, sid = JOINTS[joint]
        return self.chains[chain].get_angle(sid)

    def close(self) -> None:
        for bus in self.chains.values():
            bus.close()


if __name__ == "__main__":
    rig = MeccanoidRig(simulate=True)
    rig.set_joint_angles({
        "right_shoulder": 150,
        "left_shoulder": 150,
    })
    print("right_shoulder:", rig.get_joint_angle("right_shoulder"))
    print("left_shoulder:", rig.get_joint_angle("left_shoulder"))

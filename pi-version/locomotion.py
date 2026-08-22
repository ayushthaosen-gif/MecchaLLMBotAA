"""
locomotion.py
--------------
The Meccanoid Personal Robot 2.0's wheeled feet: 2 simple DC gearbox
motors, NOT smart servos. Confirmed from the same product research as
rig.py's arm topology — these motors have no position feedback and
aren't on the daisy-chain servo bus at all. Control is direction +
speed, not angle targets, so this is a genuinely different interface
from ServoBus/MeccanoidRig, not a variation of it.

Because each wheel is driven independently (differential drive — same
principle as a tank or wheelchair), several distinct turn types are
possible, not just "turn":

  - ROTATE IN PLACE: wheels equal speed, OPPOSITE direction. The robot
    spins on its own footprint without translating at all.
  - ARC TURN: both wheels same direction, DIFFERENT speeds. The robot
    curves while still moving forward/backward, like a car.
  - PIVOT TURN: one wheel stopped, the other driving. The robot swings
    around the stopped wheel like a hinge.

Real hardware notes:
  - No feedback: the code has no way to know how far the robot actually
    moved — only how long it commanded the motors to run. Real distance
    depends on floor surface, battery charge, and drivetrain wear.
  - The pose tracking below (x, y, heading) is DEAD RECKONING from
    commanded speed and time only — it assumes idealized, slip-free
    wheels. On real hardware this drifts from the true position quickly;
    it's useful for understanding what a given command *should* do, not
    as an accurate real-world position estimate.
"""

import time
import math
import threading
from dataclasses import dataclass
from typing import Optional, List, Tuple


@dataclass
class DriveConfig:
    simulate: bool = True
    max_speed: int = 255       # PWM duty cycle ceiling, not a real-world unit
    wheelbase_m: float = 0.20  # distance between the two wheels, estimate
    max_linear_mps: float = 0.3  # assumed real-world speed at max_speed, estimate


class DriveMotors:
    """Two independent wheel motors, differential drive. Left/right speed
    range: -max_speed (full reverse) to +max_speed (full forward), 0 =
    stopped. Also tracks an idealized (x, y, heading) pose via dead
    reckoning so turn types are visible as numbers, not just motor duty
    cycles — see the module docstring for why this drifts from reality."""

    def __init__(self, config: Optional[DriveConfig] = None):
        self.config = config or DriveConfig()
        self._lock = threading.Lock()
        self.left_speed = 0
        self.right_speed = 0
        self.x = 0.0
        self.y = 0.0
        self.heading_deg = 0.0  # 0 = facing its original forward direction
        self.pose_history: List[Tuple[float, float, float, float]] = [(0.0, 0.0, 0.0, 0.0)]
        self._t0 = time.perf_counter()

    def _speed_to_mps(self, pwm: int) -> float:
        return (pwm / self.config.max_speed) * self.config.max_linear_mps

    def set_speeds(self, left: int, right: int) -> None:
        left = max(-self.config.max_speed, min(self.config.max_speed, left))
        right = max(-self.config.max_speed, min(self.config.max_speed, right))
        with self._lock:
            self.left_speed = left
            self.right_speed = right
            if self.config.simulate:
                print(f"[SIM/drive] left={left:+4d}  right={right:+4d}")

    def _integrate_pose(self, duration_s: float, steps: int = 20) -> None:
        """Dead-reckoning integration of the current wheel speeds over
        `duration_s`, updating self.x/y/heading_deg and appending to
        pose_history — this is what turns motor commands into a visible
        turning/driving trajectory."""
        dt = duration_s / steps
        wheelbase = self.config.wheelbase_m
        for _ in range(steps):
            v_left = self._speed_to_mps(self.left_speed)
            v_right = self._speed_to_mps(self.right_speed)
            v = (v_left + v_right) / 2.0          # linear velocity
            omega = (v_right - v_left) / wheelbase  # angular velocity, rad/s

            theta_rad = math.radians(self.heading_deg)
            self.x += v * math.cos(theta_rad) * dt
            self.y += v * math.sin(theta_rad) * dt
            self.heading_deg += math.degrees(omega * dt)
            self.heading_deg %= 360

            elapsed = time.perf_counter() - self._t0
            self.pose_history.append((elapsed, self.x, self.y, self.heading_deg))

    def _run(self, left: int, right: int, duration_s: float) -> None:
        self.set_speeds(left, right)
        self._integrate_pose(duration_s)
        self.stop()

    # -- named movement primitives ------------------------------------------

    def forward(self, speed: int = 200, duration_s: float = 1.0) -> None:
        self._run(speed, speed, duration_s)

    def backward(self, speed: int = 200, duration_s: float = 1.0) -> None:
        self._run(-speed, -speed, duration_s)

    def rotate_in_place(self, speed: int = 180, duration_s: float = 0.6, direction: str = "left") -> None:
        """Spins on the spot — x, y should stay ~0, only heading changes."""
        sign = -1 if direction == "left" else 1
        self._run(sign * speed, -sign * speed, duration_s)

    def arc_turn(self, forward_speed: int = 200, turn_bias: int = 100, duration_s: float = 1.0, direction: str = "left") -> None:
        """Curves while moving — both wheels same direction, different
        speeds, like a car turning. `turn_bias` is how much slower the
        inside wheel runs."""
        if direction == "left":
            self._run(forward_speed - turn_bias, forward_speed, duration_s)
        else:
            self._run(forward_speed, forward_speed - turn_bias, duration_s)

    def pivot_turn(self, speed: int = 180, duration_s: float = 0.8, direction: str = "left") -> None:
        """One wheel stopped, the other drives — swings around the
        stopped wheel like a hinge, rather than spinning on the center."""
        if direction == "left":
            self._run(0, speed, duration_s)
        else:
            self._run(speed, 0, duration_s)

    def turn_left(self, speed: int = 180, duration_s: float = 0.6) -> None:
        self.rotate_in_place(speed, duration_s, direction="left")

    def turn_right(self, speed: int = 180, duration_s: float = 0.6) -> None:
        self.rotate_in_place(speed, duration_s, direction="right")

    def stop(self) -> None:
        self.set_speeds(0, 0)


if __name__ == "__main__":
    drive = DriveMotors()
    drive.forward(duration_s=0.3)
    drive.turn_right(duration_s=0.2)
    drive.backward(duration_s=0.3)
    drive.stop()
    print("done")
    print(f"final pose: x={drive.x:.3f}m y={drive.y:.3f}m heading={drive.heading_deg:.1f}°")


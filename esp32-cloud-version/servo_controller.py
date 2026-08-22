"""
servo_controller.py
--------------------
Low-level interface to the daisy-chained Meccanoid smart servos.

IMPORTANT — read before wiring anything up:
Meccano never published an official serial protocol for the smart servos,
so there is no single "correct" byte format to hand you here. Community
reverse-engineering efforts (logic-analyzer captures of the stock
MeccaBrain talking to the servo chain) are the only source for this, and
the exact bytes can differ between Meccanoid revisions (G15KS vs Meccanoid
2.0 vs Personal Robot). Treat PACKET FORMAT below as the one thing in this
file you need to verify or replace with your own captured protocol before
trusting it on real hardware.

What this module gives you regardless of the exact byte format:
  - A clean `ServoBus` class your gesture/motion code can call without
    caring about wire-level details.
  - A SIMULATION mode (default) that logs commands instead of writing to
    a serial port, so you can develop and test the rest of the stack
    (gestures, threading, Flask routes) before any hardware is attached.
  - A single place to plug in the real packet bytes once you've verified
    them against your own logic-analyzer trace.
"""

from __future__ import annotations
import time
import threading
from dataclasses import dataclass, field
from typing import Optional

try:
    import serial  # pyserial — only required in REAL mode
except ImportError:
    serial = None


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class ServoBusConfig:
    port: str = "/dev/serial0"      # Pi UART (only used in transport="direct")
    baudrate: int = 9600            # placeholder — confirm against your capture
    servo_count: int = 4            # how many smart servos are daisy-chained
    simulate: bool = True           # False only once wired to real hardware
    min_angle: int = 0
    max_angle: int = 180
    # Per-servo trim, e.g. {2: -3} means "when asked for 90, actually send 87"
    # because that servo's true mechanical center is 3° off. Fill these in
    # after physically checking each joint's rest position.
    calibration_offsets: dict = field(default_factory=dict)

    # --- transport selection -------------------------------------------
    # "direct" — Pi builds Meccanoid bus packets itself and writes them to
    #            its own UART (through the level shifter). Simple, but
    #            timing is at the mercy of Python/Linux scheduling — see
    #            stress_test_gpio_timing.py for why that can jitter under
    #            load.
    # "esp32"  — Pi sends a plain-text target-angle command over USB
    #            serial to an ESP32, which builds the real Meccanoid bus
    #            packets and drives the servo timing itself on a
    #            dedicated FreeRTOS core, isolated from whatever else the
    #            Pi is doing. See esp32_servo_bridge/ for the firmware.
    transport: str = "direct"
    esp32_port: str = "/dev/ttyUSB0"
    esp32_baud: int = 115200


class ServoBus:
    """
    Represents the whole daisy-chained servo bus as a single object.
    Thread-safe: motion_engine.py drives this from a background thread
    while the Flask/Claude request-response cycle runs on the main thread.
    """

    def __init__(self, config: Optional[ServoBusConfig] = None):
        self.config = config or ServoBusConfig()
        self._lock = threading.Lock()
        self._positions = {i: 90 for i in range(self.config.servo_count)}  # assume mid-travel at boot
        self._conn = None

        if not self.config.simulate:
            if serial is None:
                raise RuntimeError(
                    "pyserial is not installed. Run: pip install pyserial --break-system-packages"
                )
            if self.config.transport == "esp32":
                self._conn = serial.Serial(
                    self.config.esp32_port,
                    self.config.esp32_baud,
                    timeout=0.2,
                )
            else:
                self._conn = serial.Serial(
                    self.config.port,
                    self.config.baudrate,
                    timeout=0.2,
                )

    # -- public API --------------------------------------------------------

    def set_angle(self, servo_id: int, angle: int) -> None:
        """Command a single servo to an absolute angle (0-180 degrees).
        Applies this servo's calibration offset before clamping/sending,
        so callers always think in "ideal" angles and never have to
        remember which joints are physically off-center."""
        offset = self.config.calibration_offsets.get(servo_id, 0)
        corrected = angle + offset
        corrected = max(self.config.min_angle, min(self.config.max_angle, corrected))
        with self._lock:
            self._positions[servo_id] = angle  # store the logical (uncorrected) angle
            packet = self._build_packet(servo_id, corrected)
            self._transmit(servo_id, corrected, packet)

    def set_angles(self, angles: dict[int, int]) -> None:
        """Command several servos at once. In "esp32" transport this is a
        single batched serial line (one round-trip instead of N); in
        "direct" transport it's still one Meccanoid-bus packet per servo,
        since that bus has no true broadcast write."""
        if self.config.transport == "esp32":
            corrected = {}
            with self._lock:
                for servo_id, angle in angles.items():
                    offset = self.config.calibration_offsets.get(servo_id, 0)
                    c = angle + offset
                    c = max(self.config.min_angle, min(self.config.max_angle, c))
                    corrected[servo_id] = c
                    self._positions[servo_id] = angle  # logical angle stored
                self._transmit_batch_esp32(corrected)
            return

        for servo_id, angle in angles.items():
            self.set_angle(servo_id, angle)

    def get_angle(self, servo_id: int) -> int:
        with self._lock:
            return self._positions.get(servo_id, 90)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()

    # -- internals -----------------------------------------------------------

    def _build_packet(self, servo_id: int, angle: int) -> bytes:
        """
        PACKET FORMAT — the part you must verify against your own capture.
        Placeholder scheme used here (NOT confirmed against real hardware):
            [0xFF]  start byte
            [id]    servo address, 0-indexed
            [angle] 0-180 mapped directly to a single byte
            [chksum] simple XOR checksum of the previous three bytes
        Swap this out for whatever your logic analyzer actually shows.
        """
        start = 0xFF
        payload = bytes([start, servo_id & 0xFF, angle & 0xFF])
        checksum = 0
        for b in payload:
            checksum ^= b
        return payload + bytes([checksum])

    def _transmit(self, servo_id: int, angle: int, packet: bytes) -> None:
        if self.config.simulate:
            print(f"[SIM/direct] servo {servo_id} -> {angle}°  (packet={packet.hex()})")
            return
        self._conn.write(packet)

    def _transmit_batch_esp32(self, corrected_angles: dict) -> None:
        """
        Command protocol Pi -> ESP32 (plain ASCII, newline-terminated):
            A:0=90,1=120,2=60,3=90\n
        The ESP32 firmware (esp32_servo_bridge/) parses this and builds the
        real Meccanoid bus packets itself, driving the wire timing from a
        FreeRTOS task pinned to its own core — decoupled from whatever the
        Pi's Python process is doing at that moment.
        """
        pairs = ",".join(f"{sid}={ang}" for sid, ang in corrected_angles.items())
        line = f"A:{pairs}\n"
        if self.config.simulate:
            print(f"[SIM/esp32] -> {line.strip()}")
            return
        self._conn.write(line.encode("ascii"))


# ---------------------------------------------------------------------------
# Quick manual test: `python servo_controller.py`
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    bus = ServoBus(ServoBusConfig(simulate=True, servo_count=4))
    for angle in (90, 120, 60, 90):
        bus.set_angles({i: angle for i in range(4)})
        time.sleep(0.3)
    print("Simulated sweep complete.")

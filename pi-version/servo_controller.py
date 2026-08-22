"""
servo_controller.py
--------------------
Low-level interface to the daisy-chained Meccanoid smart servos.

PROTOCOL — no longer a guess. Meccano never published an official spec,
but the "SM protocol" (Smart Module protocol) has been reverse-engineered
and published by the community, most usably in
alexfrederiksen/MeccanoidForArduino (Meccanoid.cpp/.h), which this module
ports faithfully:

  - Single half-duplex wire per chain, up to MAX_CHAIN=4 modules,
    module id = position order (closest to the controller = id 0).
  - Each bus cycle sends: HEADER_BYTE, then one output byte per possible
    module slot (always all 4, even for unused slots — an unused slot is
    just NOMOD_BYTE), then a checksum byte; then the bus switches to
    receive and reads back one byte from whichever module is next in the
    round-robin poll (module state/position feedback + connect/disconnect
    detection).
  - Byte framing on the wire: 1 start bit (417µs LOW), 8 data bits
    LSB-first (417µs each), 2 stop bits (417µs HIGH each) — i.e. roughly
    an 8N2 UART frame at ~2400 baud, but half-duplex on a single pin, so
    a normal two-wire TX/RX UART peripheral can't reproduce it directly.
  - Servo angle 0-180° maps linearly onto the byte range
    [SERVO_MIN, SERVO_MAX] = [0x18, 0xE8], not a raw 0-180 byte.
  - Checksum: sum the 4 output bytes, fold the carry (`sum += sum >> 8`),
    fold again (`sum += sum << 4`), keep only the high nibble
    (`sum &= 0xF0`), then OR in the current poll index's low nibble.

Still worth verifying against your own logic-analyzer capture before
trusting it on real hardware — Meccanoid revisions (G15KS vs Meccanoid
2.0 vs Personal Robot) reportedly agree on this protocol, but this
project has not independently confirmed it against real silicon.

What this module gives you regardless:
  - A clean `ServoBus` class your gesture/motion code can call without
    caring about wire-level details — set_angle/set_angles/get_angle are
    unchanged, so nothing upstream (gestures.py, motion_engine.py,
    brain.py, rig.py) needs to change for this protocol port.
  - A SIMULATION mode (default) that logs the real frame bytes and a
    simulated poll response instead of writing to a serial port, so you
    can develop and test the rest of the stack before any hardware is
    attached.
  - Real "direct" transport (Pi bit-banging the bus itself over pyserial)
    is intentionally NOT implemented — pyserial drives a normal two-wire
    UART peripheral, which cannot reproduce this protocol's half-duplex
    single-wire framing, and CPython can't hit 417µs timing reliably
    anyway (see stress_test_gpio_timing.py). Use transport="esp32"
    instead — esp32_servo_bridge.ino bit-bangs the real protocol on a
    FreeRTOS task, which can actually hold that timing.
"""

from __future__ import annotations
import time
import threading
from dataclasses import dataclass, field
from typing import Optional

try:
    import serial  # pyserial — only required for transport="esp32" in REAL mode
except ImportError:
    serial = None


# ---------------------------------------------------------------------------
# Real SM protocol constants — ported from alexfrederiksen/MeccanoidForArduino
# (Meccanoid.h). Credit: Alex Frederiksen.
# ---------------------------------------------------------------------------

MAX_CHAIN = 4          # a chain always has 4 module slots, whether used or not
BIT_DELAY_US = 417     # per-bit timing on the wire

TYPE_NONE = 0x00
TYPE_SERVO = 0x01
TYPE_LED = 0x02

HEADER_BYTE = 0xFF
NOMOD_BYTE = 0xFE      # also NEWMOD_BYTE — a slot with no module attached
ERASE_BYTE = 0xFD
REQUEST_BYTE = 0xFC
NIL_BYTE = 0xFB        # "nothing new to send this cycle" for a given output slot
LIM_BYTE = 0xFA

SERVO_MIN = 0x18       # 24  — byte value at logical angle 0
SERVO_MAX = 0xE8       # 232 — byte value at logical angle 180


def _angle_to_byte(angle: int) -> int:
    """Linear map matching ServoAdapter::setPosition: 0-180 -> SERVO_MIN-SERVO_MAX."""
    angle = max(0, min(180, angle))
    return round(SERVO_MIN + (angle / 180) * (SERVO_MAX - SERVO_MIN))


def _byte_to_angle(value: int) -> int:
    """Inverse of _angle_to_byte, matching ServoAdapter::getPosition's map()."""
    value = max(SERVO_MIN, min(SERVO_MAX, value))
    return round((value - SERVO_MIN) / (SERVO_MAX - SERVO_MIN) * 180)


def _checksum(outputs: list, poll_index: int) -> int:
    """Ported from Chain::calculateCheckSum — deliberately NOT masked to 8
    bits at every step (the original sums into a wider `int` and only the
    function's `byte` return type truncates at the end); only `& 0xF0`
    matters for correctness since that already collapses to a single byte."""
    s = sum(outputs)
    s = s + (s >> 8)
    s = s + (s << 4)
    s = s & 0xF0
    s = s | (poll_index & 0x0F)
    return s & 0xFF


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class ServoBusConfig:
    port: str = "/dev/serial0"      # unused — kept only so old configs don't break on load
    baudrate: int = 9600            # unused — see transport docs below
    servo_count: int = 4            # how many smart servos are daisy-chained
    simulate: bool = True           # False only once wired to real hardware
    min_angle: int = 0
    max_angle: int = 180
    # Per-servo trim, e.g. {2: -3} means "when asked for 90, actually send 87"
    # because that servo's true mechanical center is 3° off. Fill these in
    # after physically checking each joint's rest position.
    calibration_offsets: dict = field(default_factory=dict)

    # --- transport selection -------------------------------------------
    # "direct" — simulate=True only: logs the real chain frame bytes so you
    #            can develop/test against the real protocol without
    #            hardware. simulate=False raises NotImplementedError — see
    #            the module docstring for why the Pi can't safely bit-bang
    #            this bus itself over pyserial.
    # "esp32"  — Pi sends a plain-text target-angle command over USB
    #            serial to an ESP32, which builds the real Meccanoid bus
    #            packets and drives the servo timing itself on a
    #            dedicated FreeRTOS core, isolated from whatever else the
    #            Pi is doing. This is the only supported real-hardware
    #            path. See esp32_servo_bridge/ for the firmware.
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

        # Real Chain::outputs[] — one byte per possible module slot, always
        # MAX_CHAIN long regardless of how many servos are actually wired up.
        # Slots within servo_count start at the angle-90 byte (matches
        # _positions' mid-travel assumption); slots beyond it stay
        # NOMOD_BYTE forever, since nothing is ever addressed there.
        self._outputs = [
            _angle_to_byte(90) if i < self.config.servo_count else NOMOD_BYTE
            for i in range(MAX_CHAIN)
        ]
        self._poll_index = 0  # rotates 0..MAX_CHAIN-1, matches Chain::moduleNum

        if not self.config.simulate:
            if self.config.transport == "esp32":
                if serial is None:
                    raise RuntimeError(
                        "pyserial is not installed. Run: pip install pyserial --break-system-packages"
                    )
                self._conn = serial.Serial(
                    self.config.esp32_port,
                    self.config.esp32_baud,
                    timeout=0.2,
                )
            else:
                # See module docstring: a normal two-wire pyserial UART
                # cannot reproduce this protocol's half-duplex single-wire
                # framing, and CPython can't hold 417us timing reliably
                # even if it could (stress_test_gpio_timing.py). Failing
                # loudly here is safer than silently writing the wrong
                # bytes at real servos.
                raise NotImplementedError(
                    "transport='direct' with simulate=False is not implemented — "
                    "pyserial can't reproduce the Meccanoid bus's half-duplex "
                    "single-wire framing. Use transport='esp32' for real hardware; "
                    "see esp32_servo_bridge/esp32_servo_bridge.ino."
                )

    # -- public API --------------------------------------------------------

    def set_angle(self, servo_id: int, angle: int) -> None:
        """Command a single servo to an absolute angle (0-180 degrees).
        Applies this servo's calibration offset before clamping/sending,
        so callers always think in "ideal" angles and never have to
        remember which joints are physically off-center.

        Real-protocol note: the bus has no addressed single-servo write —
        every cycle sends all MAX_CHAIN output slots (see module
        docstring), so this updates this servo's slot and then transmits
        the whole chain frame, exactly like Chain::update() does."""
        offset = self.config.calibration_offsets.get(servo_id, 0)
        corrected = angle + offset
        corrected = max(self.config.min_angle, min(self.config.max_angle, corrected))
        with self._lock:
            self._positions[servo_id] = angle  # store the logical (uncorrected) angle
            self._outputs[servo_id] = _angle_to_byte(corrected)
            frame = self._build_frame()
            self._transmit(servo_id, corrected, frame)

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

    def _build_frame(self) -> bytes:
        """
        Real SM-protocol chain frame, ported from Chain::update()/
        calculateCheckSum() (see module docstring for the source):
            [HEADER_BYTE] [out0] [out1] [out2] [out3] [checksum]
        outputs[] always has all MAX_CHAIN slots, even ones with nothing
        wired up (those stay NOMOD_BYTE and are still transmitted every
        cycle — the real bus has no way to address "only servo 2").
        """
        checksum = _checksum(self._outputs, self._poll_index)
        return bytes([HEADER_BYTE, *self._outputs, checksum])

    def _simulate_poll_response(self) -> int:
        """Stand-in for Chain::receiveByte()'s half-duplex read-back. Real
        hardware would echo the addressed module's actual reported state;
        in simulation we loop back the last commanded byte for slots that
        represent an attached servo, and TYPE_NONE for genuinely unused
        slots — good enough to exercise the full request/response cycle
        without a real bus."""
        if self._poll_index < self.config.servo_count:
            return self._outputs[self._poll_index]
        return TYPE_NONE

    def _transmit(self, servo_id: int, angle: int, frame: bytes) -> None:
        if self.config.simulate:
            response = self._simulate_poll_response()
            print(
                f"[SIM/direct] servo {servo_id} -> {angle}°  "
                f"frame={frame.hex()}  poll(slot={self._poll_index})<-0x{response:02X}"
            )
            self._poll_index = (self._poll_index + 1) % MAX_CHAIN
            return
        # Unreachable — real transport='direct' is rejected in __init__.
        raise NotImplementedError("transport='direct' real hardware I/O is not implemented")

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

# MecchaLLMBotAA

An LLM-driven brain for the **Meccanoid Personal Robot 2.0** (2ft, 4 smart
servos — arms only, no head/neck motor, 2 DC wheel motors). Two
independent architectures are built and tested here, both fully verified
in simulation — no real Meccanoid hardware has been driven yet. The servo
bus protocol is ported from a community-reverse-engineered source (see
Credits below) rather than a guess, but it's still unconfirmed against
real Meccanoid silicon — see `pi-version/servo_controller.py`'s docstring.

## Required hardware

Common to both versions:
- Meccanoid Personal Robot 2.0 (the specific 2ft/4-servo model — the XL
  2.0 has different hardware, see `docs/project-summary.md`)
- 3.3V↔5V bidirectional logic level shifter
- 5V/3A portable power bank for the servo bus, kept electrically separate
  from the Pi/ESP32's own supply (shared ground only) — servo stall
  current can brown out a shared rail
- Breadboard/jumpers, 4.7kΩ pull-up resistors (placement depends on your
  level shifter board — some already include them)
- A USB logic analyzer is **not required** to get started — the ported
  protocol (see Credits) removes the need to capture it yourself, though
  independently verifying it before trusting real hardware is still wise

| | `pi-version/` | `esp32-cloud-version/` |
|---|---|---|
| Compute | Raspberry Pi 5 (4GB+; 8GB+ recommended if you add voice/vision later) + microSD | ESP32 dev board only |
| Servo timing | Optional 2nd board: ESP32 dev board (recommended — see `pi-version/README.md`'s "Split architecture") | Built into the same ESP32 |
| Extra for voice/vision (optional, not built) | USB mic + speaker, Pi Camera | Not practical on ESP32 alone |

Full pricing notes and a BOM calculator: `docs/bom-calculator.html`,
`docs/project-summary.md`.

## Credits / where the code came from

This project's own code is original; a few pieces are ported from or
based on published community work, credited here per their use:

- **Servo bus protocol** — `pi-version/servo_controller.py` (and its
  esp32-cloud-version duplicate) and both `.ino` firmware files port the
  frame format, checksum, and bit timing from
  [alexfrederiksen/MeccanoidForArduino](https://github.com/alexfrederiksen/MeccanoidForArduino)
  (`Meccanoid.cpp`/`.h`), an MIT-style community reverse-engineering of
  Meccano's unpublished "SM protocol." Not Meccano's own published spec —
  Meccano never released one.
- **Reference architecture** —
  [StormingMoose/ESP-Rider_Meccanoid_Two_Motor_L298N_Version](https://github.com/StormingMoose/ESP-Rider_Meccanoid_Two_Motor_L298N_Version)
  (ESP32 + L298N motor driver + Meccanoid servo) was used as a real-world
  sanity check that this project's ESP32+L298N+Meccanoid-servo stack
  choice is a known-working combination, not code copied directly.
- **Hardware facts** (servo/motor counts, chain wiring, model dimensions)
  come from Meccano's own product listings for the
  [Meccanoid Personal Robot 2.0](https://www.meccano.com/product/meccanoid-personal-robot-2-0-2/).

## Which version do I want?

| | [`pi-version/`](pi-version/) | [`esp32-cloud-version/`](esp32-cloud-version/) |
|---|---|---|
| Extra hardware | Raspberry Pi (+ optional ESP32 for servo timing) | ESP32 only |
| LLM | Ollama (free, local) or Claude (paid) | Gemini 2.5 (default) or Claude |
| Memory | Local `.txt` file | In-process (dev) or Firestore (production) |
| Runs offline | Yes, with Ollama | No — needs the cloud function reachable |
| Best for | Lower running cost, more extensible (voice/vision later) | Simplest wiring, lowest hardware cost |

Both share the same gesture set, keyword-trigger logic, and non-blocking
"move while thinking" design — see `docs/project-summary.md` for the full
build history and design rationale.

## Repo layout

```
pi-version/            Flask brain + servo/motion engine, runs on a Pi
esp32-cloud-version/    Cloud function brain + ESP32 firmware, no Pi
standalone-tools/       Shared eye-display and echo-cancellation demos
docs/                   Dashboard (docs/dashboard.html), BOM calculator, pinouts
test_everything.py      Fast, dependency-free logic smoke test
test_real_modules.py    Regression suite against the actual production modules
CHANGELOG.md            What changed and why, in order
```

## Quick start

```bash
# fast sanity check, no dependencies needed
python3 test_everything.py

# regression test against the real modules (installs requests/flask as needed)
python3 test_real_modules.py
```

Then follow whichever architecture's own README matches your hardware:
[`pi-version/README.md`](pi-version/README.md) or
[`esp32-cloud-version/README.md`](esp32-cloud-version/README.md).

## Dashboard

`docs/dashboard.html` is a static, no-build-step control console — open it
directly, or host it on Firebase Hosting (see
`esp32-cloud-version/README.md`). Point its "Link configuration" panel at
your deployed `/chat` endpoint; until an endpoint is set it runs in demo
mode with simulated replies.

## Status

All logic is tested in simulation (`simulate=True` mocked I/O — see each
README's testing section, plus `test_real_modules.py`'s protocol-level
checks). The servo bus protocol is now sourced (see Credits) rather than
guessed, but independently verifying it against real Meccanoid hardware
is still the one remaining step before driving an actual robot — only
`transport="esp32"` is wired up for that (see `pi-version/README.md`).
See `CHANGELOG.md` for what's landed so far.

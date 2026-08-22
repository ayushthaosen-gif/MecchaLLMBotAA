# MecchaLLMBotAA

An LLM-driven brain for the **Meccanoid Personal Robot 2.0** (2ft, 4 smart
servos — arms only, no head/neck motor, 2 DC wheel motors). Two
independent architectures are built and tested here, both fully verified
in simulation — no real Meccanoid hardware has been driven yet, since the
servo bus protocol is still a documented placeholder pending a
logic-analyzer capture (see `pi-version/README.md`).

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
README's testing section). Real-hardware verification of the servo bus
protocol is the one remaining blocker before driving an actual Meccanoid.
See `CHANGELOG.md` for what's landed so far.

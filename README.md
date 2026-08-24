# MecchaLLMBotAA

An LLM-driven brain for the **Meccanoid Personal Robot 2.0** — a stock toy
robot with ~150 canned phrases and no memory, turned into an open-ended
conversational companion: persistent memory across power cycles, a real
LLM instead of scripted lines, a web dashboard reachable from anywhere,
and gesture/locomotion triggered by conversation instead of only physical
"Learned Motion" posing.

Two complete, independently runnable architectures are built here, both
fully verified **in simulation** — no real Meccanoid hardware has been
driven yet. See [Status](#status) for exactly what that does and doesn't
mean.

## Table of contents

- [The physical robot — confirmed hardware](#the-physical-robot--confirmed-hardware)
- [Required hardware](#required-hardware)
- [Which version do I want?](#which-version-do-i-want)
- [How it works](#how-it-works)
- [Repo layout](#repo-layout)
- [Quick start](#quick-start)
- [Dashboards](#dashboards)
- [Testing](#testing)
- [Status](#status)
- [Credits / where the code came from](#credits--where-the-code-came-from)
- [Further reading](#further-reading)

## The physical robot — confirmed hardware

This is the smaller **Meccanoid Personal Robot 2.0** (2ft), not the XL
2.0 (4ft) — an earlier version of this project targeted the XL by
mistake before the exact retail model was confirmed; see
`docs/project-summary.md` for that correction. Confirmed, not assumed:

- **4 smart servos total, arms only**: `right_shoulder`, `right_elbow`,
  `left_shoulder`, `left_elbow` (2 per arm)
- **No head/neck servo** — the head does not move under motor control at
  all; gestures implying head motion (nod, look-around) are not
  physically possible on this robot and were removed from the gesture set
- **2 simple DC gearbox motors** driving the wheeled feet (locomotion) —
  not smart servos, no position feedback, a completely separate hardware
  system from the arm servos
- Smart servos are daisy-chained, **2 independent chains** (one per arm),
  max 4 modules per chain, module id = position order (closest to the
  controller = id 0) — see `pi-version/rig.py`

## Required hardware

Common to both versions:
- Meccanoid Personal Robot 2.0 (the specific 2ft/4-servo model above)
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

Full pricing notes and a BOM calculator (robu.in / India parts pricing):
`docs/bom-calculator.html`, `docs/project-summary.md`.

## Which version do I want?

| | [`pi-version/`](pi-version/) | [`esp32-cloud-version/`](esp32-cloud-version/) |
|---|---|---|
| Extra hardware | Raspberry Pi (+ optional ESP32 for servo timing) | ESP32 only |
| LLM | Ollama (free, local) or Claude (paid) | Gemini 2.5 (default) or Claude |
| Memory | Local `.txt` file | In-process (dev) or Firestore (production) |
| Runs offline | Yes, with Ollama | No — needs the cloud function reachable |
| API key exposure | None needed (Ollama), or lives on the Pi (Claude) | Never touches the ESP32 — stays server-side in the cloud function |
| Best for | Lower running cost, more extensible (voice/vision later) | Simplest wiring, lowest hardware cost, no Pi to maintain |

Both share the same gesture set, keyword-trigger logic, and non-blocking
"move while thinking" design — see `docs/project-summary.md` for the full
build history and design rationale, including the false starts (XL vs
Personal 2.0, 8 vs 4 servos, single-chain vs two-chain) that got
corrected along the way.

## How it works

**`pi-version/`** — a Flask server (`brain.py`) on the Pi ties everything
together: it calls the LLM (Ollama or Claude), appends every exchange to
a local memory file, intercepts weather questions with a real API lookup,
and fires a gesture via a background motion thread — all before the slow
LLM call returns, so the robot can move while it's still "thinking."
Servo commands either go straight out the Pi's own bus (`transport=
"direct"`, simulation-only — see Status) or over USB to an ESP32 that
drives the real-time timing (`transport="esp32"`, the supported
real-hardware path).

```
Dashboard ──POST /chat──► Flask (brain.py) ──► LLM (Ollama/Claude)
                              │
                    memory file (.txt, append-only)
                              │
                     gesture/motion engine (non-blocking)
                              │
                  ServoBus ──"direct" (sim only) or──► ESP32 ──► Meccanoid servo bus
```

**`esp32-cloud-version/`** — no Pi at all. The ESP32 connects to WiFi
directly and *polls* a cloud function for commands (pull, not push — the
ESP32 sits behind home WiFi/NAT with no public IP, so nothing can reach
it directly). The cloud function (`cloud_function/main.py`,
`RobotBrainService`) is the actual brain: calls Gemini 2.5 or Claude,
keeps conversation memory (in-process for local testing, Firestore for
production), does the weather intercept, and decides gestures/locomotion.
Two separate polling queues (motion, reply) fixed a real bug where
gestures used to wait for the LLM call to finish before playing.

```
Dashboard ──POST /chat──► Cloud Function ──► Gemini 2.5 / Claude
                                │
                     motion_queue / reply_queue
                                │
                 ESP32 polls /next_motion every ~1.5s
                                │
                         Meccanoid servo bus
```

## Repo layout

```
pi-version/             Flask brain + servo/motion engine, runs on a Pi
  brain.py                Flask server: LLM calls, memory, weather intercept, gestures
  llm_backend.py           Pluggable LLM: Ollama (free/local) or Claude
  servo_controller.py      Real SM-protocol servo bus (simulated by default)
  motion_engine.py         Non-blocking gesture playback thread
  gestures.py               Flat 4-servo compatibility gesture/keyword model
  rig.py / rig_gestures.py  Confirmed 2-chain hardware model + fuller gesture set
  locomotion.py             Differential-drive wheel motor kinematics
  esp32_servo_bridge/       ESP32 firmware: bit-bangs the real protocol
  test_*.py, demo_*.py, stress_test_gpio_timing.py   Simulation test/demo scripts

esp32-cloud-version/    Cloud function brain + ESP32 firmware, no Pi
  esp32_cloud_brain.ino     ESP32 firmware: WiFi + polling + real servo protocol
  cloud_function/
    main.py                  RobotBrainService: the actual brain logic
    cloud_llm_backends.py     Pluggable LLM: Gemini 2.5 (default) or Claude
    firestore_memory.py       Production persistent-memory backend
    eyes.py                   Optional status/mood eye display
  gestures.py, servo_controller.py, motion_engine.py, locomotion.py
    Duplicates of the pi-version modules, used as a tested stand-in for
    the ESP32 firmware's logic in simulate_full_run.py
  simulate_full_run.py, full_system_test.py, test_*.py, demo_*.py

standalone-tools/       Shared eye-display and echo-cancellation demos
docs/                   docs/index.html (static control console), BOM
                        calculator, pinouts, project-summary.md (full build history)
app/, worker/, .openai/ A separate deployable public dashboard (Next.js/
                        React on Cloudflare Workers via OpenAI Sites
                        tooling) — an alternate frontend to the same
                        `/chat` endpoint as docs/index.html

test_everything.py      Fast, dependency-free logic smoke test
test_real_modules.py    Regression suite against the actual production
                        modules (including the real servo protocol's
                        checksum/frame logic)
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
[`esp32-cloud-version/README.md`](esp32-cloud-version/README.md). Each
covers backend setup (Ollama/Claude/Gemini env vars), wiring, and how to
run everything in simulation before touching real hardware.

## Dashboards

Two separate frontends exist, both talking to the same `/chat`-shaped
backend — pick whichever fits how you want to host it:

- **`docs/index.html`** — a single static file, no build step, no
  dependencies. Open it directly, or host it anywhere that serves static
  files (Firebase Hosting, GitHub Pages, etc. — see
  `esp32-cloud-version/README.md`). Link settings persist in
  `localStorage`. Full feature parity with `app/`: all 19 gesture/meme
  buttons, and its own vanilla-JS port of the camera pose mirror
  (on-device tracking, live HUD, joint-angle chips) that talks to the
  same `/mirror_pose` endpoint — no React/Next.js needed to get the
  camera feature.

  **GitHub Pages setup** (no local server involved): repo → Settings →
  Pages → Source: "Deploy from a branch" → Branch: `main`, folder:
  `/docs` → Save. GitHub Pages serves whatever's in `docs/` at
  `https://<your-username>.github.io/<repo-name>/`, and specifically
  looks for `index.html` as that URL's default page — which is exactly
  why the dashboard lives at `docs/index.html` and not
  `docs/dashboard.html`. No build step, no server to run or keep
  alive — GitHub hosts the static file directly. After enabling it,
  changes land live within a minute or two of pushing to `main`.
- **`app/`** — a Next.js/React app built with `vinext`/Vite, deployed to
  Cloudflare Workers via `worker/index.ts` and OpenAI's Sites tooling
  (`.openai/hosting.json`). Same functionality (gesture buttons, chat,
  link configuration), different hosting story — run `npm install &&
  npm run dev` to develop it locally, `npm run build` to build it.

Both run in **demo mode** with simulated replies until you point their
"Link configuration" panel at a real deployed `/chat` endpoint — neither
ever calls Gemini/Claude directly from the browser, which is the whole
point of the server-side split (see How it works above).

## Testing

Everything is tested in simulation — no real hardware required to run
any of these:

- `test_everything.py` — fast, zero-dependency smoke test of gesture
  matching, servo interpolation, and locomotion kinematics
- `test_real_modules.py` — imports and exercises the actual production
  modules (not a reimplementation): gesture matchers, `RobotBrainService`
  including the non-blocking queue pattern, Firestore memory backend
  selection, and the real servo protocol's checksum/frame math
- `pi-version/test_integration.py` — full Flask request/response cycle:
  memory, gesture, weather intercept
- `pi-version/demo_complex_movements.py` — all 7 gestures verified
  joint-by-joint
- `pi-version/stress_test_gpio_timing.py` — demonstrates *why* the
  ESP32 servo-timing offload exists (measured Python/Linux jitter under
  CPU load)
- `esp32-cloud-version/simulate_full_run.py` — full round trip: dashboard
  → cloud function → command queue → simulated ESP32 poll loop
- `esp32-cloud-version/full_system_test.py` — every feature in one run:
  gestures, locomotion, non-blocking commands, weather, hybrid filler
  timing, LLM backend swap

Full list and what each one proves: `docs/project-summary.md`.

## Status

All logic is tested in simulation (`simulate=True` mocked I/O throughout
— see each README's testing section, plus `test_real_modules.py`'s
protocol-level checks). What that means concretely:

- The servo bus protocol is now **sourced** from a published
  community reverse-engineering effort (see Credits), not guessed — but
  it hasn't been independently confirmed against real Meccanoid silicon
  by this project. Verifying it with your own logic-analyzer capture
  before fully trusting it on real hardware is still the responsible move.
- `transport="direct"` (Pi driving the servo bus itself) is **not** a
  supported real-hardware path — it raises `NotImplementedError` on
  purpose, because a normal two-wire UART can't reproduce the real
  protocol's half-duplex single-wire framing. `transport="esp32"` is the
  only wired-up real-hardware path.
- The dead-reckoning locomotion numbers (wheelbase, max speed) are
  estimates, not measured against a real robot.
- Voice I/O, camera/vision, and an always-on autonomy loop were discussed
  in the original design conversation but not built.
- The ESP32 bridge firmware currently drives the flat single-chain
  compatibility model (matches what `brain.py` actually uses), not the
  confirmed real 2-independent-chain topology in `rig.py` — extending it
  to two chains is an open item, see `docs/project-summary.md`'s
  "Suggested simulation tests to run next."

See `CHANGELOG.md` for the full history of what's landed, in order.

## Credits / where the code came from

This project's own code is original; a few pieces are ported from or
based on published community work, credited here per their use:

- **Servo bus protocol** — `pi-version/servo_controller.py` (and its
  esp32-cloud-version duplicate) and both `.ino` firmware files port the
  frame format, checksum, and bit timing from
  [alexfrederiksen/MeccanoidForArduino](https://github.com/alexfrederiksen/MeccanoidForArduino)
  (`Meccanoid.cpp`/`.h`), a community reverse-engineering of Meccano's
  unpublished "SM protocol." Not Meccano's own published spec — Meccano
  never released one publicly (though `docs/project-summary.md` notes a
  `Smart_Module_Protocols_2015.pdf` reference was also identified as a
  source worth cross-checking against).
- **Reference architecture** —
  [StormingMoose/ESP-Rider_Meccanoid_Two_Motor_L298N_Version](https://github.com/StormingMoose/ESP-Rider_Meccanoid_Two_Motor_L298N_Version)
  (ESP32 + L298N motor driver + Meccanoid servo) was used as a real-world
  sanity check that this project's ESP32+L298N+Meccanoid-servo stack
  choice is a known-working combination, not code copied directly.
- **Hardware facts** (servo/motor counts, chain wiring, model dimensions)
  come from Meccano's own product listings for the
  [Meccanoid Personal Robot 2.0](https://www.meccano.com/product/meccanoid-personal-robot-2-0-2/).

## Further reading

- `docs/project-summary.md` — the full build history, every hardware
  correction made along the way (and why), and the testing log
- `CHANGELOG.md` — what changed, when, and why, newest first
- `pi-version/README.md`, `esp32-cloud-version/README.md` — setup,
  wiring, and backend configuration for each architecture
- `pi-version/servo_controller.py`'s module docstring — the full
  byte-level/timing writeup of the real servo protocol

## Camera pose mirror

The dashboard's `/mirror` page (both `docs/index.html`'s vanilla-JS port and
`app/mirror/page.tsx`) uses a phone or laptop front camera, entirely on-device
(nothing but 4 joint angles + an expression tag + an optional follow direction
ever leaves the browser), to:

- **Draw a MediaPipe pose wireframe** and send 4 arm-joint angles to the Pi
  — drawing only requires *a* pose to be detected at all; sending only
  requires shoulders/elbows/wrists specifically confident (no hips needed —
  see `torsoReference()` in either mirror script for why hip visibility
  isn't actually required for the angle math either).
- **Track the face and expression** with a second MediaPipe model
  (FaceLandmarker): face contours and both irises are drawn locally,
  while all 52 blendshape coefficients are temporally smoothed and scored
  from multiple facial signals. Three-frame confirmation, switch
  hysteresis, and a 450ms face-loss grace period prevent eye-color
  flicker. The stable result is one of 7 moods (happy/sad/angry/
  surprised/fear/disgust/neutral), sent to the robot's eye LEDs.
- **Detect crossed wrists** as a "follow mode" toggle: crossing your wrists
  *locks* your current apparent distance (shoulder width in frame — see
  "How distance is tracked" below) as the target. From then on, if you
  step back the robot drives forward in short bounded pulses to close the
  gap, stopping once it's caught back up to the locked distance —
  deliberately one-directional for now, no backward/retreat if you step
  closer than the locked distance. Cross again to release.
- Cap the actual camera-loop framerate (detection + redraw) at 2x the
  robot's own accepted update rate (40Hz vs. the backend's 20Hz limit) —
  going faster wastes battery/CPU on frames neither the eye nor the robot
  can use.
- Show a real-time terminal-style log of what's actually happening (model
  loads, camera access, tracking lock, uplink connect/ack, expression/
  follow-mode transitions) — not decorative, every line is a real event.

On the Pi, configure `ENABLE_MIRROR_CONTROL=1`, a long random
`DASHBOARD_TOKEN`, and `MIRROR_ALLOWED_ORIGIN` set to the dashboard's HTTPS
origin. Enter the Pi's HTTPS `/mirror_pose` URL and the same token on the
mirror page.

`MirrorController` (`pi-version/mirror_control.py`) applies three
independently rate-limited fields from the same feed, each with safety
matched to how safety-critical it actually is:
- **Arm angles** (`joints`, required): 20Hz max, clamped to 15-165°, each
  update limited to 12° of movement, non-finite values rejected, returns
  to rest if updates stop for 750ms.
- **Mood** (`mood`, optional): 0.3s min interval — cosmetic, so a skipped
  update is never an error, just a no-op.
- **Locomotion** (`locomotion`, optional — strictly `forward`/`stop` in
  follow mode; backward commands are rejected by the controller):
  0.5s min interval, each command a short self-bounding wheel pulse (never
  a continuous motor command) at a gentler speed than scripted routines
  use, since this is live teleop, not a scripted gesture.

Test with simulated servos/motors before enabling real hardware.

### How distance is tracked (follow mode)

There's no depth sensor or real-world distance measurement involved — it's
a monocular-camera proxy based on simple perspective: **shoulder width in
pixels**, `|right_shoulder.x - left_shoulder.x| × canvas width`. Closer to
the camera = shoulders span more pixels; farther away = fewer pixels. That
single number, captured at the instant you cross your wrists, becomes
`followBaselineWidth`.

On every subsequent processed frame, the same measurement is taken again
and compared as a ratio (`currentWidth / followBaselineWidth`). Ratio
below `1 - 8%` means you've stepped back (shoulders look narrower than
when locked) → drive forward; within that ±8% dead-band → stop, you're
back at the locked distance. The 8% band exists so ordinary sway/breathing
doesn't cause the wheels to twitch right at the target.

Known limitations, stated plainly: this is a *relative* proxy, not a
measurement in meters — it has no absolute calibration and would need one
(e.g. a known focal length + real shoulder width) to report actual
distance. It also assumes you stay roughly facing the camera; turning
sideways foreshortens the shoulder span and would read as "farther away"
even at the same real distance. Same category of honest caveat as
`locomotion.py`'s dead-reckoning pose tracking elsewhere in this project —
useful for the behavior it drives, not for precision.

This mirrors shoulders and elbows, not fingers: the robot has no finger
motors. Live pose streaming is currently Pi-only because the cloud ESP32
polling loop and single-chain firmware are not suitable for safe real-time
control. Eye cues are synchronized in the cloud logic, but physical eye
output still requires the separate eye-module transport and the documented
two-arm-chain firmware work.
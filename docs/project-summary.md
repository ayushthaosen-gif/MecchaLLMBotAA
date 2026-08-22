# Meccanoid Cloud-AI Robot — Project Summary (for simulation testing)

## The physical robot
**Meccanoid Personal Robot 2.0** (2ft tall, non-XL variant). Confirmed hardware, NOT assumed:
- **4 smart servos total, arms only**: right_shoulder, right_elbow, left_shoulder, left_elbow (2 per arm)
- **NO head/neck servo** — the head does not move under motor control at all
- **2 simple DC gearbox motors** driving wheeled feet (locomotion) — NOT smart servos, no position feedback, separate hardware system entirely from the arm servos
- Smart servos are daisy-chained: max 4 modules per chain, module id = position order (closest to controller pin = id 0)

## Servo protocol — already solved, no reverse-engineering needed
- Official spec: Meccano's own `Smart_Module_Protocols_2015.pdf` (published by Meccano)
- Working library: `alexfrederiksen/MeccanoidForArduino` (C++, Arduino/ESP32-compatible)
- Exact reference match for this build: `StormingMoose/ESP-Rider_Meccanoid_Two_Motor_L298N_Version` — ESP32 + L298N motor driver + Meccanoid servo, same stack as this project
- Real protocol facts: single PWM-style pin per chain (not standard UART), not a byte-packet/checksum scheme as initially guessed

## Two architectures built (both simulated and tested, no real hardware used)

### A) Raspberry Pi version
- `brain.py` (Flask) ties together: LLM calls, persistent memory (`.txt` file, append-only), weather tool-use intercept, gesture triggering
- `llm_backend.py`: pluggable — **Ollama (free, local, default)** or **Claude API (paid)**, selected via `LLM_BACKEND` env var
- `servo_controller.py` / `motion_engine.py`: servo bus abstraction with smoothing/easing (`_ease_in_out`), per-servo calibration offsets, speed-capped interpolation
- `rig.py` / `rig_gestures.py`: the corrected, real 4-servo/2-chain model (right_arm, left_arm — no head chain)
- Supports both "direct" transport (Pi drives servos itself) and "esp32" transport (Pi sends commands to an ESP32 over serial, which does the real-time driving — added because Python/Linux threading showed measurable timing jitter under CPU load in `stress_test_gpio_timing.py`)

### B) ESP32-only + Cloud LLM version (no Pi at all)
- ESP32 connects to WiFi directly, polls a cloud function for commands (pull model — ESP32 has no public IP, so it can't receive pushed requests)
- `cloud_function/main.py` (`RobotBrainService`): the actual brain — calls the LLM, keeps memory, weather intercept, gesture + locomotion keyword matching
- `cloud_llm_backends.py`: pluggable — **Gemini 2.5** or **Claude**, same brain logic either way
- **Two separate polling queues** (`motion_queue`, `reply_queue`) — this was a real bug fix: gestures used to only get queued *after* the slow LLM call finished, defeating "move while thinking." Fixed by enqueueing gesture/locomotion matches immediately, before the LLM call, so movement never waits on the network
- `locomotion.py`: the 2 wheel motors, differential drive, 3 turn types (rotate-in-place, arc turn, pivot turn), with dead-reckoning pose tracking (x, y, heading) for visualizing what a command does — explicitly *not* accurate real-world odometry, just a kinematics sanity check
- `local_filler_model.py`: stand-in for a real tiny on-device LLM (e.g. the demonstrated 28.9M-param TinyStories model that runs on an ESP32-S3 at ~9 tok/s) — generates instant, non-contextual filler text while the real cloud reply is in flight, to mask network latency. Honest limitation: it can't answer anything, it's not a smaller Claude/Gemini

## Current gesture set (arms-only, matches confirmed hardware)
Core: `wave_right`, `wave_both`, `bow`, `shrug`, `point`, `full_dance`, `sit`
Dance/meme: `dab`, `flex`, `floss`, `the_robot`, `mic_drop`, `finger_guns`
Gesture chains (multiple gestures played as one continuous routine):
`greeting_routine` (bow+wave_both), `showoff_routine` (wave_right+full_dance+bow),
`goodnight_routine` (wave_right+sit), `meme_routine` (dab+flex+finger_guns)

All keyword-triggered, longest-match-wins specificity (fixed a bug where "wave" was shadowing "wave with both") on word-boundary matches (fixed a separate bug where e.g. "bow" fired inside "rainbow"/"elbow")

## Movement refinement
`motion_engine.py`/`rig_motion_engine.py` now have a small dead-band
(`DEAD_BAND_DEG = 1.5`): a keyframe move smaller than that snaps directly
to the target in one command instead of spending several interpolation
steps easing a correction nobody could see — this was flagged as a "not
yet built" refinement in the original design conversation ("small
dead-band so it doesn't twitch when a target is 1-2° off due to command
noise") and is now implemented.

Locomotion triggers: `forward`, `backward`, `turn_left`, `turn_right`, `turn_around`

## Testing done (all passed, all in simulation — no real ESP32/Pi/cloud used)
- `test_integration.py` (Pi version) — Flask + memory + gesture + weather, full request/response cycle
- `demo_complex_movements.py` — all 7 gestures individually verified, joint-by-joint
- `stress_test_gpio_timing.py` — proved Python/Linux servo timing jitter under CPU load (this is *why* the ESP32 transport option exists)
- `simulate_full_run.py` (ESP32-cloud version) — dashboard → cloud function → command queue → simulated ESP32 poll loop, full round trip
- `test_nonblocking_motion.py` — proved gesture visible in queue ~1s before a simulated 1s-latency LLM call returns
- `full_system_test.py` — every feature in one run: gestures, locomotion, combined non-blocking commands, weather, hybrid filler timing, backend swap
- Locomotion turn-type test — confirmed rotate/arc/pivot produce kinematically distinct displacement/heading signatures

## What's NOT yet tested / open items
- No real hardware has touched any of this — everything is `simulate=True` mocked I/O
- The dead-reckoning locomotion numbers (wheelbase, max speed) are estimates, not measured
- Voice I/O, camera/vision, and autonomy loop were discussed but not built
- BOM/cost calculator built for robu.in (India) parts — logic analyzer originally listed as required is now understood to be unnecessary given the official protocol docs + existing libraries above

## Suggested simulation tests to run next
1. ~~Port `alexfrederiksen/MeccanoidForArduino`'s actual protocol logic into the Python simulation layer (replace the old guessed byte-packet code)~~ — done, see CHANGELOG.md. Ported into `servo_controller.py` (both copies) and both `.ino` firmware files: real HEADER+4-slot+checksum frame, 417µs half-duplex bit timing, SERVO_MIN/MAX angle mapping. `transport="direct"` real hardware I/O is intentionally left unimplemented (raises clearly) since a two-wire UART can't reproduce the real single-wire half-duplex bus — `transport="esp32"` is the only supported real-hardware path now.
2. End-to-end test combining the Pi version's Ollama backend with the ESP32-cloud version's non-blocking queue pattern
3. Load test: many rapid dashboard messages in sequence, confirm queue ordering and no dropped gestures
4. Fault injection: simulated cloud/WiFi timeout, confirm local filler + graceful degradation
5. New: extend the ESP32 bridge firmware to drive rig.py's real 2-independent-chain topology (right_arm/left_arm), not just the flat single-chain compatibility model

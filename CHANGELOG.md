# Changelog

All notable changes to this project, newest first. Commit hashes refer to
this repo's `main` branch.

## [Unreleased]

### Improved (stable facial tracking)
- Upgraded both camera-mirror implementations to combine multiple
  MediaPipe blendshapes per expression instead of relying on one dominant
  feature. Fear is now distinguished from surprise using mouth stretch,
  frown, eye-wide, brow, and jaw signals.
- Added exponential blendshape smoothing, three-frame confirmation,
  expression-switch hysteresis, and a 450ms face-loss grace period so the
  robot eye color does not flicker on noisy frames.
- Added live face-contour and iris overlays plus a face confidence readout.
  Explicit 0.6 detection/presence/tracking thresholds reject weak face
  locks, while single-face mode retains MediaPipe's internal smoothing.
- Face inference now runs at 20Hz and is cached between the 40Hz pose
  frames, reducing synchronous main-thread work without slowing arm
  tracking.

### Fixed (follow mode was bidirectional, should be forward-only for now)
- Follow mode's distance decision drove the wheels both directions
  (forward when farther, backward when closer) around the baseline
  width. Corrected to the intended behavior: crossing your wrists LOCKS
  the distance at that instant; only stepping back (narrower shoulder
  width) triggers a forward pulse to close the gap, stopping once back
  at the locked distance. Stepping closer than the locked distance now
  correctly does nothing — no backward/retreat behavior, on purpose, for
  now. Applied to both `docs/index.html` and `app/mirror/page.tsx`.
- Documented the actual distance-tracking mechanism in the README (a
  shoulder-width-in-pixels proxy, not a real measurement) since it's a
  reasonable question and deserves a written answer, not just a chat
  reply — including its real limitations (no absolute calibration,
  assumes facing the camera).
- Fixed a stale, now-incorrect README claim left over from before this
  feature existed ("intentionally does not control the wheels or follow
  a person").

### Added (face expression tracking, follow mode, action log)
- **Face expression tracking**: a second on-device MediaPipe model
  (`FaceLandmarker` + blendshapes) classifies expression into 7 hues —
  happy/sad/angry/surprised/fear/disgust/neutral — via a heuristic
  scorer over the relevant blendshape categories (mouth smile/frown,
  brow down/up, eye wide, jaw open, nose sneer). Sent as `mood` on the
  same `/mirror_pose` feed and applied to the robot's eye LEDs.
- **Expression color palette**, designed (not arbitrary) for 7 distinct,
  immediately-readable hues with established color/emotion associations:
  happy=gold, sad=deep blue, angry=red, surprised=violet-magenta,
  fear=muted violet, disgust=sickly green, neutral=cyan. Added to
  `eyes.py`'s `MOOD_COLORS` (0-7 LED intensities) and a matching
  UI-hex `EXPRESSION_COLORS` table in both mirror pages — the two are
  matched in spirit, not pixel-for-pixel, since they're optimized for
  different media (bright dark-UI swatch vs. real LED brightness).
  `fear` is deliberately excluded from the auto-classifier — its
  blendshape signature overlaps too heavily with `surprised` to
  distinguish reliably from a handful of scores.
- **Distance-holding follow mode**: crossing your wrists in front of your
  chest (detected via each wrist vs. its own shoulder's side of the body
  midline — robust to mirroring) toggles a mode that records your
  current shoulder-width-in-frame as a baseline, then sends
  `locomotion: "forward"/"backward"` to hold that apparent distance as
  you move. Backend (`mirror_control.py`'s new `apply_locomotion()`)
  drives the wheels in short, self-bounding pulses (`DriveMotors`
  already stops itself after each pulse — no separate watchdog needed),
  rate-limited to 0.5s, gentler speed than scripted routines since this
  is live teleop.
- **Visual framerate cap**: the camera loop's expensive work (pose+face
  detection, canvas redraw) is now capped at 40Hz — 2x the backend's own
  20Hz accepted update rate — instead of running at the display's native
  refresh rate (which can be 90/120/144Hz). Detecting/drawing faster than
  that is wasted battery/CPU; neither the eye nor the robot can use it.
- **Real-time action log** on the main dashboard (both `docs/index.html`
  and `app/page.tsx`), same terminal-style pattern as the mirror page's
  existing log: what message was actually sent, what the backend
  actually decided (gesture/locomotion/mood), how long it actually took.
- `mirror_control.py`: `apply_mood()` and `apply_locomotion()`, each with
  their own (looser than the 20Hz arm limit) rate limit — cosmetic/
  coarse updates don't need to share the arm angles' safety-critical
  cadence. `MirrorController` now optionally takes `eyes`/`drive`
  instances; `brain.py` constructs and wires them when
  `ENABLE_MIRROR_CONTROL=1`.
- Synced `eyes.py` across all three copies (`pi-version/`,
  `standalone-tools/`, `esp32-cloud-version/cloud_function/`) — they'd
  drifted (only the cloud copy had gesture eye-cues); added a
  `pi-version/eyes.py` copy since `brain.py` needed one to wire mood
  support in, and it didn't exist there before.

### Fixed (pose mirror wireframe never rendering)
- Root cause of "wireframe/joints not visible" in the camera mirror
  (both `docs/index.html` and `app/mirror/page.tsx`): drawing was gated
  behind a single strict check requiring all 8 of
  shoulders/elbows/wrists/**hips** to be confidently visible. Typical
  webcam framing (sitting at a desk) usually crops the hips out of frame
  entirely, so that gate silently stayed false forever and nothing ever
  drew — not a rendering bug, a never-true condition. Split into two
  gates: draw the wireframe/joint markers whenever any pose is detected
  at all; only require the stricter arm-only check (shoulders, elbows,
  wrists — no hips) before computing and streaming angles to the robot.
  Added a "LOCKING" (pose seen, still no confident arm read) state
  between SEARCHING and TRACKING so the HUD reflects this honestly.
- Also removed the hip *requirement* from the angle math itself, not
  just the draw gate: `side()`/`torsoReference()` now derive a virtual
  torso reference from the head+shoulder landmarks (extending the
  nose-to-shoulder-midpoint vector) and only fall back to it when the
  real hip landmark's confidence is low — so accuracy improves when hips
  are visible, but nothing breaks when they aren't.

### Added (terminal log)
- Both mirror pages now show a real-time terminal-style log
  (`[HH:MM:SS] KIND :: message`, color-coded by kind: SYS/NET/CAM/TRACK/
  UPLINK/ERR) of actual lifecycle events — WASM fetch, model load,
  camera request, tracking lock acquired/lost, uplink connect/first-ack,
  errors. Deliberately not decorative/fake activity — every line
  corresponds to a real state transition, in keeping with this project's
  otherwise-honest telemetry throughout.

### Fixed
- `docs/index.html` (the GitHub Pages dashboard) had drifted out of sync
  with `app/` (the Next.js one): it was missing Codex's 6 newer meme
  gestures (`aura_farm`, `six_seven`, `npc_mode`, `facepalm`,
  `success_pump`, `side_eye`) and had no camera mirror feature at all —
  the pose mirror only existed as a Next.js route. Added the 6 missing
  buttons and ported the camera mirror (on-device MediaPipe tracking +
  the same live HUD from `app/mirror/page.tsx`: corner brackets,
  FPS/latency/lock/uplink readout, glowing joint markers, floating angle
  chips, always-visible angle grid) as vanilla JS/canvas — no
  React/Next.js/build-step needed, matching this file's zero-dependency
  design. Verified end-to-end in-browser: model loads, gesture buttons
  round-trip correctly, and camera-permission denial is handled
  gracefully (shows "Permission denied", resets cleanly, no crash).

### Added
- `app/mirror/page.tsx` HUD overhaul: corner brackets, a live FPS/latency/
  tracking-lock/uplink readout, glowing joint markers on the wireframe,
  and floating angle-label chips positioned at each tracked joint on the
  video, plus an always-visible 4-tile angle grid below it. Joint chips
  are separate (non-mirrored) DOM elements rather than canvas text —
  canvas text would render backwards, since `.camera video,.camera
  canvas` are CSS-mirrored (`scaleX(-1)`) for the front-camera view but
  the chips aren't.

### Fixed
- Stale-closure bug caught while adding the HUD: `loop()` recurses via
  its own `requestAnimationFrame` call, not through React re-renders, so
  a `hud` value captured in that closure would never see later state
  updates. The "uplink streaming" indicator now reads a ref
  (`lastSent.current`) instead. Also fixed a pre-existing (not
  introduced by this change) TS type error on the start/stop button's
  `onClick` handler.

### Changed
- Renamed `docs/dashboard.html` → `docs/index.html`. GitHub Pages (repo
  → Settings → Pages → Source: branch `main`, folder `/docs`) serves
  whatever's in `docs/` and specifically looks for `index.html` as the
  site's default page — without this rename, the Pages URL 404'd at its
  root and only worked if you knew to append `/dashboard.html`.

### Added
- 4 more song-tempo dance routines in `rig_gestures.py`: `dance_jaiho`
  (A.R. Rahman, Bollywood), `dance_everybody` (Backstreet Boys),
  `dance_ymca` (Village People), and a `wakawaka` chain (Shakira, arms +
  wheel-wiggle like the Macarena). Same approach as the existing
  `dance_iwitw`/`dance_ekpal`: original choreography, real BPM tempo,
  never a reproduction of official moves or lyrics.
- `tone_player.py` — an optional beat-synced tone cue that can run
  alongside any dance. Deliberately plays a fixed, song-independent
  5-note arpeggio (only tempo changes, never the notes) rather than any
  real song's melody, since a melody is itself a copyrighted musical
  composition separate from any given recording of it — "just play it in
  tones" doesn't avoid that. Non-blocking, same pattern as
  `motion_engine.py`'s gesture thread.
- Found and fixed a stale test assumption: `test_song_dances.py`'s 15%
  playback-drift tolerance predates "Make rig motion shutdown
  interruptible" swapping `time.sleep()` for `threading.Event.wait()`
  (so gesture playback can be cancelled mid-move) — `Event.wait()`
  measured ~30% more per-call overhead than `sleep()` on this dev
  environment, made the existing tolerance too tight, and had nothing to
  do with the new dances that happened to be the first thing to exercise
  it again. Tolerance widened to 45% with an explanatory comment.

### Added (gestures)
- 6 new dance/meme gestures — `dab`, `flex`, `floss`, `the_robot`,
  `mic_drop`, `finger_guns` — plus a `meme_routine` gesture chain
  (dab+flex+finger_guns), added to both gesture models
  (`rig_gestures.py`, flat `gestures.py` in both copies), the ESP32-cloud
  brain's keyword triggers, and the ESP32 firmware's keyframe library
  (`esp32_cloud_brain.ino`) for real-hardware parity. Exposed in both
  dashboards (`docs/dashboard.html`, `app/page.tsx`) as a "Dance / Meme
  Moves" panel. All joint angles verified within the real [0, 180] servo
  range in `test_real_modules.py`.
- Movement refinement: a small dead-band (`DEAD_BAND_DEG = 1.5`) in
  `motion_engine.py`/`rig_motion_engine.py` — a keyframe move smaller
  than that now snaps directly to target in one command instead of
  spending several interpolation steps easing a correction nobody could
  see. This was an explicitly flagged "not yet built" item from the
  original design conversation.

### Added (protocol port)
- Ported the real Meccanoid servo bus protocol (from
  `alexfrederiksen/MeccanoidForArduino`) into `servo_controller.py` (both
  copies) and both `.ino` firmware files, replacing the old guessed
  3-byte XOR-checksum packet: real HEADER+4-slot+checksum frame, 417µs
  half-duplex bit timing, SERVO_MIN/MAX (0x18-0xE8) angle mapping. This
  was the explicit "suggested next test" in `docs/project-summary.md`.
- `test_real_modules.py`: hand-verified checksum/frame regression tests
  for the new protocol logic.
- Required-hardware list and source/credit attribution
  (`alexfrederiksen/MeccanoidForArduino`, `StormingMoose/ESP-Rider_*`,
  Meccano's own product listing) in the root `README.md`.

### Changed
- `ServoBusConfig(transport="direct", simulate=False)` now raises
  `NotImplementedError` instead of silently sending the wrong bytes at
  real servos — the real protocol's half-duplex single-wire framing
  can't be reproduced over a normal two-wire pyserial UART. Real hardware
  must use `transport="esp32"`.
- `pi-version/pinout.md` and `pi-version/README.md`: corrected wiring
  docs and caveats now that the protocol is sourced rather than guessed,
  and that `transport="direct"` isn't a supported real-hardware path.

## 2026-08-22 — Dashboard, Firestore, Gemini 2.5 integration (`d92e8dd`)

### Fixed
- **Substring false-positive gesture/locomotion triggers.** Every keyword
  matcher used a bare `phrase in text` check, so e.g. `"my elbow hurts"`
  fired the `bow` gesture, `"let's visit downtown"` fired `sit`, and
  `"welcome here"` fired forward locomotion. Replaced with word-boundary
  regex matching in `pi-version/gestures.py`, `pi-version/rig_gestures.py`,
  `esp32-cloud-version/gestures.py`, and `cloud_function/main.py`.
- **Unhandled LLM failures left the robot in an inconsistent state** —
  `RobotBrainService.handle_chat` now catches LLM exceptions and returns
  an error envelope instead of an unhandled 500 with the reply queue
  never populated (gesture/locomotion already fired, but no reply ever
  sent).
- **Corrupted/binary memory file crashed every future `/chat` request** —
  `brain.py`'s memory read is now wrapped in try/except and degrades to
  no memory context instead of raising.
- **ESP32 firmware silently dropped gestures**: `xQueueSend`'s return
  value was never checked, but `/ack_motion` was sent unconditionally —
  a full local queue meant the cloud side believed a gesture was
  delivered when it wasn't, and never retried. Now only acks on a
  successful enqueue.
- **No WiFi reconnect logic** — a dropped connection after boot left the
  robot permanently unresponsive until a manual power-cycle. Added a
  bounded connect timeout in `setup()` and reconnect-on-drop in `loop()`.
- Stale "12/12 servos" telemetry in `docs/dashboard.html`, left over from
  before the hardware was corrected from the assumed XL (8 servos) to
  the real Personal Robot 2.0 (4 servos) — now shows the real count.

### Added
- `test_real_modules.py` — a regression suite that imports and exercises
  the actual production modules (`rig_gestures`, `gestures`,
  `RobotBrainService`), since `test_everything.py` only tested a
  hand-kept-in-sync reimplementation and could pass while the real code
  regressed.
- `esp32-cloud-version/cloud_function/firestore_memory.py` — a
  `FirestoreMemoryStore` implementing `MemoryStore`'s interface, giving
  conversation memory that actually survives a Cloud Function cold start
  (the in-memory list didn't). Selected via `MEMORY_BACKEND=firestore`;
  default stays `memory` for dependency-free local testing.
- `esp32-cloud-version/cloud_function/requirements.txt`.
- Bounded growth + locking for `SimpleQueue`/`MemoryStore` in
  `cloud_function/main.py`, matching the module's already-documented
  concurrent-access design (previously correct only by accident of the
  GIL).
- Defensive parsing for Gemini/Ollama API responses (blocked/empty
  replies now raise a clear error instead of a raw `KeyError`/`IndexError`).
- `.gitignore` (`__pycache__/`, generated `robot_memory.txt`,
  `run_history.json`).

### Changed
- `docs/dashboard.html`: action buttons now send the real trigger phrase
  through the chat pipeline instead of being cosmetic; link settings
  persist in `localStorage`; replies surface `gesture`/`locomotion`/`mood`
  tags and backend errors. Gesture button set corrected to the robot's
  actual 7 gestures (dropped the impossible "Look around" — no head
  servo).
- `MACARENA_ROUTINE` in `rig_gestures.py` type-tagged as distinct from
  `Gesture` (it's a 3-tuple format, not 2-tuple) to prevent a future
  `engine.play(MACARENA_ROUTINE)` misuse.

## 2026-08-22 — Initial project import (`571f319`)

### Added
- `pi-version/`: Flask brain (`brain.py`), pluggable LLM backend
  (Ollama/Claude), servo bus + motion engine with easing/calibration,
  optional Pi→ESP32 servo-timing offload, 4-servo/2-chain rig model and
  gesture library (`rig.py`, `rig_gestures.py`), song-synced dance
  routines, Macarena arm+wheel routine.
- `esp32-cloud-version/`: ESP32 firmware polling a cloud function
  (`cloud_function/main.py`) for commands; Gemini/Claude pluggable LLM
  backend; two-queue non-blocking motion/reply design; locomotion
  (differential drive, 3 turn types, dead-reckoning).
- `standalone-tools/`: eye-display module, acoustic echo cancellation
  demo.
- `docs/`: dashboard UI, BOM/cost calculator, pinouts.
- `test_everything.py`: 29-case dependency-free logic test suite.

### Known limitations (as of this commit)
- Servo bus packet format is a documented placeholder — Meccano never
  published the protocol; needs a logic-analyzer capture to confirm.
- No real hardware exercised anywhere — all testing is
  `simulate=True` mocked I/O.
- Voice I/O, camera/vision, and an autonomy loop were discussed in the
  original design conversation but not built.

"""
tone_player.py
-----------------
Optional beat-synced tone cue for the song-tempo dance routines in
rig_gestures.py (dance_iwitw, dance_ekpal, dance_jaiho, dance_everybody,
dance_ymca, the Waka Waka routine, ...).

IMPORTANT — this does NOT play the actual songs, and never will:
  - No audio files, no streaming, no recordings of any kind.
  - The notes played are a fixed, original 5-note major arpeggio
    (C4-E4-G4-B4-C5), always the same regardless of which dance is
    playing — only the TEMPO changes, matching that dance's BEAT_*
    constant. A song's melody is itself copyrighted (the musical
    composition, separate from any particular recording of it), so
    reproducing a song's actual tune — even as plain beeps, even
    hummed, even MIDI — would still infringe. Keeping the note pattern
    fixed and song-independent is what keeps this on the right side of
    that line: it's a rhythmic "having a moment" cue, not a cover.
  - Start the real track from your own music source if you want actual
    music playing — same approach the dance gesture docstrings already
    suggest for the choreography itself.

Runs on its own daemon thread, non-blocking — same "extra channel that
never delays the main thing" pattern as eyes.py's status/mood calls and
motion_engine.py's gesture playback, so triggering a tone cue never
delays or desyncs the arm movement it's playing alongside.

Real hardware: a piezo buzzer on a GPIO pin, driven with a PWM tone per
note (Arduino/ESP32 `tone()`, or RPi.GPIO software PWM on the Pi).
Simulate-only by default, matching this project's "prove the logic
first, hardware later" pattern throughout.
"""

import threading
import time
from typing import Optional

# Fixed, song-independent arpeggio (C major: C4 E4 G4 B4 C5) in Hz — see
# the module docstring for why this never varies by song.
_ARPEGGIO_HZ = [262, 330, 392, 494, 523]

_real_hardware_warned = False


class ToneSequencer:
    def __init__(self, simulate: bool = True):
        self.simulate = simulate
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def play_beats_async(self, beat_seconds: float, num_beats: int) -> None:
        """Starts (or restarts) a background cue: one arpeggio note per
        beat, cycling through _ARPEGGIO_HZ, for num_beats beats. Returns
        immediately — call this right when you trigger a dance gesture,
        it runs alongside the motion engine's own thread without
        blocking either one."""
        self.stop()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run, args=(beat_seconds, num_beats), daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)

    def _run(self, beat_seconds: float, num_beats: int) -> None:
        for i in range(num_beats):
            if self._stop_event.is_set():
                return
            hz = _ARPEGGIO_HZ[i % len(_ARPEGGIO_HZ)]
            self._play_note(hz, beat_seconds)
            if self._stop_event.wait(beat_seconds):
                return

    def _play_note(self, hz: int, duration_s: float) -> None:
        if self.simulate:
            print(f"[SIM/tone] {hz}Hz for {duration_s:.3f}s")
            return
        global _real_hardware_warned
        if not _real_hardware_warned:
            print(
                "[tone_player] real hardware tone output isn't wired up — "
                "no piezo buzzer driver implemented. Running silently. "
                "See this module's docstring for what real hardware needs."
            )
            _real_hardware_warned = True


def beats_for_gesture(gesture) -> int:
    """Counts how many beats a dance_* gesture's keyframes span, so a
    caller can size play_beats_async() to actually cover the whole
    routine without needing to hand-count keyframes itself."""
    return len(gesture)


# ---------------------------------------------------------------------------
# Manual test: `python tone_player.py`
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    seq = ToneSequencer(simulate=True)
    print("Playing a 4-beat cue at dance_iwitw's tempo (99 BPM)...")
    seq.play_beats_async(60 / 99, 4)
    time.sleep(60 / 99 * 4 + 0.1)
    print("Done — non-blocking, main thread was free the whole time.")

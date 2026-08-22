"""
aec_algorithm_demo.py
------------------------
Proves the ADAPTIVE FILTER ALGORITHM behind acoustic echo cancellation
works, using synthetic signals — not a substitute for Espressif's real
ESP-SR AEC module, but validates the underlying math (NLMS adaptive
filtering) before committing to the real ESP-IDF firmware integration,
which is a much bigger lift than anything simulated so far in this
project.

Setup being modeled:
  - "far-end" signal = what the speaker plays (the TTS reference)
  - "echo path" = how that sound reflects/attenuates before the mic
    picks it up (modeled here as a short FIR impulse response)
  - "mic signal" = echo (reference passed through the echo path) +
    near-end speech (what the person actually says) + noise
  - GOAL: recover near-end speech by estimating and subtracting the
    echo, using only the reference and mic signals — exactly what
    ESP-SR's AEC does in real time on-device.

NLMS (Normalized Least Mean Squares) is the standard adaptive-filter
algorithm for this — it's what most real AEC implementations, including
Espressif's, are built on.
"""

import numpy as np


def make_echo_path(length=64, decay=0.35, seed=0):
    """Synthetic room/speaker-to-mic impulse response — a few reflections
    decaying over time, standing in for a real acoustic echo path."""
    rng = np.random.default_rng(seed)
    h = rng.normal(0, 1, length) * np.exp(-decay * np.arange(length))
    h /= np.max(np.abs(h))
    return h


def nlms_filter(reference, mic_signal, filter_length=64, mu=0.5, eps=1e-6,
                 freeze_from: int = None):
    """Normalized LMS adaptive filter. `freeze_from`, if given, stops
    adapting the filter weights after that sample index — this models
    a DOUBLE-TALK DETECTOR, which real AEC systems need: naive NLMS
    misbehaves if it keeps adapting while both far-end (echo) and
    near-end (person talking) signals are present at once, since it
    can't distinguish "echo path changed" from "someone started
    talking." Real ESP-SR AEC handles this internally; this demo makes
    the same requirement explicit rather than glossing over it."""
    n = len(mic_signal)
    w = np.zeros(filter_length)
    error = np.zeros(n)
    estimated_echo = np.zeros(n)

    ref_padded = np.concatenate([np.zeros(filter_length), reference])

    for i in range(n):
        x = ref_padded[i:i + filter_length][::-1]
        y_hat = np.dot(w, x)
        e = mic_signal[i] - y_hat

        if freeze_from is None or i < freeze_from:
            norm = np.dot(x, x) + eps
            w += (mu / norm) * e * x

        error[i] = e
        estimated_echo[i] = y_hat

    return error, estimated_echo


def run_demo():
    sample_rate = 16000  # matches ESP-SR's required rate
    duration_s = 1.0
    n = int(sample_rate * duration_s)
    t = np.arange(n) / sample_rate

    rng = np.random.default_rng(42)

    # Far-end reference: what the robot's TTS is playing (synthetic tone burst)
    reference = 0.8 * np.sin(2 * np.pi * 440 * t) * np.hanning(n)

    # Real acoustic echo path (speaker -> room -> mic)
    echo_path = make_echo_path()
    echo = np.convolve(reference, echo_path, mode="full")[:n]

    # Near-end speech: what the person actually says — different frequency,
    # only present in the second half (simulating them speaking mid-reply)
    near_end = np.zeros(n)
    speech_region = slice(n // 2, n)
    near_end[speech_region] = 0.3 * np.sin(2 * np.pi * 200 * t[speech_region])

    noise = rng.normal(0, 0.01, n)
    mic_signal = echo + near_end + noise

    # Run AEC — freeze adaptation once near-end speech starts (double-talk
    # detection), same requirement real AEC systems have
    aec_output, estimated_echo = nlms_filter(reference, mic_signal, freeze_from=n // 2)

    # Metrics, measured in the regions where they're meaningful:
    #  - ERLE during the PURE ECHO period (before speech starts) — did the
    #    filter learn the echo path correctly?
    #  - near-end correlation during the SPEECH period — with the filter
    #    frozen at a good echo estimate, does real speech survive cancellation?
    pure_echo_region = slice(0, n // 2)
    echo_energy_before = np.sum(echo[pure_echo_region] ** 2)
    echo_energy_after = np.sum(aec_output[pure_echo_region] ** 2)
    erle_db = 10 * np.log10(echo_energy_before / max(echo_energy_after, 1e-12))

    near_end_correlation = np.corrcoef(near_end[speech_region], aec_output[speech_region])[0, 1]

    print("=== AEC algorithm demo (NLMS, synthetic signals) ===")
    print(f"  sample rate:                  {sample_rate} Hz (matches ESP-SR's requirement)")
    print(f"  echo energy before AEC:       {echo_energy_before:.3f}")
    print(f"  echo energy after AEC:        {echo_energy_after:.3f}")
    print(f"  ERLE (echo return loss enh.): {erle_db:.1f} dB  (higher = more echo removed)")
    print(f"  near-end speech correlation:  {near_end_correlation:.3f}  "
          f"(closer to 1.0 = person's real voice preserved)")

    assert erle_db > 15, f"expected meaningful echo suppression, got {erle_db:.1f} dB"
    assert near_end_correlation > 0.8, \
        f"near-end speech should survive cancellation, correlation={near_end_correlation:.3f}"
    print("\n  PASS: echo suppressed substantially, near-end speech preserved")
    print("\n  NOTE: this validates the NLMS algorithm on synthetic signals only,")
    print("  and required freezing adaptation once near-end speech starts (a")
    print("  'double-talk detector') — naive adaptation during simultaneous")
    print("  speaker+mic activity corrupts the filter, which is a real")
    print("  constraint any AEC implementation has to handle, not a simulation")
    print("  artifact. Real acoustic AEC on the ESP32 needs the actual ESP-SR")
    print("  component (esp_aec.h), a wired reference channel from the speaker")
    print("  output, and tuning against the real mic/speaker/enclosure — this")
    print("  demo is the 'does the math work' check, not hardware validation.")


if __name__ == "__main__":
    run_demo()

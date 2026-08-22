# Audio Wiring — I2S Speaker + Microphone

This is new, independent hardware — not a reuse of the Meccanoid's
stock speaker/mic, which are wired directly to the original MeccaBrain
and can't be repurposed for LLM-generated speech (see project notes).
Same principle as the servo/LED takeover: replace, don't hack the
stock board.

## Parts
- **MAX98357A** I2S Class-D amplifier breakout (speaker output)
- **INMP441** I2S MEMS microphone (voice input)
- A small 4Ω or 8Ω speaker (any wattage — the amp just won't be driven
  to full power with a low-wattage speaker, which is fine and safer)

## Why two separate I2S buses

Audio *in* (mic) and audio *out* (speaker) run as two independent I2S
peripherals — trying to share one clock/word-select pair between input
and output on a classic ESP32 is more trouble than it's worth. Wire
them to separate pin sets as below.

## Wiring diagram

```
                    ESP32 (WROOM-32, 38-pin)
                 ┌─────────────────────────────┐
                 │                             │
   MAX98357A     │                             │      INMP441
   (speaker out) │                             │      (mic in)
  ┌───────────┐  │                             │  ┌───────────┐
  │ LRC   ────┼──┤ GPIO25 (I2S0 WS)             │  │
  │ BCLK  ────┼──┤ GPIO26 (I2S0 BCLK)           │  │
  │ DIN   ────┼──┤ GPIO22 (I2S0 DOUT)           │  │
  │ GAIN  ────┼──┤ (leave floating = 9dB,       │  │
  │           │  │  or tie to GND/VIN per       │  │
  │           │  │  datasheet for other gains)  │  │
  │ SD    ────┼──┤ 3.3V (always enabled)        │  │
  │ VIN   ────┼──┤ 5V                            │  │
  │ GND   ────┼──┤ GND                           │  │
  │ +/- ──────┼──┼─────────► to speaker terminals│  │
  └───────────┘  │                             │  └───────────┘
                 │                             │
                 │                             │    SCK ──── GPIO14 (I2S1 BCLK)
                 │                             │    WS  ──── GPIO15 (I2S1 WS)
                 │                             │    SD  ──── GPIO32 (I2S1 DIN)
                 │                             │    L/R ──── GND (selects left channel)
                 │                             │    VDD ──── 3.3V
                 │                             │    GND ──── GND
                 └─────────────────────────────┘
```

## Pin summary table

| Signal | ESP32 pin | Goes to | Notes |
|---|---|---|---|
| I2S0 WS (LRC) | GPIO25 | MAX98357A `LRC` | speaker word-select |
| I2S0 BCLK | GPIO26 | MAX98357A `BCLK` | speaker bit clock |
| I2S0 DOUT | GPIO22 | MAX98357A `DIN` | speaker data |
| I2S1 BCLK | GPIO14 | INMP441 `SCK` | mic bit clock |
| I2S1 WS | GPIO15 | INMP441 `WS` | mic word-select |
| I2S1 DIN | GPIO32 | INMP441 `SD` | mic data |
| — | GND | INMP441 `L/R` | selects left channel (tie high for right) |
| 3.3V | — | INMP441 `VDD` | |
| 5V | — | MAX98357A `VIN` | needs 5V for full power output |

## Notes before wiring

- **MAX98357A `GAIN` pin** sets amplifier gain: floating = 9dB (default,
  reasonable starting point), tied to GND = 15dB (louder, more
  distortion risk with a small speaker), tied to VDD = 3dB (quieter).
  Start floating and adjust if needed.
- **Power draw**: the amplifier can pull meaningful current at volume —
  share the same isolated power bank used for the servos/motors, not
  a rail feeding the ESP32's own logic, same reasoning as the servo
  power isolation from earlier.
- **This is untested** — like the rest of this project, verify wiring
  against the actual breakout board's silkscreen labels before
  powering up, since pinout can vary slightly between manufacturers of
  "generic" MAX98357A/INMP441 breakouts.

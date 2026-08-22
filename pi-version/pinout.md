# Raspberry Pi Wiring — Meccanoid Cloud-AI Brain

## Pins you actually need (of the 40-pin header)

```
                 Raspberry Pi 40-pin header (top view)
                 ┌─────────────────────────────┐
            3V3  │ 1  ●  ● 2  │ 5V              <-- Pi 5V rail: DO NOT power
            SDA1 │ 3  ●  ● 4  │ 5V                  servos from here (see note)
            SCL1 │ 5  ●  ● 6  │ GND  ◄───────┐
             GP4  │ 7  ●  ● 8  │ TXD ◄─GP14   │      to level shifter GND
             GND  │ 9  ●  ●10  │ RXD ◄─GP15   │
            GP17  │11  ●  ●12  │ GP18         │
            GP27  │13  ●  ●14  │ GND ─────────┘
            GP22  │15  ●  ●16  │ GP23
             3V3  │17  ●  ●18  │ GP24
            MOSI  │19  ●  ●20  │ GND
            MISO  │21  ●  ●22  │ GP25
            SCLK  │23  ●  ●24  │ CE0
             GND  │25  ●  ●26  │ CE1
                 └─────────────────────────────┘

Used by this project:
  Pin 8  (GPIO14 / TXD) ──► level shifter LV1 (Pi side)
  Pin 10 (GPIO15 / RXD) ◄── level shifter LV2 (Pi side)
  Pin 6 or 9 (GND)      ─── level shifter GND (shared reference, required)
```

## Full signal path

```
 Raspberry Pi                Logic Level             Meccanoid
 ────────────                Shifter (3.3V<->5V)      servo bus
 GPIO14 (TXD, pin 8) ──────► LV1        HV1 ─────────► servo data in
 GPIO15 (RXD, pin 10) ◄───── LV2        HV2 ◄───────── servo data out
 GND (pin 6 or 9)    ──────► LV-GND     HV-GND ───────► servo bus GND
 3.3V (pin 1 or 17)  ──────► LV (low-voltage supply)
                              HV (high-voltage supply) ◄── 5V from the
                                                            servo power bank
                                                            (NOT from the Pi)
```

## Why the Pi's own 5V pin is NOT used for servo power

Pins 2/4 on the header are the Pi's 5V rail, fed straight from whatever
powers the Pi. Servos can draw sudden current spikes when they move or
stall — sharing that rail can brown out the Pi mid-conversation. That's
the isolation the original architecture diagram called for:

```
Portable power bank (5V/3A) ──► Meccanoid servo bus + level shifter HV side
Pi's own power supply        ──► Pi only (and level shifter's LV side, 3.3V)
GND                           ──► must still be common between both supplies
```

Common ground between the Pi and the servo power bank is required even
though their positive rails are separate — without it the level shifter
and UART signaling won't reference the same 0V.

## Pull-up resistors

4.7kΩ pull-ups go on the level shifter's HV-side data lines (to HV/5V),
not on the Pi's 3.3V side — exact placement depends on the specific
level shifter board's datasheet (some breakout boards, like the common
4-channel bidirectional shifters, already include onboard pull-ups and
you don't need to add your own).

## Quick pin summary

| Signal | Pi pin | Pi GPIO | Goes to |
|---|---|---|---|
| UART TX | 8 | GPIO14 | Level shifter LV1 |
| UART RX | 10 | GPIO15 | Level shifter LV2 |
| Ground | 6 or 9 | GND | Level shifter GND (shared with servo bank GND) |
| 3.3V | 1 or 17 | 3V3 | Level shifter LV supply |

Everything else on the 40-pin header is unused by this project — those
pins stay free for a future camera, mic, or additional sensors.

## Before trusting this on hardware

This assumes the Pi's UART (`/dev/serial0`) is enabled and the Bluetooth
serial console isn't fighting for the same pins — on Pi models with
onboard Bluetooth, you'll want to either disable the Bluetooth UART or
use a USB-to-serial adapter instead of the GPIO UART, depending on your
Pi model. Check `raspi-config` → Interface Options → Serial Port, and
disable the login shell over serial while enabling the hardware itself.

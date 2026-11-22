# Experimental: Bluetooth speaker output (branch `exp/bt-transmitter`)

This is a spin-off. A player can send its room's audio to a Bluetooth
speaker (A2DP source) instead of its wired output (HDMI, DAC or amp board).
It lives only on `exp/bt-transmitter`, so it can be dropped easily.

## How it works

- **Player:** `bluez-alsa-utils` (bluealsa) runs only the `a2dp-source`
  profile. It is not started at boot. freesounderd starts it once a speaker
  is chosen.
- **Output:** `select-audio-output` picks
  `bluealsa:DEV=<mac>,PROFILE=a2dp` when `/etc/freesounder/bt-output` is
  enabled and bluealsa has a PCM for that speaker. Otherwise it uses the
  wired output. The fsdsp chain (EQ, limiter) and the room volume still
  apply. snapclient restarts when the output changes.
- **freesounderd (`bluetooth.py`):**
  - Scans BR/EDR only, and pairs with a one-shot agent that accepts just
    the chosen speaker.
  - Trusts and connects the speaker, then tries to reconnect every 10 s
    while it is away (boot, speaker switched off or out of range).
  - While the speaker is away the room plays nothing: it never falls back to
    the DAC/HDMI (0.6.18). A room uses Bluetooth or its wired output, never
    both. Choose **Wired output** in Room settings to play through the DAC.
- **Opening the speaker (0.6.9):**
  - The output is chosen only once bluealsa's PCM for the speaker has a
    configured transport. select-audio-output waits up to 6 s for it.
  - The chain is pinned to the transport's format, rate and channels (from
    `bluealsa-cli info`, usually SBC at 44.1 or 48 kHz, S16_LE, stereo), and
    the whole chain is resampled once to that rate.
  - The chain is then opened for real. If it fails (the ALSA "empty hw-params
    interval" assertion seen on the first image), a direct `plug` into
    bluealsa is tried without tone controls. If that fails too, the wired
    output is used and the room info says why.
- **Latency:** Bluetooth adds a delay. The room's Snapcast client latency is
  set to the speaker latency, 200 ms by default. You can change it in the
  app from 0 to 500 ms, and the API accepts up to 1000 ms. Wired output uses
  0.
- **BLE setup:** Improv BLE setup is unchanged. The adapter is only made
  pairable while a speaker is being paired.

## Dropouts (patchy sound)

- snapclient plays to Bluetooth with a 400 ms ALSA buffer in 8 periods
  (`--player alsa:buffer_time=400,fragments=8`, written by
  `select-audio-output` to `/run/freesounder/snapclient-player.env`); the
  default 80 ms is too short for the Bluetooth link's stalls.
- bluealsa runs its IO threads real-time (`--io-rt-priority=40`).
- Wi-Fi power saving is off (`/etc/NetworkManager/conf.d/20-freesounder-wifi-powersave.conf`).
- Nothing on the player polls Bluetooth while it plays. Up to 0.6.16 the
  BLE setup service rebuilt its boot-partition doctor report every 2 s, even
  when online. That ran `bluetoothctl show` (which registers an advertisement
  monitor with bluetoothd each time) and rewrote a file on the SD card, and
  it broke up speaker audio every few seconds. Since 0.6.17 the report is only
  rebuilt when the network or advertising state changes (and at most once a
  minute while offline), and it reads the adapter over D-Bus instead.
- Wi-Fi on 2.4 GHz shares the Pi's radio with Bluetooth. Use 5 GHz Wi-Fi or
  Ethernet where you can.
- Diagnostics → `audio.link`: Wi-Fi band/signal/power save, the Bluetooth
  RSSI (`btmgmt conn-info`), the transport (codec, rate), and counts of
  snapclient ALSA underruns versus network gaps.

## API (per room, forwarded to the room's player)

| Method | Path | Body |
|---|---|---|
| GET | `/api/v2/rooms/{id}/bluetooth` | status |
| POST | `/api/v2/rooms/{id}/bluetooth/scan` | `{"seconds": 8}` |
| POST | `/api/v2/rooms/{id}/bluetooth/connect` | `{"mac": "AA:BB:…", "name": "…"}` |
| POST | `/api/v2/rooms/{id}/bluetooth/output` | `{"enabled": true}` (false = wired) |
| POST | `/api/v2/rooms/{id}/bluetooth/latency` | `{"latency_ms": 200}` |
| DELETE | `/api/v2/rooms/{id}/bluetooth` | forget the speaker |

## Known limits

- **Codec:** Debian's bluealsa has no AAC (fdk-aac is non-free), so speakers
  use SBC. aptX and LDAC are built in, but only some speakers support them.
- **Radio:** The Pi Zero 2 W and Pi 3 share one 2.4 GHz radio for Wi-Fi and
  Bluetooth, so audio may drop out. 5 GHz Wi-Fi (Pi 3B+, 4 and 5) or
  Ethernet helps. The app shows a warning on those models.
- **Speakers:** Each room has one speaker. Speakers that need a PIN other
  than 0000 are not supported.

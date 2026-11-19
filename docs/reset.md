# Factory reset a FreeSounder player

A factory reset makes a player forget:

- its Wi-Fi network and password (every saved Wi-Fi connection),
- its home: the household key and the other players it knew,
- its room name, group, sound settings, radio favourites and Spotify sign-in,
- its player id (it comes back as a new player).

It then goes back to **Bluetooth setup mode** with the hostname `fs-new`, ready
to be added again in the app ("Add a player").

These stay the same: the `fsadmin` console account, the Wi-Fi country and
the DAC/amp choice. Developer SSH is switched off.

While it resets, the green ACT LED blinks fast and, if the player has a working
speaker, it plays a short tone. The HDMI status screen shows
`>>> Resetting... do not unplug <<<`, then `>>> Reset - ready for setup <<<`
until the player is set up again.

The other players in the home still list the old room as **Offline**. Remove
it with the bin icon next to it in the app's Rooms list.

There are four ways to reset. None of them uses a 40-pin header GPIO, so
nothing interferes with DAC/amp HATs (I2S on GPIO18-21, I2C on GPIO2/3).

## 1. In the app (software reset)

Open the room, **Room settings**, scroll to the bottom and tap **Factory
reset** (red). Confirm in the dialog. The app calls
`POST /api/v2/rooms/{id}/factory-reset` with `{"confirm": true}` (forwarded to
that player), which runs the same routine as the other methods
(`/usr/lib/freesounder/factory-reset --reason app`). The player restarts in
setup mode about a minute later.

## 2. Raspberry Pi 5: the power button

| Action | Result |
| --- | --- |
| 1 press (then nothing for 1.2 s) | clean shutdown (red LED). Press again to start the Pi. |
| **5 presses within 3 s** | **factory reset** (starts at the 5th press, then reboots) |
| 2-4 presses | nothing (so a mis-count never shuts it down) |
| hold 10 s | hard power-off by the Pi 5 firmware (FreeSounder does not change this) |

`systemd-logind` is set to `HandlePowerKey=ignore`, and
`freesounder-buttons.service` reads the power key from the input device.

## 3. Any Pi, no tools: power-cycle 5 times

1. Plug the power in, wait **about 20 seconds**, then unplug it.
2. Do this **5 times in a row**. From the 2nd time, about 10 s after you plug
   in, the green LED blinks once for each short boot counted so far (2, 3, 4,
   then 5 blinks).
3. Plug it in a 6th time and **leave it**. It resets as it starts (about 1 minute),
   with no extra reboot.

Exact timing:

- Early in each boot (right after the SD card is mounted, about 5-15 s after
  you plug in, depending on the model), `freesounder-boot-count.service`
  adds 1 to `/var/lib/freesounder/short-boots`. It is a single small write,
  fsync'd.
- `freesounder-boot-count-clear.service` sets the count back to 0 once the
  system has been up for **40 s** (kernel uptime, about 42-45 s after you plug
  in).
- So a boot counts as "short" only if you unplug it between about **15 s and
  40 s** after plugging in. 20 s works on every model.
- When a boot starts with a count of 5 or more, the player resets.

Why ordinary use does not trigger it:

- Every boot that runs for more than 40 s clears the count.
- A single power cut, or a few reboots in a row, never adds up to 5 unless
  every one of them is cut inside that window.
- If the Pi reports **under-voltage** at that moment (`vcgencmd
  get_throttled`), the boot is not counted and the count is cleared. A weak
  power supply that makes the Pi restart over and over therefore never
  resets it.
- Unplugging before the counter is written (under about 5 s) does nothing
  either way.

The power button is on the Pi 5's power chip, not on the 40-pin header.

**No wired header button (since 0.6.11).** Earlier images offered a push
button on GPIO3 (pin 5). GPIO3 is also the I2C clock line that DAC/amp HATs
(HiFiBerry DAC+ and clones such as the PiFi DAC+ 2.0, IQaudio, Amp boards)
use, and the button service claiming it could leave the sound card listed but
silent. It was removed: `/etc/freesounder/button` and the `gpiomon` monitor are
gone, and FreeSounder no longer touches any header GPIO. Use the app reset
instead.

## 4. From the console

On the HDMI console (log in as `fsadmin` on tty2), or over developer SSH:

```
sudo freesounder-setup factory-reset
```

`sudo freesounder-setup reset` only leaves the home and keeps Wi-Fi.

## Diagnostics

**Persistent journal.** `/etc/systemd/journald.conf.d/90-freesounder.conf` sets
`Storage=persistent` (50 MB cap, 1 month) and `SyncIntervalSec=15s`, so a hard
power loss loses at most ~15 s of log. Raspberry Pi OS ships
`/usr/lib/systemd/journald.conf.d/40-rpi-volatile-storage.conf`
(`Storage=volatile`); in 0.6.5 it overrode ours, so no log survived a reboot.
The image masks it (a `/dev/null` symlink of the same name in /etc). The
previous boot is looked up with `journalctl --merge --list-boots`, so a
machine-id change (first boot, reflash) does not hide it. 0.6.6's first boot is
the first one with a persistent log.

**Heartbeat** (`freesounder-heartbeat.service`, log tag `freesounder-power`).
Every 15 s it rewrites `/var/lib/freesounder/heartbeat.json` (atomic, fsync,
~250 bytes) with the time, uptime, Pi 5 input voltage (`EXT5V_V`) and its
minimum this boot, CPU temperature and `get_throttled` flags. Flag changes are
appended to `power-history.json` (last 100). It logs warnings for
`UNDER-VOLTAGE`, `THROTTLING`, `LOW INPUT VOLTAGE` (< 4.75 V) and `HOT`
(>= 80 C). On a clean shutdown it logs `CLEAN SHUTDOWN started: poweroff|reboot|halt`
and marks the heartbeat clean. At the next boot the old heartbeat becomes
`last-boot.json`; if it was not clean it logs `PREVIOUS BOOT DID NOT SHUT DOWN CLEANLY`
with the last-seen time and uptime.

**CPU and heat** (0.6.8). Each heartbeat also records the load average
(1/5/15 min), total CPU busy %, the CPU clock (`scaling_cur_freq`, max,
governor) and the top 5 processes by CPU over the last 15 s (% of one core,
like `top`), all read from /proc and /sys. A `cpu:` line with the same facts
is logged every 5 min, and every beat while the CPU is at or above 80 C; the
`HOT` and `THROTTLING` warnings include the top processes. `last-boot.json`
keeps them, so Diagnostics can show what was running before a crash.

**Thermal safeguard.** From 75 C the heartbeat lowers the cpufreq maximum
(`scaling_max_freq`) to 1.5 GHz (never below the board's own minimum; the Pi 5
minimum is 1.5 GHz) and restores the full clock below 65 C, logging
`THERMAL: CPU clock capped ...` / `... back to ...`. Audio needs a few percent
of one core, so this never affects playback. `/etc/freesounder/cpu` can hold
`auto` (default), `cool` (always capped) or `off` (never touch the clock).

**Buttons.** Each Pi 5 power-button press logs `POWER BUTTON pressed (n/5 to reset)`; a single press logs
`POWER BUTTON: clean shutdown requested by the power button`.

**Pi 5 firmware.** `/proc/device-tree/chosen/power/power_reset` is the PMIC
reset reason (bit 0 over-voltage, 1 under-voltage, 2 over-temperature,
3 enable signal, 4 watchdog), `max_current` the current the PSU offered
(5000 mA for the official 27 W supply), and `usb_over_current_detected`.
`chosen/bootloader/rsts` is shown raw.

To see why a player stopped last time:

- in the app with developer mode on: room settings, then **Diagnostics**,
- or `sudo freesounder-setup diagnostics` on the console.

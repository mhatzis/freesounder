# freesounder player image

A single **64-bit arm64** Raspberry Pi OS Lite image intended to boot on both
**Raspberry Pi 4** and **Raspberry Pi 5**. It is a room player. There is no
separate cupboard server.

When the board has at least 3.5 GiB of RAM (a Pi 4 4GB or Pi 5 4GB), it
defaults to the **household host** role: official Music Assistant in Docker
(`ghcr.io/music-assistant/server`, host networking, data in
`/var/lib/freesounder/ma-data`) plus the freesounder control API. Every board,
including the host, runs `snapclient`. Smaller boards default to the **room**
role (snapclient + control API only). Override with `freesounder-setup`.

This image is **not** boot-tested on real Pi 4/Pi 5 hardware in the environment
that first built it. Treat early builds as alpha.

## Flash with Raspberry Pi Imager

1. Install [Raspberry Pi Imager](https://www.raspberrypi.com/software/).
2. Choose **Raspberry Pi Device** (Pi 4 or Pi 5 — the same image).
3. Operating system: choose **Use custom** and select the
   `freesounder-*-lite-arm64.img.xz` file (Imager accepts a compressed `.img.xz`).
4. Storage: your microSD, USB SSD, or (Pi 5) NVMe, as appropriate.
5. Open **OS customisation** (the gear / “Edit settings”) **before** writing:
   - Set a **username and password**. This image does **not** ship a default
     login. SSH is enabled, but you cannot log in until a user exists.
   - Set the hostname if you do not want the default `freesounder` (each room
     should have a unique name, for example `kitchen` or `lounge`).
   - You do **not** need to set Wi-Fi in Imager. An unprovisioned board with no
     Ethernet and no working Wi-Fi advertises over Bluetooth as
     `FreeSounder-XXXX`. The FreeSounder app walks you through joining a
     network (Improv Wi-Fi). Ethernet still works if you plug a cable in.
   - Optionally set Wi-Fi here anyway if you prefer, and set a wireless
     country. The household host should prefer Ethernet when you have it.
   - You can also set locale and timezone here (the image default timezone is UTC).
6. Write the image, eject the media, and boot the Pi.

First boot on a 4 GB host needs **internet access** so Docker can pull
`ghcr.io/music-assistant/server`. Rooms only need the LAN to reach the host.
If the board has no network yet, use **Settings → Add a player** in the app
and choose the nearby Bluetooth player, then join Wi-Fi; adoption continues
once it appears on the network.

After login:

```bash
sudo freesounder-setup status
sudo freesounder-setup set-role auto    # or host / room
sudo freesounder-setup set-host <hostname-or-ip-of-the-household-host>
sudo freesounder-setup set-name Kitchen
sudo freesounder-setup set-dac dacplus  # DAC+ or DigiAMP+ (same overlay)
```

Prefer the FreeSounder app for everyday setup: **Settings → Connect** to reach
an existing host, **Add a player** to name a new Pi and join it, and the gear
on a room for bass, treble, balance, bass boost, volume limit and DAC.
`freesounder-setup` remains for SSH and first-boot recovery.

DAC+: `dtoverlay=hifiberry-dacplus` in `/boot/firmware/config.txt`. Official
DigiAMP+ uses the same overlay. A reboot is required after `set-dac`. Then
plug in powered speakers (DAC+) or passives (DigiAMP+; check PSU needs).

## What is installed

- Raspberry Pi OS Lite, 64-bit (Trixie), hostname `freesounder`, SSH enabled
- `snapclient`, ALSA utilities, Avahi
- Docker Engine (`docker.io`) and a systemd unit that starts Music Assistant
  **only** in the host role
- freesounder control API (`services/control-api`) in a Python 3.13 venv,
  listening on port 8080. Only the **host** advertises `_freesounder._tcp`.
- player agent (`services/player-agent`) on port 8090, advertised on every
  board as `_freesounder-player._tcp` (state `unconfigured` until adopted)
- Improv Wi-Fi BLE provisioning (`services/ble-provisioning`) and `bluetooth.service`.
  Advertising runs only while the board has no usable network. This is a
  peripheral for setup only; it does not register Bluetooth audio.
- ALSA LADSPA tone controls (`caps` Eq10X2) on a PCM named `freesounder`;
  `snapclient` outputs through that device (`--soundcard freesounder` is baked
  into `/etc/default/snapclient`)
- `freesounder-setup` for role, host, room name, DAC overlay, and optional MA token

No passwords, API tokens, or wireless secrets are baked into the image.

## Build it yourself

On a Debian-based amd64 or arm64 host with loop-device access, `qemu-user`
binfmt (for cross builds), and the packages listed in the pi-gen README
(`debootstrap`, `parted`, `quilt`, `kpartx`, `qemu-user-static`, `mtools`, …):

```bash
sudo ./images/player/build.sh
```

This clones the official [pi-gen](https://github.com/RPi-Distro/pi-gen)
**arm64** branch, builds Lite (stages 0–2) plus
`images/player/pi-gen/stage-freesounder`, and writes an `.img.xz` under
`/workspace/freesounder-build/deploy` (override with `DEPLOY_DIR`). Config lives
in `images/player/pi-gen/config`. The build script switches the pi-gen apt
sources to **HTTPS** so they work on hosts whose HTTP path to the Raspberry Pi
mirrors is intercepted. On some sandboxed networks it also temporarily disables apt
SRV records (`Acquire::EnableSrvRecords false`) because those lookups can be
steered at a broken intercept; that setting is removed before the image is exported.

If the build host kernel has `loop.max_part=0` (no `/dev/loop0p1` nodes and no working device-mapper), `build.sh` patches pi-gen's image export to attach each partition with `losetup --offset`.
If the kernel also lacks VFAT (`CONFIG_VFAT_FS` is not set), the boot partition is staged on a bind mount and written with `mtools` (`mcopy`) at the end of export.

A native (non-Docker) pi-gen build is the default. Docker is not required on
the build machine; it is installed **inside** the image.

## Limitations

- Not boot-tested on real Pi hardware in the first builder environment.
- First host boot requires a registry pull of Music Assistant (network).
- Music Assistant schema ≥ 28 needs a long-lived token; set it with
  `sudo freesounder-setup set-ma-token` after creating one in the MA UI.
- Automatic host failover is **not** in the first beta; back up
  `/var/lib/freesounder/ma-data`.
- The default hostname `freesounder` will clash if two unconfigured boards
  share a LAN. Rename them in Imager, with `hostnamectl`, or by adopting
  the player in the app (that also sets a hostname-friendly label).
- Tone controls use ALSA LADSPA (`caps`). A CamillaDSP YAML file is written
  as well, but CamillaDSP is not bundled in this image. Changing DAC still
  needs a reboot. These paths have not been tested on real Pi hardware yet.
- QEMU system emulation of a Pi 4/5 is not a substitute for a real boot test.

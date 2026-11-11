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

Players are locked-down appliances. You should not set a username, password,
or hostname in Imager — the image ignores those customisations.

1. Install [Raspberry Pi Imager](https://www.raspberrypi.com/software/).
2. Choose **Raspberry Pi Device** (Pi 4 or Pi 5 — the same image).
3. Operating system: choose **Use custom** and select the
   `freesounder-*-lite-arm64.img.xz` file (Imager accepts a compressed `.img.xz`).
4. Storage: your microSD, USB SSD, or (Pi 5) NVMe, as appropriate.
5. Skip **OS customisation** for user, password, hostname, and SSH. The first
   boot sets the hostname to `fs-new`. When you name the room in the app the
   hostname becomes `fs-<room>` (for example, Living Room → `fs-living-room`).
   HDMI on tty1 shows a status screen (hostname, room, IP, BLE, SSH), not a
   login prompt. A local `fsadmin` account is available on tty2 and above;
   its password is set at image build time and is not stored in the source
   tree. Do not set a different user in Imager — those settings are ignored.
6. You do **not** need to set Wi-Fi in Imager. An unprovisioned board with no
   Ethernet and no working Wi-Fi advertises over Bluetooth as
   `FreeSounder-XXXX`. The primary advertisement is the Improv 128-bit service
   UUID plus service data (31/31 bytes). Manufacturer-specific data (Bluetooth
   SIG test company ID `0xFFFF`, payload `FreeSounder`) and the short name
   `FS-XXXX` (BlueZ `LocalName`) are in the scan response (24/31 bytes). The
   app shows `FS-XXXX` as `FreeSounder-XXXX`. If BlueZ refuses that, the
   service falls back to the name alone, then to no scan response; the status
   screen says which one is live.
   Wi-Fi ships unblocked with regulatory country `AU`
   (`/etc/freesounder/wifi-country`, `cfg80211.ieee80211_regdom` on the
   kernel command line). The app sends the phone's region before scanning,
   and the player saves it, so channels 12/13 and 5 GHz match the country.
   The status screen's Wi-Fi block shows the rfkill state, country,
   NetworkManager state, last scan count and the last join result. The HDMI status screen shows
   the real adapter address, Powered and rfkill state, the
   `RegisterAdvertisement` result, and the last error — not a static
   "advertising setup" placeholder. The Address line is the real `wlan0` /
   `eth0` IPv4 address, or `no network`; `docker0` is ignored. The FreeSounder
   app walks you through joining a network (Improv Wi-Fi). Ethernet still
   works if you plug a cable in.
7. Write the image, eject the media, and boot the Pi.

First boot on a 4 GB host needs **internet access** so Docker can pull
`ghcr.io/music-assistant/server`. Rooms only need the LAN to reach the host.
If the board has no network yet, open the app (demo mode is off by default)
and choose **Set up your first player** / **Add a player**, then join Wi-Fi
over Bluetooth; adoption continues once it appears on the network.

Prefer the FreeSounder app for everyday setup: **Add a player** to name a new
Pi and join it, and the gear on a room for bass, treble, balance, bass boost,
volume limit and DAC.

SSH is **off**. There is no bootfs `ssh` flag and the first-user wizard is
disabled. To open a shell for development, connect the app to the household
host, open the player’s settings, tap the version label seven times, and
enable SSH. The app shows a one-time password (or you can send a public key).
SSH turns itself off after a reboot or when you toggle it off. The household
control API has no authentication; only use developer SSH on a trusted LAN.

`freesounder-setup` remains on the image for recovery if you already have a
shell (status, doctor, role, host, DAC).

DAC+: `dtoverlay=hifiberry-dacplus` in `/boot/firmware/config.txt`. Official
DigiAMP+ uses the same overlay. A reboot is required after `set-dac`. Then
plug in powered speakers (DAC+) or passives (DigiAMP+; check PSU needs).

## What is installed

- Raspberry Pi OS Lite, 64-bit (Trixie), hostname `fs-new` until adopted (`fs-<room>` after), SSH disabled
- `snapclient`, ALSA utilities, Avahi. `select-audio-output` (run before
  snapclient starts and on HDMI/sound-card hotplug) points the `freesounder`
  ALSA chain at a DAC/HAT or USB card, else the HDMI port with a display
  connected (Pi 5: `vc4hdmi0`/`vc4hdmi1`), else the null sink, so the room
  always registers with Music Assistant. The choice is in
  `/etc/freesounder/audio-output`, on tty1 and in the doctor report.
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

No plaintext passwords, API tokens, or wireless secrets are stored in the
source tree. The published image includes a local `fsadmin` account whose
shadow hash is supplied to `build.sh` via `FSADMIN_PASSWORD_HASH` (or
`FSADMIN_PASSWORD_HASH_FILE`). SSH stays off until developer mode is enabled
in the app.

## Build it yourself

On a Debian-based amd64 or arm64 host with loop-device access, `qemu-user`
binfmt (for cross builds), and the packages listed in the pi-gen README
(`debootstrap`, `parted`, `quilt`, `kpartx`, `qemu-user-static`, `mtools`, …):

```bash
sudo FSADMIN_PASSWORD_HASH_FILE=/path/to/crypt-hash ./images/player/build.sh
```

The hash must be a crypt(3) string (`openssl passwd -6`). You can also pass
`FSADMIN_PASSWORD_FILE` (plaintext on the build host only; it is hashed and
not copied into the image or the repo).

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
- Music Assistant (pinned in `/etc/freesounder/music-assistant-image`, arm64)
  is preloaded into the image by `stage-freesounder/04-music-assistant`
  (`tools/fetch-docker-archive.py`, no Docker needed on the build host; cache
  with `MA_IMAGE_CACHE`). On the first host start `run-music-assistant` runs
  `docker load` once (then deletes the archive), waits for a LAN address, and
  starts MA with host networking on port 8095. Progress is written to
  `/run/freesounder/ma-status.json` and shown on tty1 and in the doctor file.
  It only pulls from the registry if the bundled archive is missing.
- Music Assistant schema ≥ 28 needs a token. On the household host the control
  API onboards the bundled MA itself: it creates the first admin with a random
  password (`/etc/freesounder/ma-admin.json`, root 0600, generated on the
  player, never in the image) and stores a token in `/etc/freesounder/ma-token`
  (0600). It also enables MA's Snapcast provider (built-in server). If MA was
  already set up by hand, set a token with `sudo freesounder-setup set-ma-token`.
- The control API never blocks on MA: `/health`, `/` and `/api/v1/system`
  report `ma_state` (connected, starting, auth_required, unavailable) with a
  reason, and MA-backed calls answer 503 with that state while it starts.
- Automatic host failover is **not** in the first beta; back up
  `/var/lib/freesounder/ma-data`.
- Unadopted boards share the hostname `fs-new` and can clash on mDNS if two
  sit on the same LAN. Adopt each one in the app so it becomes `fs-<room>`.
- Tone controls use ALSA LADSPA (`caps`). A CamillaDSP YAML file is written
  as well, but CamillaDSP is not bundled in this image. Changing DAC still
  needs a reboot. These paths have not been tested on real Pi hardware yet.
- QEMU system emulation of a Pi 4/5 is not a substitute for a real boot test.

## Bluetooth setup diagnostics

After a boot (even a failed or incomplete one), the boot partition (`bootfs`) should contain progress markers you can read on a computer:

- `freesounder-initramfs.txt` — kernel reached initramfs (before first-boot resize)
- `freesounder-booted.txt` — systemd mounted `/boot/firmware` (very early)
- `freesounder-sysinit-ok.txt` — `sysinit.target` completed
- `freesounder-doctor.txt` — Bluetooth / network diagnostics (written after sysinit and again one minute after boot)

The HDMI console is verbose (`console=tty1`, `loglevel=7`, no `quiet`). Plug in a micro-HDMI cable to read kernel messages if the green ACT LED stays solid. On the device itself, run `sudo freesounder-setup doctor` and `journalctl -u freesounder-ble-provisioning -b --no-pager`. The same doctor report is also written to `/boot/firmware/freesounder-doctor.txt` so you can read it from the boot partition on a PC.

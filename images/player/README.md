# Player image

Every freesounder room ships the **same** image. On a 4 GB board, the image can run the household-host profile (official Music Assistant Docker + built-in snapserver + control-api). On every board, including the host, it runs `snapclient`, ALSA to a local DAC, and the control-api / setup daemons.

## Profiles

| Profile | Boards | Processes |
|---|---|---|
| Host | Pi 4 4GB (default) or Pi 5 4GB | Docker, `ghcr.io/music-assistant/server`, built-in snapserver, snapclient, control-api |
| Member | Pi 4 2GB / 1GB, Zero 2 W, or a 4 GB board not elected host | snapclient, control-api (proxies to the host) |

The first public beta backs up and restores Music Assistant’s `/data` directory. There is no automatic failover in that beta.

## Image generator

This directory will hold config for one of:

- [rpi-image-gen](https://github.com/raspberrypi/rpi-image-gen) — current Raspberry Pi image generation tooling
- [pi-gen](https://github.com/RPi-Distro/pi-gen) — the older official generator, still widely used

A stub config lives in `rpi-image-gen/` (preferred) and `pi-gen/` (fallback notes). Neither image has been built in this repository yet.

## Planned first image contents

- Raspberry Pi OS Lite (64-bit) baseline
- Docker Engine (host profile starts Music Assistant with host networking and a `/data` volume; prefer USB SSD or NVMe for `/data` to limit SD wear)
- `snapclient` pointed at the household host
- `freesounder-control-api` (Python 3.13+, `music-assistant-client`)
- mDNS (`_freesounder._tcp`)
- Official DAC / I2S overlay as documented at image-build time

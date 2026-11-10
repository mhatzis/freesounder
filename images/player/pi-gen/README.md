# pi-gen fallback

If rpi-image-gen is not a good fit when we first build, we can use [pi-gen](https://github.com/RPi-Distro/pi-gen) with a custom stage that installs Docker, snapclient and the freesounder control-api.

No stage scripts have been written yet. Prefer `../rpi-image-gen` if it supports the Pi OS release we target.

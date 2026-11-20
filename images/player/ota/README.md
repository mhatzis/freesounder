# Player OTA: A/B slots with RAUC (phase 1)

Plan and decisions: [`docs/ota-plan.md`](../../../docs/ota-plan.md). This directory holds the
build tools. The on-player parts live in the pi-gen stage
(`../pi-gen/stage-freesounder/01-filesystem/files/...`).

## SD card layout (MBR, disk id `0x46534f54`, so the PARTUUIDs are the same on every player)

| Part | Size | Content |
|---|---|---|
| p1 | 64 MiB FAT16 | **bootsel**: `autoboot.txt`, `bootcode.bin` (Pi 3 / Zero 2 W), empty `config.txt` |
| p2 / p3 | 256 MiB FAT32 each | boot **A** / **B** (firmware, kernel, `config.txt`, `cmdline_a.txt`, `cmdline_b.txt`) |
| p4 | extended | holds p5–p7 |
| p5 / p6 | 4 GiB ext4 each | root **A** / **B** |
| p7 | rest of the card, ext4 `fsdata` | `/data`. Created on the first boot by `freesounder-data.service`. Survives updates. |

The image file stops at the start of p6 (~4.9 GB), so Imager writes less. A 16 GB card is the minimum.

## Boot selection (Raspberry Pi `tryboot`)

- `autoboot.txt` on p1 contains `[all] tryboot_a_b=1, boot_partition=<default>` and
  `[tryboot] boot_partition=<other>`. Both variants are the same length, so a commit is a single
  in-place write.
- Each boot slot's `config.txt` contains `[boot_partition=2] cmdline=cmdline_a.txt` and
  `[boot_partition=3] cmdline=cmdline_b.txt`. The same `boot.vfat` therefore works in either slot.
  The command line carries `root=PARTUUID=…-05|06 rauc.slot=A|B fsboot=PARTUUID=…-02|03`.
- An update installs into the *other* slot and reboots once with `reboot "0 tryboot"`. The tryboot
  flag only lasts for one boot. A hang (14 s hardware watchdog), a power cut, or a failed health
  check followed by a normal reboot all lead back to the old slot.
- `freesounder-ota health` (`freesounder-ota-health.service`, up to 5 min) checks that:
  - `/data` is mounted and writable;
  - systemd reached `running` or `degraded`;
  - freesounderd answers `/health`;
  - the network is up (only if the update was started over the network).

  If all checks pass, `rauc status mark-good` commits the update: autoboot.txt is rewritten so the
  new slot becomes the default. If a check fails, the slot is marked bad and the player reboots
  normally into the old slot.
- `freesounder-ota-boot.service` runs `boot-check` early on every boot. If the player is back on
  the default slot while a trial was open, the trial slot is marked bad and `last_result` is set
  to `rolled_back`. The app will read and show this later.
- RAUC uses `bootloader=custom`, with `rauc-backend` (fsota.py) handling get/set-primary, get/set-state
  and get-current.
- State is stored in `/data/ota/state.json`, plus RAUC's `/data/ota/rauc.status`.

## What survives an update

Persistent state lives on `/data` and is mounted early by `freesounder-data.service`
(`/usr/lib/freesounder/ota/data-setup`):

- **Bind mounts** from `/data/state` for `/var/lib/{freesounder,bluetooth,NetworkManager,docker,snapserver}`
  and `/etc/NetworkManager/system-connections`. Each one is seeded from the image the first time.
- **Overlay** for `/etc/freesounder`: image defaults stay visible, and your changes win.

RAUC's post-install handler copies the player's identity into the new slot:

- `/etc/machine-id` and the SSH host keys go to the new root;
- the DAC/amp `dtoverlay` block goes into the new `config.txt`.

## Tools

| Script | What it does |
|---|---|
| `gen-dev-keys.sh` | Creates a dev CA and a codeSigning cert in `~/.config/freesounder-build/ota-dev-keys/` (keys are mode 0600 and never printed). |
| `make-ab-image.sh IMG OUT [VER]` | Converts the pi-gen image into the A/B card image, `rootfs.ext4` and `boot.vfat`. `build.sh` runs it automatically (`FS_AB_IMAGE=0` turns this off). |
| `build-bundle.sh --images OUT --version V --out F.raucb` | Builds a signed RAUC bundle (verity + adaptive `block-hash-index` by default) and verifies it the way a player does. |
| `simulate.sh AB-DIR PLAIN.raucb` | Runs the full install → trial → commit / rollback flow on loop devices (see below). Needs root. |
| `tests/` | pytest suite: `uv run --no-project --with pytest python -m pytest -q images/player/ota/tests` |

Keys:

- `build.sh` needs `RAUC_KEYRING_PEM`, the **public** CA certificate that is baked into the image.
  If it is not set, `build.sh` falls back to the dev CA.
- In CI the signing key and cert come from the GitHub secrets `RAUC_SIGNING_KEY_PEM` and
  `RAUC_SIGNING_CERT_PEM`. They are written to 0600 temp files and passed with `--key` and `--cert`.
- The production CA private key stays offline. It is used only to issue or renew the signing cert.

## Verified on the build box (no Pi hardware)

- `tests/` passes (24 tests):
  - autoboot rendering and a model of the firmware's selection rules;
  - the backend state machine: install, commit, failed health → rollback, crash during trial →
    rollback, power cut before the trial, manual rollback, three updates in a row;
  - the health retry and timeout loop;
  - data-setup seeding, bind mounts and overlay, and that state and user changes survive a new root;
  - the partition table checks: alignment, no overlaps, and first-boot p7 growth on a 16 GB card.
- `simulate.sh` passes (48 checks). It used a 0.6.16 image converted with `make-ab-image.sh` and a
  real signed RAUC 1.13 bundle, installed through `rauc service` over D-Bus onto loop devices:
  - p7 is created after root B. This caught a real bug: `sfdisk --append` put p7 into the EBR gap.
  - RAUC writes boot B and root B.
  - post-install runs fsck and resize on root B, and copies machine-id, SSH keys and the DAC block.
  - The trial boot is selected by the firmware model, and health pass commits through
    `rauc status mark-good`.
  - A failed health check rolls back automatically.
  - A hang or power cut during the trial rolls back.
  - Manual rollback works.
  - Unsigned or foreign-CA bundles and bundles for another `compatible` are refused.
- Bundle size for 0.6.16: 756 MB plain / 780 MB verity (xz squashfs). Build time is about 1.5 min.

## Needs real hardware (Pi 3, Zero 2 W, Pi 4, Pi 5 with 16 GB cards)

- `autoboot.txt`/`tryboot_a_b` on each model, and the `[boot_partition=N]` cmdline filter.
  The Pi 3 and Zero 2 W boot ROMs read `bootcode.bin` from p1. Pi 4 and Pi 5 need an EEPROM recent
  enough for tryboot.
- `reboot "0 tryboot"` sets the flag. The 14 s hardware watchdog resets a hung trial boot.
- First-boot creation of p7 on the live card (`sfdisk` + `partx` on a mounted disk).
- The vfat in-place `autoboot.txt` write. The box has no vfat kernel driver, so the simulation uses
  mtools.
- dm-verity bundles and HTTP streaming (nbd). The box kernel has no device-mapper, so the
  simulation installs a *plain* bundle. The verity bundle was only built and verified.
- Install time and RAM use on the Zero 2 W during playback.

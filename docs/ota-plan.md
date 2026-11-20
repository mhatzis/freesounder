# FreeSounder OTA update plan (players + app)

Status: **approved 2026-11-20** (decisions in section 0). Phase 1 in progress on branch `feat/ota`. Based on the 0.6.16 image (pi-gen, arm64 Raspberry Pi OS Lite).
The current image is 3.6 GB raw and about 650 MB as .img.xz. It has the usual boot + root layout and runs on
the Pi Zero 2 W, 3, 4 and 5 from SD cards. There is no server: releases go out on GitHub Releases.

## 0. Decisions (approved by the user, 2026-11-20)

| Topic | Decision |
|---|---|
| Re-flash | One last re-flash of every player into the new A/B layout is fine. |
| SD card | **16 GB minimum.** |
| Install policy | **Always ask in the app before updating.** No automatic installs. Players only check (fetch the signed manifest) and report "Update available"; nothing downloads or installs until the user taps Update (per household, rolling). |
| Signing key | Kept as a **GitHub Actions secret**. Dev/test keys are generated locally (never printed, never committed); only the public CA cert ships in the image keyring. |
| Stopgap | **No freesounderd-only bridge.** Existing (single-slot) players get OTA only after the one-time re-flash. |
| Tool | **RAUC A/B** with the Raspberry Pi `tryboot` + `autoboot.txt` partition switch (custom bootloader backend). |

## 1. What has to be updatable

| Layer | Examples | How often |
|---|---|---|
| FreeSounder app code | freesounderd, fsdsp, wheels | Every release |
| Bundled binaries | go-librespot, shairport-sync, snapcast, bluez-alsa | Some releases |
| OS userland + config | Debian security fixes, systemd units, ALSA/BlueZ config | Monthly-ish |
| Kernel + firmware | `kernel8.img`, `start*.elf`, overlays, device tree | A few times a year |
| Pi 4/5 bootloader EEPROM | rpi-eeprom | Rarely; leave to `rpi-eeprom-update` |

Bluetooth, audio and Wi-Fi regressions in 0.6.x came from changes at every layer. So a fix-everything
update has to cover the whole image, not just freesounderd.

## 2. Player options compared

### A. A/B root filesystem, full-image updates
Two copies of boot and root (A and B). The update writes the inactive slot, test-boots it once with the
Pi firmware's `tryboot` flag, and commits only if a health check passes. Otherwise the next reboot goes back
to the old slot. `autoboot.txt` with `tryboot_a_b=1` and `[tryboot] boot_partition=3` works on every model
we support:
- Pi 3 and Zero 2 W: `bootcode.bin` must stay on partition 1 and is not redundant. It almost never changes.
- Pi 5: each slot needs a `config.txt`.
- Pi 4 rev 1.0/1.1: the EEPROM must not be write-protected.

Tooling choices:

| Tool | Fit | Notes |
|---|---|---|
| **RAUC** (Debian package `rauc`) | **Best fit** | X.509/CMS-signed bundles, slot status, HTTP streaming install, adaptive (block-hash) downloads. Its `custom` bootloader backend is a small script that drives `autoboot.txt`/tryboot, so no U-Boot is needed. Install hooks handle data migration. Small C daemon with low RAM use. |
| SWUpdate | Good, more DIY | Very flexible, signed `.swu`, streaming, Lua handlers. Its bootloader integration expects U-Boot/GRUB/EFI env, so we would write the tryboot glue ourselves. Has more knobs than we need. |
| Mender | Poor fit | Built around the Mender server (hosted is paid, self-hosted is a server we don't want). Standalone mode loses rollout management. Pi integration normally goes through U-Boot. Heavier client. |
| Raspberry Pi tryboot by hand (own script + `dd`/`bmaptool`) | Possible | No dependency, but we would rebuild signing, streaming, slot bookkeeping and hooks that RAUC already has. Raspberry Pi's own A/B Boot Update needs Raspberry Pi Connect (cloud account), which doesn't fit "no server". |

Pros:
- Updates are atomic and survive power loss: the running slot is never touched.
- Rollback is automatic (tryboot) and manual ("go back to previous version").
- It updates everything, kernel and firmware included.
- Every player runs a byte-identical, tested image, which makes support much easier.
- It fits a locked-down appliance: the root can be read-only, which is kinder to SD cards.

Cons:
- It needs a new partition layout, so every existing player must be re-flashed once.
- Downloads are big: about 600–700 MB per full bundle. Adaptive/delta mode cuts later downloads to the
  changed blocks, often tens of MB to 200 MB, but you have to plan for the worst case.
- Each update needs a reboot. Music on that player stops for about 30–60 s.
- Everything we persist (household, keys, Wi-Fi, Bluetooth pairings, Spotify credentials, library index,
  settings) must move to a separate data partition. This is a one-off but real job.

SD/space cost (rough):
- Layout: p1 boot-select FAT 32 MB + p2/p3 boot A/B 256 MB each + p5/p6 root A/B ~3.5 GB each + p7 data
  (the rest of the card).
- That is about 7.5 GB before data, so **16 GB minimum SD** (8 GB only if root is trimmed to ~2.5 GB).
- Streaming install writes straight into the inactive slot, so no extra download space is needed.

Zero 2 W (512 MB RAM):
- Must stream; never download a bundle into tmpfs/RAM.
- RAUC streaming uses an NBD helper with small buffers, and verity checks one block at a time. Expect tens of MB.
- Run it with `nice`/`ionice` so audio keeps going during the install.
- The SD write takes about 3–8 minutes on a Zero 2 W. Only the final reboot interrupts playback.

### B. Our own apt repository of .debs
Package freesounderd and the bundled binaries as .debs, host a signed apt repo (aptly/reprepro) on GitHub
Pages or as Release assets, and run `apt upgrade` on the player.

Pros:
- Small downloads (MBs) and no re-flash.
- Familiar tooling, and Debian security updates come along for free.

Cons:
- Not atomic: a power cut or full card during `dpkg` can leave a broken player.
- No real rollback. Downgrades are fragile, and kernel/firmware updates have no safety net.
- Players slowly drift apart (different package sets), so a "known good image" no longer exists.
- dpkg writes a lot to the SD card, and the root can't be read-only.
- Running apt on a Zero 2 W with 512 MB is slow and memory-hungry (package lists alone take ~100+ MB of RAM).
- Space: small, though apt caches and lists grow by hundreds of MB.

### C. Update freesounderd only
Install the signed app tarball (code + wheels, ~10–40 MB) into `/opt/freesounder/releases/<ver>/`, switch a
`current` symlink, and restart the service.

Pros:
- Tiny and fast: about a 5–10 s service restart, no reboot.
- Instant rollback: switch the symlink back. Works on today's layout with no re-flash.
- Fine on 512 MB of RAM.

Cons:
- Can't fix the OS, kernel, firmware, BlueZ/ALSA config, go-librespot or shairport. Much of the 0.6.x
  Bluetooth and audio work was at exactly those layers.
- The Python ABI and system libraries are pinned to whatever the image shipped.
- Space: ~50–100 MB for two or three kept versions.

## 3. Recommendation

**RAUC A/B full-image updates driven by tryboot, with a separate data partition (read-only root later).**
No freesounderd-only bridge and no apt repo (decided). Single-slot players show "Re-flash needed for
updates" in the app once the app knows about OTA.

### Signing and verification
- **Bundles:** RAUC bundles are signed with a FreeSounder release key (X.509, ECDSA P-256). The CA cert is
  baked into the image (`/etc/rauc/keyring.pem`). The `verity` bundle format means each block is checked as
  it streams.
- **Manifest:** signed separately with an Ed25519 key (minisign/signify style), with the public key in the image.
- **Key custody (decided):** the release signing key and cert live only as GitHub Actions secrets
  (`RAUC_SIGNING_KEY_PEM`, `RAUC_SIGNING_CERT_PEM`; later `OTA_MANIFEST_KEY` for the manifest). Dev/test keys are
  generated locally by `images/player/ota/gen-dev-keys.sh` into `~/.config/freesounder-build/ota-dev-keys/`.
  - Add a second backup key to the keyring from day one so a key can be rotated without a re-flash.
  - Never log or print the keys. Treat them like `ASC_API_KEY_P8`.
- **Anti-rollback:** the manifest carries `version` and `min_version`. A player refuses a bundle older than
  `min_version`, which blocks downgrades to known-bad releases. A user-requested rollback to the previous
  slot is still allowed.

### Hosting (no server)
- **Bundles:** GitHub Release assets, e.g. `freesounder-0.7.0-arm64.raucb`.
  - Direct download URLs aren't subject to the 60/hour API limit.
  - GitHub serves Range requests, which RAUC streaming needs. Verify this in phase 1 because downloads redirect
    to `objects.githubusercontent.com`.
- **Manifests:** one per channel, at stable URLs on GitHub Pages (or a fixed `ota-manifests` release that is
  edited, never re-tagged): `stable.json`, `beta.json`, `dev.json` plus a `.sig` for each.
  - Each manifest holds version, bundle URL(s), sha256, size, `min_version`, `min_app_version`,
    `api_version`, release notes (short text) and a `rollout_percent`.
- **Checks:** players fetch the manifest about every 6 h, with jitter. Only the elected household leader needs
  to fetch; it shares the result with its peers over the LAN.

### Channels
- **stable** (default): what the user releases on purpose.
- **beta:** TestFlight-like, opt-in per household in Settings.
- **dev:** hidden behind developer mode; every CI build.
- The channel is per household, so all rooms stay on the same version.

### App UX: "Update available"
- freesounderd adds:
  - `GET /api/v2/update`: current/available version, channel, notes, state, progress, last error;
  - `POST /api/v2/update/check`;
  - `POST /api/v2/update/start {"when": "now"|"tonight", "players": [...]}`;
  - `POST /api/v2/update/rollback`;
  - `PATCH` channel and auto-update settings.
- A banner on Home ("FreeSounder 0.7.0 is available · What's new · Update") and a **Settings → Software
  update** page.
  - The page shows each room's version and status: Up to date / Downloading 42% / Installing / Restarting /
    Rolled back.
- Policy (decided): **the app always asks.** A check only shows "Update available" and the release notes. Nothing
  downloads or installs until the user taps **Update** (or **Update tonight**, which the user also has to choose).

### Staggered updates across a group
- The household leader runs a rolling update, one player at a time, so music never stops everywhere at once.
  1. Pick players that are idle first. Wait for a playing player to go idle unless the user said "now"
     (or picked "tonight").
  2. For "now" in a playing group, do grouped followers one at a time. Snapcast keeps the others playing, and
     the rebooted room rejoins automatically.
  3. Update the player that hosts the group's stream (Snapserver/leader) last, and only when it's idle,
     because its reboot pauses the whole group for ~45 s.
- Each player commits and reports healthy before the next one starts. If any player rolls back, the rollout
  stops for the household and the app shows why.
- Mixed versions are temporary, so the peer protocol must accept **N-1** for at least one release. Add a
  `peer_api` version to the peer handshake.

### Health check and auto-rollback
- After the tryboot boot, a `freesounder-update-commit` service waits up to 5 minutes for all of these:
  - freesounderd `/health` is OK;
  - the data partition is mounted and readable;
  - the network is up (Wi-Fi associated or Ethernet);
  - the audio output opens: a silent test write through the selected PCM, Bluetooth included if one is
    configured;
  - the snapclient starts;
  - the household peers can see the player.
- On success, `rauc status mark-good` makes this slot the default in `autoboot.txt`.
- On failure, a plain reboot goes back to the old slot, because tryboot is one-shot. The reason is recorded on
  the data partition and reported to the app.
- Enable the hardware watchdog (`bcm2835_wdt`, systemd `RuntimeWatchdogSec`) so a hung kernel also falls back.
- The app keeps a manual "Go back to previous version" while the old slot is intact.

### Migration for existing players (one last re-flash)
1. No bridge updater (decided). The app explains the one-time re-flash; existing pairing and adopt already let
   a re-flashed player rejoin the household.
2. Ship `0.7.0-ab` as a new image with the A/B + data layout, flashed with Raspberry Pi Imager as today. First
   boot puts the persistent state on `/data`. The app guides "Re-flash this player"; the household re-adopts
   it and restores its settings.
3. In-place repartitioning of a running SD card is not recommended: it's risky on power loss and differs by
   card size. Players that aren't re-flashed keep working but get no updates; the app marks them.

## 4. App side
- **Binaries:** App Store/TestFlight (iOS) and Play/internal testing (Android) handle app binaries; we don't
  OTA the app.
- **Compatibility contract:**
  - Each player reports `api_version` (integer, bumped on breaking API changes) plus `min_app_version`.
  - Each app build declares `kMinPlayerApi`/`kMaxPlayerApi`.
- **In-app prompts:**
  - Player too old for the app: "Update your players" (starts the player update).
  - App too old for the players: "Update the FreeSounder app" (App Store/Play/TestFlight link). Features that
    need the newer API are hidden gracefully instead of failing.
  - Mixed household: warn and offer to update the remaining rooms.
- **Order:** release the app first (tolerant of old and new API), then the players. Keep the server API
  backward compatible for at least one app version.

## 5. Phases and rough effort (one developer)

| Phase | Work | Effort |
|---|---|---|
| 0 | Decisions (below), create signing keys + backup key, manifest schema | 1–2 days |
| 1 | pi-gen: A/B + data partition layout, read-only root, move persistent state to `/data`, RAUC + tryboot `custom` backend, watchdog; prove on Zero 2 W, 3, 4 and 5 (incl. Pi 5 `config.txt`, Pi 3 `bootcode.bin`) | 2–3 weeks |
| 2 | CI: build `.raucb` (verity, adaptive), sign, upload to Release, publish signed channel manifests; verify GitHub Range/streaming | 3–5 days |
| 3 | freesounderd: update API, manifest fetch/verify, household-leader rolling orchestration, health-check commit service, N-1 peer compatibility | 1.5–2 weeks |
| 4 | App: Software update page, banner, progress, channel and auto-update settings, `api_version` compatibility checks and prompts | 1 week |
| 5 | Test matrix: power-cut during install, bad bundle, bad signature, failed health check → rollback, Zero 2 W RAM/IO during playback, mixed-version groups | 1 week |

Total: **about 5–7 weeks** (no bridge).

## 6. Decisions
All taken. See section 0. Still open (smaller, can wait for phase 3/4):
1. Which channels users can see (proposal: stable + beta in Settings, dev only in developer mode).
2. Read-only root (proposal: yes, phase 1b, after the data partition has proven itself on hardware).
3. Manifest hosting: GitHub Pages vs a fixed `ota-manifests` release (proposal: Pages).

## 7. Phase 1 status
See `images/player/ota/README.md` on branch `feat/ota` for what is implemented and what was verified.

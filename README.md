# FreeSounder

<p align="center">
  <img src="assets/freesounder-icon.png" width="128" height="128" alt="FreeSounder icon">
</p>

**FreeSounder** is a free, open-source multi-room wireless audio system that aims to feel as simple and polished as commercial whole-home speaker systems, without the lock-in.


> Status: early implementation. Architecture is locked as **player-only**: there is no cupboard server. Music Assistant runs on an elected 4 GB household-host player; the Flutter app talks to a control API on any player, which proxies to that host.

## Architecture in brief

- Every room ships the same player image. **One** 4 GB player at a time is the household host (default hardware: **Raspberry Pi 4 4GB**).
- The host runs official Music Assistant Docker and MA’s built-in snapserver. Every room, including the host, runs `snapclient`.
- The Flutter app discovers any player via mDNS (`_freesounder._tcp`) and uses the [control API](services/control-api) facade.
- First public beta uses **backup and restore** of Music Assistant’s `/data` (no automatic failover).
- Spotify: **both** Music Assistant Soloist and go-librespot Connect, user-selectable, with disclosures in the app.

Details, hardware prices and sources: [docs/architecture.md](docs/architecture.md). Beta and distribution notes: [docs/development.md](docs/development.md). How to contribute: [CONTRIBUTING.md](CONTRIBUTING.md).

Design research Page: [freesounder – Architecture Research & Recommended Stack](https://cursor.com/canvas/store-1e1f00a9-5a68-4935-844e-05e60d7c3684/36256424-0c34-4af8-91ca-18271424bb71).

## Repository layout

| Path | Role |
|---|---|
| `apps/flutter` | iOS, Android and web controller |
| `services/control-api` | Python FastAPI facade over Music Assistant |
| `services/setup` | First-boot / household pairing (stub) |
| `images/player` | Raspberry Pi image plan (pi-gen / rpi-image-gen) |
| `dev/` | Docker Compose: Music Assistant + control-api + two snapclients |
| `docs/` | Architecture and development |

## Licence

FreeSounder is licensed under the [Apache License 2.0](LICENSE). Some third-party components it may build on use other licences (for example GPL-3.0 Snapcast); those run as separate programs and keep their own licences.

"Sonos" is a trademark of Sonos, Inc. FreeSounder is an independent project and is not affiliated with or endorsed by Sonos.
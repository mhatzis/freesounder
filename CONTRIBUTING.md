# Contributing to freesounder

freesounder is licensed under the [Apache License 2.0](LICENSE). First-party code stays Apache-2.0. Do not vendor or statically link GPL sources (Snapcast, go-librespot, and so on); those run as unmodified separate processes.

## Repository layout

- `apps/flutter` — iOS, Android and web controller
- `services/control-api` — FastAPI facade over Music Assistant on the household host
- `services/setup` — first-boot and household pairing (stub)
- `images/player` — Raspberry Pi player image plan
- `dev/` — Docker Compose for local Music Assistant + two simulated rooms
- `docs/` — architecture and development notes

## Development

Python **3.13+** is required for `services/control-api` because the official [`music-assistant-client`](https://pypi.org/project/music-assistant-client/) package requires it (verified 1.8.0 on 10 November 2026).

```bash
cd services/control-api
uv sync
uv run ruff check src tests
uv run pytest
```

```bash
cd apps/flutter
flutter pub get
flutter analyze
flutter test
flutter build web
```

Local stack (needs Docker): see [dev/README.md](dev/README.md).

Please keep the Flutter rooms-and-playback store as the single source of truth for rooms, groups, volume and now-playing. Prefer a mocked Music Assistant client in unit tests rather than a live server.

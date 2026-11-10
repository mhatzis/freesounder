# services/player-agent

Small HTTP service that runs on every FreeSounder Pi (host and room).
The Flutter app does not talk to it directly: the household-host
[control API](../control-api) discovers agents over mDNS
(`_freesounder-player._tcp`) and proxies adopt and settings calls.

## Endpoints

- `GET /health`
- `GET /api/v1/agent` — id, name, role, `state` (`unconfigured` / `configured`), host, DAC
- `POST /api/v1/agent/adopt` — `{ "name": "Kitchen", "host": "192.168.1.10", "role": "room" }`
- `GET /api/v1/settings`
- `PATCH /api/v1/settings` — name, volume_limit, bass, treble, balance, loudness, dac

Settings are stored in `/var/lib/freesounder/settings.json`. Tone controls are
applied with an ALSA LADSPA equaliser (Debian `caps` Eq10X2) in front of
`snapclient` output. A CamillaDSP config is also written for boards that have
the binary installed.

## Run

```bash
uv sync --group dev
uv run freesounder-player-agent
```

In tests, point `FREESOUNDER_CONFIG_DIR` and `FREESOUNDER_STATE_DIR` at a temp
directory and set `FREESOUNDER_APPLY_COMMAND=` so apply hooks are skipped.

```bash
uv run ruff check src tests
uv run pytest
```

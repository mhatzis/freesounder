# services/control-api

Python 3.13+ FastAPI facade over Music Assistant, using the official
[`music-assistant-client`](https://pypi.org/project/music-assistant-client/) package
(1.8.0 when this was written; requires Python ≥ 3.13, Apache-2.0).

The household **host** advertises this API as `_freesounder._tcp`. The Flutter
app talks only to this surface — never to Music Assistant, Snapcast, or a
player agent directly. Catalogue, queue, grouping and transport calls are
proxied to Music Assistant. Adopting a new Pi and per-player tone settings are
proxied to the [player agent](../player-agent) on that board
(`_freesounder-player._tcp`).

## Run

```bash
uv sync --group dev
export FREESOUNDER_MA_URL=http://127.0.0.1:8095
# Optional, required for Music Assistant schema >= 28:
# export FREESOUNDER_MA_TOKEN=...
uv run freesounder-control-api
```

Set `FREESOUNDER_MOCK_MA=true` to serve in-memory rooms with no Music Assistant.

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `FREESOUNDER_HOST` | `0.0.0.0` | Bind address |
| `FREESOUNDER_PORT` | `8080` | HTTP port |
| `FREESOUNDER_MA_URL` | `http://127.0.0.1:8095` | Music Assistant base URL |
| `FREESOUNDER_MA_TOKEN` | empty | Long-lived MA token |
| `FREESOUNDER_MDNS_ENABLED` | `true` | Advertise `_freesounder._tcp` |
| `FREESOUNDER_MDNS_NAME` | `freesounder` | Instance name |
| `FREESOUNDER_MOCK_MA` | `false` | In-memory backend |
| `FREESOUNDER_GROUP_PROVIDER` | `snapcast` | Provider domain for `create_group_player` |

## HTTP API

- `GET /health`
- `GET /api/v1/rooms`
- `GET /api/v1/groups`
- `POST /api/v1/groups` — create a MA group player / sync group
- `POST /api/v1/groups/{group_id}/join` — `{ "player_id": "..." }`
- `POST /api/v1/groups/{group_id}/leave` — `{ "player_id": "..." }`
- `GET /api/v1/now-playing?player_id=`
- `POST /api/v1/players/{player_id}/play`
- `POST /api/v1/players/{player_id}/pause`
- `POST /api/v1/players/{player_id}/next`
- `POST /api/v1/players/{player_id}/volume` — `{ "level": 0-100 }`
- `POST /api/v1/groups/{group_id}/volume` — group master volume
- `GET /api/v1/search?q=`
- `GET /api/v1/browse?path=`
- `GET /api/v1/system` — household name, version, mock flag
- `GET /api/v1/players/unclaimed` — new Pis advertising `_freesounder-player._tcp`
- `POST /api/v1/players/adopt` — `{ "id": "...", "name": "Kitchen" }` or `{ "url": "http://...", "name": "Kitchen" }`
- `GET /api/v1/players/{player_id}/settings`
- `PATCH /api/v1/players/{player_id}/settings` — name, volume_limit (1–100), bass/treble/balance (−10..+10 dB), loudness, dac (`none` / `dacplus` / `digiampplus`)
- `WebSocket /api/v1/ws` — `rooms`, `now_playing`, `health` events

Grouping is implemented with Music Assistant sync-group commands (`players.group`,
`ungroup`, `create_group_player`, `group_volume`), not a second grouping model.

`POST /api/v1/players/{id}/volume` is capped at that player's `volume_limit`.
Tone controls are applied on the player by the agent (ALSA LADSPA Eq10X2), not
in Music Assistant.

## Tests

```bash
uv run ruff check src tests
uv run pytest
```

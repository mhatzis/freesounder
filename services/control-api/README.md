# services/control-api

Python 3.13+ FastAPI facade over Music Assistant, using the official
[`music-assistant-client`](https://pypi.org/project/music-assistant-client/) package
(1.8.0 when this was written; requires Python ≥ 3.13, Apache-2.0).

The API is meant to run on every player. Catalogue, queue, grouping and transport
calls are proxied to the household-host Music Assistant instance. Setup and
discovery stay local.

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
- `WebSocket /api/v1/ws` — `rooms`, `now_playing`, `health` events

Grouping is implemented with Music Assistant sync-group commands (`players.group`,
`ungroup`, `create_group_player`, `group_volume`), not a second grouping model.

## Tests

```bash
uv run ruff check src tests
uv run pytest
```

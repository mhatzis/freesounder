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
- `GET /api/v1/players/{player_id}/developer/ssh` — ephemeral developer SSH status
- `POST /api/v1/players/{player_id}/developer/ssh` — `{ "enabled": true, "confirm": true, "public_key": "ssh-ed25519 …" }`. Proxies to an adopted player's agent. `confirm` must be true to enable. SSH turns off after reboot. **The API has no authentication**; anyone on the LAN who can reach the household host can call this.

- `GET /api/v1/music-services/catalog` — curated service types (Spotify, Tidal, Apple Music, Deezer, YouTube Music, Qobuz, SoundCloud, TuneIn, Radio Browser, Plex, Jellyfin, OpenSubsonic, local/SMB/NFS) plus configured instances. Marks which Music Assistant actually supports, and states limits honestly (Apple Music is experimental).
- `GET /api/v1/music-services/types` — curated types only
- `GET /api/v1/music-services` — configured providers, status `loaded` / `loading` / `needs_reauth` / `error` / `disabled`. Secrets are never returned.
- `GET /api/v1/music-services/{instance_id}` — detail and a redacted form schema
- `POST /api/v1/music-services` — `{ "domain": "qobuz", "values": {…} }`. Starts a setup or auth session if values are omitted.
- `PATCH /api/v1/music-services/{instance_id}` — update options. Omitted secrets are left unchanged.
- `DELETE /api/v1/music-services/{instance_id}`
- `POST /api/v1/music-services/auth` — `{ "domain": "spotify" }` or `{ "instance_id": "…" }`. Starts Music Assistant setup or reconfigure. For OAuth, returns `auth_url` + `session_id` + `callback_scheme` (`freesounder`).
- `GET /api/v1/music-services/auth/{session_id}` — poll status
- `POST /api/v1/music-services/auth/{session_id}/submit` — submit a credential form step
- `POST /api/v1/music-services/auth/{session_id}/abort`
- `GET|POST /api/v1/music-services/auth/{session_id}/callback` — redirects to `freesounder://music-services/auth?session_id=&status=` so an in-app browser can close
- `GET /api/v1/music-services/spotify-connect` — go-librespot / Soloist plugin status and per-room toggles, if the plugin is present
- `POST /api/v1/music-services/spotify-connect/{player_id}` — `{ "enabled": true }`
- `WebSocket /api/v1/ws` — `rooms`, `now_playing`, `health`, `music_services`, `music_service_auth` events

The Flutter app talks only to this API, never to Music Assistant on port 8095. OAuth (Spotify, Tidal, Apple Music, …) is driven as: the control API starts `config/providers/setup` or `reconfigure`, returns the Music Assistant authorize URL, the app opens it in ASWebAuthenticationSession / an in-app browser, Music Assistant receives the provider callback, and the control API reports success over the WebSocket (the app also polls the session). Credential providers render a form from Music Assistant's config entries (`string`, `secure_string`, `boolean`, `integer`, `dropdown`, `label`).


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

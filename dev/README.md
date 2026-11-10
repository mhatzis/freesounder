# Local development stack

This Compose file runs:

1. Official Music Assistant — `ghcr.io/music-assistant/server`
2. The freesounder [control-api](../services/control-api)
3. Two `snapclient` containers (`kitchen`, `lounge`) that write audio to a **file** under `/audio` (no speakers)

```bash
cd dev
cp -n .env.example .env
docker compose up --build
```

Then point the Flutter app at `http://127.0.0.1:8080` (disable mock mode) or open `http://127.0.0.1:8095` for the Music Assistant UI to add providers and a long-lived token (`FREESOUNDER_MA_TOKEN` in `.env`).

Without Docker, run the API alone:

```bash
cd ../services/control-api
FREESOUNDER_MOCK_MA=true FREESOUNDER_MDNS_ENABLED=false uv run freesounder-control-api
```

The Flutter app also has an in-app mock data mode so the rooms panel can be exercised with no hardware and no Compose stack.

## Notes

- Official MA install docs prefer **host networking** and a `/data` volume on a real player. Bridge networking is used here so containers can resolve each other by name.
- First beta backs up and restores MA `/data` (the `ma-data` volume). There is no automatic failover in this stack.
- `docker compose config` can be used to validate the file when Docker is installed.

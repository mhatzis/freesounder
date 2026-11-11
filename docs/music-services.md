# Music services in the FreeSounder app

As of **11 November 2026, Australia/Melbourne**. The in-app **Music services** screen (app 0.5.0) is the supported way to add and remove music sources. Users should not need the Music Assistant web UI at port 8095.

The Flutter app talks only to the household **control API**. The control API is a facade over Music Assistant's WebSocket commands (`config/providers/setup`, `reconfigure`, `config/flows/submit`, `get_entries`, `save`, `remove`, and `SETUP_FLOW_UPDATED` events).

## User flow

1. Open **Settings → Music services**.
2. Connected services appear at the top, with a status (connected, needs sign-in, error) and the account name when Music Assistant exposes one.
3. **Add a service** is a grid of curated sources. Tapping one either:
   - opens a system in-app browser for OAuth (Spotify, Tidal, Apple Music, Deezer), or
   - shows a form generated from Music Assistant's config schema (Qobuz, YouTube Music, Plex, Jellyfin, OpenSubsonic, SMB, local folders), or
   - enables the service immediately if no account is needed (Radio Browser, often TuneIn).
4. On a service's detail screen you can re-authenticate or remove it, with a confirmation.
5. If the Spotify Connect plugin is present, each room can be shown or hidden as a speaker in the official Spotify app. The engine (go-librespot or Spotify Soloist) is reported when known.

Demo mode ships with Spotify already connected and Tidal available to add. No live Music Assistant is required for that preview.

## What works fully vs what is limited

These judgments follow Music Assistant's own documentation. This work was **not tested against a live Music Assistant server with real accounts**.

| Service | Typical sign-in | Notes |
|---|---|---|
| Spotify | OAuth | In-app browse needs a configured Spotify music provider. Connect playback is a separate plugin (see below). Premium is required for Connect and for in-app playback through Music Assistant. |
| Tidal | OAuth, sometimes a follow-up URL | Tidal's redirect can land on a "page not found" URL that Music Assistant still needs in order to finish PKCE. The setup form will ask for that URL when the flow requires it. |
| Apple Music | OAuth | **Limited / experimental.** Playback is not officially supported by Apple. Maximum quality is AAC 256 kbps; lossless and Atmos are not available. Tokens expire after about 180 days. Gaps of up to five seconds can occur between tracks. The app shows this warning before you connect. |
| Deezer | OAuth | Supported when present on the server. |
| YouTube Music, Qobuz, SoundCloud | Credentials | Dynamic form; passwords are never echoed back. |
| TuneIn, Radio Browser | Often none | Added without an account when Music Assistant does not need one. |
| Plex, Jellyfin, OpenSubsonic | Server URL and credentials | Dynamic form. |
| Local folder, SMB share | Path / credentials | Multi-instance is allowed. |
| NFS share | Path | Listed for completeness. Current Music Assistant builds may not ship an NFS provider; the app marks it unavailable and suggests SMB or a local folder. |

**Spotify Connect** is the `spotify_connect` plugin, not the Spotify *music* provider. Per-room toggles are written to the plugin's `connected_players` list when the plugin is already set up. go-librespot has known limits for some accounts created after December 2024; Soloist is Music Assistant's official alternative and needs a personal API key.

## Endpoints

See [services/control-api/README.md](../services/control-api/README.md). WebSocket events `music_services` and `music_service_auth` report list changes and auth completion.

## Known gaps

- Not tested against a live Music Assistant server or real streaming accounts.
- Some providers use multi-step or device-code flows (QR, paste-a-URL). The app renders whatever form or external URL Music Assistant emits, but unusual steps may still feel rough.
- Setting up Spotify Connect *from scratch* (choosing Soloist vs go-librespot and entering a Soloist API key) still uses the same setup-flow machinery; the first version focuses on toggling rooms when the plugin is already present.
- The control API has no authentication of its own; these endpoints are reachable by anyone on the household LAN.

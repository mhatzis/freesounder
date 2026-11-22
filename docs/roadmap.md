# FreeSounder roadmap

Decisions agreed on 23 November 2026 (Australia/Melbourne). Order within a
release is priority order.

## 0.6.19

1. **Spotify sign-in with a pairing code.** Spotify no longer accepts a
   speaker sign-in with a token from the app's own Spotify client id
   (go-librespot `login5: INVALID_CREDENTIALS`, go-librespot issue #364).
   Use go-librespot's `device_auth` credentials: the player starts it, the
   app shows the code from go-librespot's `GET /auth/code` with an "Open
   spotify.com/pair" button, and the stored credentials are kept
   (`persist_credentials`). Picking the room in the official Spotify app
   (zeroconf) keeps working as the other way in.
2. **Bluetooth dropouts follow-up.** A "Robust" Bluetooth quality (lower SBC
   bitpool, about a third less airtime (237 vs 345 kbps)), suggested by the app when Diagnostics
   shows Bluetooth-side gaps. Needs the Diagnostics text from the lounge.
3. **Players reappear by themselves** after a power cycle (no app restart).
4. **Group volume as smooth as room volume:** the app sets each room's level
   while the "All rooms" slider moves, instead of group commands.
5. **Developer mode** is off each time the app starts; seven taps on the
   version toggle it on and off. The home-screen metadata line stays
   developer-only.
6. **Settings:** the "connected to" address moves to developer mode.
7. **Join this home:** a second phone on the same Wi-Fi is offered "Join the
   <name> home" with a code shown in Settings (a UX gate; the LAN API has no
   authentication yet).
8. **Now playing outside the app:** Android media notification; iOS Live
   Activity (may move to 0.6.20: needs a native extension).
9. **Bluetooth keep-alive:** while Bluetooth is a room's output and nothing
   plays, keep the stream open with a near-inaudible signal (adjustable; an
   audible soft chime as a fallback). The IKEA SOLSKYDD's auto standby is
   1 minute.
10. **Low-voltage warning** on the room when the player reports under-voltage.
11. **Image:** apt lists dropped after pi-gen's export `apt-get update` (done).

## Next (0.7.x)

- **OTA updates** (from the `feat/ota` branch). Needed before licensing.
- **Sonos speakers as rooms:** control Sonos players from FreeSounder (local
  UPnP or the Sonos Control API). FreeSounder cannot join a Sonos system as a
  Sonos player: Sonos does not allow third-party players.
- **Licensing** (below), after OTA.
- YouTube Music and Amazon Music: no supported playback API (YouTube Music
  only through the unofficial ytmusicapi; Amazon Music playback is limited to
  certified devices with Widevine). Both already work from a phone over
  AirPlay.

## Licensing (design)

**What a licence unlocks (per player, A$4):**

| Without a licence | With a licence |
|---|---|
| Radio, music library, podcasts, Spotify, AirPlay, UPnP | same |
| Groups of up to **2 rooms** | groups of any size |
| Wired output only | **Bluetooth speaker output** |

Streaming services are never what is sold (Spotify's developer terms allow
its integration for personal, non-commercial use only).

**Identity:** the Raspberry Pi's hardware serial number (`/proc/cpuinfo`
`Serial`, from OTP), not the SD card, so a reflash or an OTA update cannot
lose a licence.

**Licence:** a small document `{serial, features, issued, licence_id}`
signed with an Ed25519 key held by the portal. Every image carries the public
key and verifies offline; nothing phones home on boot.

**Portal (portal.freesounder.org):**

1. Buy: the app's Settings → Licences shows each player's serial and opens the
   portal with it filled in. Pay A$4 (Stripe or PayPal Checkout).
2. The payment webhook records the licence and signs it.
3. The player fetches its licence (`GET /licence/<serial>`) when it is online
   and stores it on the boot partition and in `/etc/freesounder`. After a
   reflash it fetches it again by itself.
4. Account by email (magic link): lists the owner's serials and licences.
5. **Transfer** (changed hardware): move a licence from one serial to
   another in the portal. The old serial is put on a revocation list; players
   refresh that list when they check for updates (OTA) and drop a revoked
   licence. Limit transfers (for example one per 90 days) to deter sharing.

**Open points:** what counts for a group of 3+ (every room in it licensed,
or the leader only); Apple's in-app purchase rules if the iOS app unlocks
features bought on the web (keep the purchase on the website, show status
only); FreeSounder is Apache-2.0, so the check is a convenience, not
protection.

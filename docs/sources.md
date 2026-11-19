# Sources on a v2 player (0.6.12)

Every source plays on the group leader into the group's Snapcast stream.
From there it goes through each room's own sound chain (fsdsp: bass,
treble, loudness, balance), so all sources sound the same and stay in sync.
Starting any source stops the one before it, in the whole group.

In the app, the **music** button in the top bar opens the source picker:
Radio, Music library, Podcasts and Spotify, and how to cast with AirPlay or
UPnP / DLNA.

| Source | Started from | Plays via |
|---|---|---|
| Radio | the app (radio-browser.info) | ffmpeg → `Radio` stream |
| Spotify | the Spotify app (Connect) | go-librespot → `Spotify` stream |
| AirPlay | Control Centre / any AirPlay button | shairport-sync → `AirPlay` stream |
| Music library | the app | MPD → `Media` stream |
| Podcasts | the app | MPD → `Media` stream |
| UPnP / DLNA | BubbleUPnP, mconnect, foobar2000, Audirvana, … | upmpdcli → MPD → `Media` stream |

## Music library (NAS and USB)

- **Add music** (Music library → shares button → Add): SMB (Windows / NAS
  shares, guest or user and password), NFS (host and export path), or a USB
  drive plugged into the player that holds the library.
- Shares are mounted **read-only** under `/var/lib/freesounder/music/<id>` on
  one player, the *library player* (the first player you add a share on).
  Its MPD indexes the files. Up to 8 shares.
- The SMB password is stored on that player only, in
  `/var/lib/freesounder/library/<id>.cred` (mode 0600). The API never returns
  it (only `has_password`) and it is never logged. Factory reset unmounts the
  shares and deletes it.
- Browse by album, artist or folder, or search. Covers come from the files'
  embedded art, else `cover.jpg` / `folder.jpg` / `front.jpg` in the folder.
- Any room or group can play the library: the group leader's MPD streams the
  files from the library player over HTTP
  (`/api/v2/library/file?uri=…&sig=…`, with a per-file token signed with the
  home key), so only the library player needs the share.
- The library is rescanned at start and once a day; **Rescan** on the shares
  screen does it now.

## Podcasts

- Search uses the Apple Podcasts directory (iTunes Search API, no account or
  key). Episodes come from the podcast's own RSS feed, read by the player.
- Subscriptions and "where you stopped" are shared by the whole home: every
  player keeps a copy (`podcasts.json`) and takes a newer one from its peers.
  Any phone sees the same list and resumes at the same place.
- Playback resumes at the saved position (after 10 s). An episode within
  30 s of the end counts as played. Use "Play from the start" to start over.
  Podcasts can be seeked from now playing.

## UPnP / DLNA (with OpenHome)

- Every group leader runs upmpdcli, so each group shows up as a renderer
  named after the group (a solo room under its room name). It supports
  UPnP AV and OpenHome (playlists that keep playing when the phone sleeps,
  in BubbleUPnP's "OpenHome" mode, Linn Kazoo, Lumin, …).
- Picking it in a control app takes over the group like Spotify does: other
  sources stop, the FreeSounder app shows the song and cover, and its play /
  pause, skip and volume work. The control app's volume slider is the group
  volume.
- Members of a group don't advertise themselves; only the leader does.

## Limits (0.6.12)

- Not measured yet on a Pi Zero 2 W: MPD's memory use with a large library
  (it keeps the database in RAM; expect tens of MB for ~50 000 songs).
  `freesounder-mpd.service` is capped at 160 MB.
- Leaving a group while it plays the library or a podcast does not hand the
  playback over: the remaining members stop, like with Spotify.
- USB drives must be plugged into the library player.
- upmpdcli comes from its author's Debian / Raspberry Pi OS repository
  (pinned by SHA-256 in `images/player/deps/versions.env`), not from Debian.

# freesounder Flutter app

Controller for iOS, Android and web. The app talks only to the
[control-api](../../services/control-api) on a player (or, by default, uses
in-memory **mock data** so the rooms panel can be developed without hardware).

## Features in this skeleton

- Persistent mini-player with a rooms chip
- Rooms bottom sheet with collapsed / half / full snap detents
- Right-hand rooms rail at **900px** and above
- A single Riverpod rooms-and-playback store
- Basic Material 3 theme tokens

```bash
flutter pub get
flutter analyze
flutter test
flutter run -d chrome   # or a device
flutter build web
```

To point at a running control-api, change `AppConfig.mockMode` to `false` and
set `apiBase` (see `lib/state/playback_store.dart`).

The app discovers a household host advertised as `_freesounder._tcp`, or you
can type the host address in **Settings**. If none is found it opens a demo
household so the UI can be tried without hardware.

Use **Settings → Add a player** (or the + in the rooms panel) to name a new
Pi and join it to the system. Open room settings from the gear on a room,
a long-press, or the room name on now-playing, to set the name, volume
limit, bass, treble, balance, bass boost and audio board.

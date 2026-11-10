# freesounder

**freesounder** is a free, open-source multi-room wireless audio system that aims to feel as simple and polished as commercial whole-home speaker systems, without the lock-in.

> Status: early design. The architecture research and recommended stack are being written up now; code will follow.

## Goals

- **Easy to use.** Set up a speaker, pick a room, press play. Grouping rooms, per-room volume and handing music between rooms should be one tap.
- **Tight multi-room sync.** Rooms grouped together play in lockstep, with no audible echo between them.
- **Streaming services.** Spotify, Apple Music, Tidal and others wherever an open API or integration (Spotify Connect, AirPlay 2 and so on) makes it possible, plus internet radio and your own local library.
- **Native mobile apps.** First-class iPhone (iOS) and Android apps for control, and a web app for everything else.
- **Cheap, open hardware.** Runs on a Raspberry Pi with a DAC or amplifier HAT, or on an old PC, so you can build a speaker for any room.
- **Free and public.** Developed in the open under a permissive licence, with no accounts, cloud lock-in or telemetry required.

## Planned components

| Component | Role |
|---|---|
| Server | Library, queues, streaming-service sources, room and group state |
| Sync layer | Time-synchronized audio distribution to every room |
| Room player | Lightweight client for Raspberry Pi and Linux speakers |
| Control API | Documented API used by every app |
| Mobile apps | Native iOS and Android controllers |
| Web app | Browser-based controller and setup |

## Contributing

The project is just getting started. Issues with ideas, hardware reports and design feedback are welcome.

## Licence

freesounder is licensed under the [Apache License 2.0](LICENSE). Some third-party components it may build on use other licences (for example GPL-3.0); those run as separate programs and keep their own licences.

"Sonos" is a trademark of Sonos, Inc. freesounder is an independent project and is not affiliated with or endorsed by Sonos.

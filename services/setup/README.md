# services/setup

Stub for first-boot setup, room naming and household pairing.

This will eventually be a small service (or a mode of the player image) that:

- brings up a setup Wi-Fi / access-point or uses Ethernet
- names the room
- joins an existing household or starts a new one
- elects or records the 4 GB household host
- documents backup/restore of Music Assistant `/data` for the first beta

It is not implemented yet. The [control-api](../control-api) already exposes a health endpoint and mDNS advertisement that setup can later consume.

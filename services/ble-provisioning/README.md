# services/ble-provisioning

Improv Wi-Fi over BLE for a FreeSounder Pi that has no network yet
(no Ethernet link and no working Wi-Fi). The service advertises
`FreeSounder-XXXX` (last four hex digits of a MAC address) and the
standard Improv GATT service. The Flutter app is the client.

It runs only as a BLE peripheral (no A2DP / Bluetooth audio) so it
does not interfere with `snapclient`. Advertising stops once the board
has a usable IP address, and starts again if the network later drops
before the player is otherwise reachable.

Passwords are never written to the log.

## Protocol

[Improv Wi-Fi via BLE](https://www.improv-wifi.com/ble/) 2.4: scan,
device info, hostname / device name, and Wi-Fi credentials. The device
starts **Authorized** (no PIN, no button). On a failed join the error
state is `unable to connect` and the RPC result includes a short
reason (`wrong_password`, `ssid_not_found`, or `unable_to_connect`).

## Run

```bash
uv sync --group dev
uv run ruff check src tests
uv run pytest
```

On a Pi the systemd unit `freesounder-ble-provisioning.service` starts
this after `bluetooth` and NetworkManager.

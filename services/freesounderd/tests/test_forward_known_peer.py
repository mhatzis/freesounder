"""Rooms the player only remembers (no recent beacon) are still reachable.

The rooms list shows remembered members (store.known_peers) even when they
are missing from engine.peers, e.g. right after this player restarted or
while beacons are being missed. Requests for such a room used to fail with
404 "Unknown player", which the BT app read as "no Bluetooth on this player".
"""

from __future__ import annotations

from .conftest import Lan, settle
from .test_daemon import setup_household


async def test_remembered_room_is_forwarded_to_its_last_address(lan: Lan, make_player) -> None:
    a, b = await setup_household(make_player, ("10.0.0.1", "Pi 5"), ("10.0.0.2", "Pi Zero 2 W"))
    await settle(lan, [a, b])
    assert b.id in a.engine.store.known_peers()
    a.engine.peers.pop(b.id, None)  # no beacon from b since a restarted
    rooms = (await a.client.get("/api/v2/rooms")).json()
    assert any(r["id"] == b.id for r in rooms)
    res = await a.client.get(f"/api/v2/rooms/{b.id}/settings")
    assert res.status_code == 200, res.text


async def test_remembered_room_that_is_down_is_not_a_404(lan: Lan, make_player) -> None:
    a, b = await setup_household(make_player, ("10.0.0.1", "Pi 5"), ("10.0.0.2", "Pi Zero 2 W"))
    await settle(lan, [a, b])
    a.engine.peers.pop(b.id, None)
    lan.down.add("10.0.0.2")
    res = await a.client.get(f"/api/v2/rooms/{b.id}/settings")
    assert res.status_code == 502, res.text
    assert "did not answer" in res.json()["detail"]


async def test_never_seen_player_is_still_404(lan: Lan, make_player) -> None:
    (a,) = await setup_household(make_player, ("10.0.0.1", "Pi 5"))
    res = await a.client.get("/api/v2/rooms/9b4a4454-7d22-44e0-8dd0-3e6cea5b9f02/settings")
    assert res.status_code == 404 and "Unknown player" in res.json()["detail"]

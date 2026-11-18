"""Room controls answer quickly and slider drags don't queue up."""

import asyncio

from .conftest import Lan, settle
from .test_daemon import setup_household


async def test_volume_does_not_wait_for_a_slow_tick(lan: Lan, make_player) -> None:
    (a,) = await setup_household(make_player, ("10.0.0.1", "Pi 5"))
    await settle(lan, [a])
    async with a.engine._lock:  # a tick busy with systemctl etc.
        res = await asyncio.wait_for(
            a.client.post(f"/api/v2/rooms/{a.id}/volume", json={"volume": 33}), timeout=1
        )
    assert res.status_code == 200
    assert lan.snapservers[a.ip].clients[a.id]["config"]["volume"]["percent"] == 33


async def test_slider_drag_coalesces_and_lands_on_the_last_value(lan: Lan, make_player) -> None:
    (a,) = await setup_household(make_player, ("10.0.0.1", "Pi 5"))
    await settle(lan, [a])
    server = lan.snapservers[a.ip]
    before = sum(1 for m, _ in server.calls if m == "Client.SetVolume")
    await asyncio.gather(
        *(a.client.post(f"/api/v2/rooms/{a.id}/volume", json={"volume": v}) for v in range(10, 60))
    )
    sent = sum(1 for m, _ in server.calls if m == "Client.SetVolume") - before
    assert server.clients[a.id]["config"]["volume"]["percent"] == 59
    assert sent <= 50
    assert a.engine.group["volume"] == 59


async def test_forwarded_volume_shows_at_once(lan: Lan, make_player) -> None:
    a, b = await setup_household(make_player, ("10.0.0.1", "Pi 5"), ("10.0.0.2", "Pi 4"))
    await settle(lan, [a, b])
    await a.client.post(f"/api/v2/rooms/{b.id}/volume", json={"volume": 71})
    rooms = {r["id"]: r for r in (await a.client.get("/api/v2/rooms")).json()}
    assert rooms[b.id]["volume"] == 71, "no snap back while waiting for the next poll"
    assert lan.snapservers[b.ip].clients[b.id]["config"]["volume"]["percent"] == 71

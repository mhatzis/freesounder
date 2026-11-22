"""A paused room keeps its song and cover (0.6.18).

Reported on 0.6.17: with everything paused, the cover art kept changing.
"""

from __future__ import annotations

from .conftest import Lan, settle
from .test_daemon import setup_household


async def _np(player) -> dict:
    res = await player.client.get("/api/v2/now-playing", params={"player_id": player.id})
    return res.json()


async def test_paused_radio_keeps_its_song_and_cover(lan: Lan, make_player) -> None:
    (a,) = await setup_household(make_player, ("10.0.0.1", "Pi 5"))
    await a.client.post(
        f"/api/v2/groups/{a.id}/radio",
        json={"url": "http://radio.example/s", "name": "R1", "favicon": "http://logo/r1.png"},
    )
    a.engine.radio.stream_title = "Band A - Song A"
    await settle(lan, [a])
    before = await _np(a)
    assert before["title"] == "Song A" and before["state"] == "playing"
    assert (await a.client.post(f"/api/v2/groups/{a.id}/pause")).status_code == 200
    await settle(lan, [a])
    # The station moves on while we are paused: nothing on screen changes.
    a.engine.radio.stream_title = "Band B - Song B"
    await settle(lan, [a], rounds=3)
    paused = await _np(a)
    assert paused["state"] == "paused"
    assert (paused["title"], paused["artist"], paused["image_url"]) == (
        before["title"],
        before["artist"],
        before["image_url"],
    )
    # Playing again shows what is on now.
    assert (await a.client.post(f"/api/v2/groups/{a.id}/play")).status_code == 200
    a.engine.radio.stream_title = "Band B - Song B"
    await settle(lan, [a], rounds=2)
    assert (await _np(a))["title"] == "Song B"

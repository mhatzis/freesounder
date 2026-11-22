"""Taking a room out of a group turns that room off; the rest keep the music.

Reported on 0.6.17: on room A in a group led by B, unticking B stopped A and
left B playing (Spotify / library stayed on the leader).
"""

from __future__ import annotations

from .conftest import Lan, settle
from .test_daemon import setup_household
from .test_music_sources import _library_on


async def test_removing_the_leader_keeps_library_music_on_the_room_i_am_on(
    lan: Lan, make_player
) -> None:
    a, b, c = await setup_household(
        make_player, ("10.0.0.1", "Pi 5"), ("10.0.0.2", "Pi 4"), ("10.0.0.3", "Pi 3")
    )
    players = [a, b, c]
    await _library_on(a, lan)
    await settle(lan, players)
    await a.client.put(f"/api/v2/groups/{a.id}/members", json={"members": [a.id, b.id, c.id]})
    await settle(lan, players)
    tracks = (await a.client.get("/api/v2/library/album", params={"album": "Album X"})).json()
    res = await a.client.post(
        f"/api/v2/groups/{a.id}/library/play", json={"tracks": tracks, "start": 1}
    )
    assert res.status_code == 200, res.text
    await settle(lan, players)
    assert a.engine.source == "library" and a.mpd.pos == 1
    # From C (the room I'm on), untick A, the leader.
    res = await c.client.put(f"/api/v2/groups/{a.id}/members", json={"members": [c.id, b.id]})
    assert res.status_code == 200, res.text
    await settle(lan, players)
    assert c.engine.group["follow"] is None, "the room I'm on leads what remains"
    assert b.engine.group["follow"] == c.id
    assert c.engine.source == "library" and c.mpd.state == "play" and c.mpd.pos == 1
    assert a.engine.source == "none" and a.mpd.state == "stop", "the room taken out is off"
    assert a.engine.group["follow"] is None


async def test_removing_a_spotify_leader_hands_the_group_over_and_asks_for_a_transfer(
    lan: Lan, make_player
) -> None:
    a, b = await setup_household(make_player, ("10.0.0.1", "Pi 5"), ("10.0.0.2", "Pi 4"))
    players = [a, b]
    await settle(lan, players)
    await a.client.put(f"/api/v2/groups/{a.id}/members", json={"members": [a.id, b.id]})
    await settle(lan, players)
    lan.spotify[a.ip] = {
        "username": "me",
        "device_id": "dev-a",
        "stopped": False,
        "paused": False,
        "track": {"uri": "spotify:track:t1", "name": "Song", "artist_names": ["X"]},
    }
    res = await b.client.post(
        f"/api/v2/groups/{b.id}/spotify/play", json={"uri": "spotify:album:abc"}
    )
    assert res.status_code == 200, res.text
    await settle(lan, players)
    assert a.engine.source == "spotify"
    # B takes A (the leader) out with "leave": A goes quiet, B keeps the group.
    res = await b.client.post(f"/api/v2/rooms/{a.id}/leave", json={"keep": b.id})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["leader"] == b.id
    assert body["transfer"]["source"] == "spotify" and body["transfer"]["to"] == b.id
    await settle(lan, players)
    assert ("10.0.0.1", "/player/pause") in [(h, p) for h, p, _ in lan.spotify_calls]
    # A's Spotify device is paused; the app then moves the session to B.
    assert b.engine.group["follow"] is None and a.engine.group["follow"] is None


async def test_removing_a_member_never_touches_the_leader(lan: Lan, make_player) -> None:
    a, b = await setup_household(make_player, ("10.0.0.1", "Pi 5"), ("10.0.0.2", "Pi 4"))
    players = [a, b]
    await settle(lan, players)
    await a.client.put(f"/api/v2/groups/{a.id}/members", json={"members": [a.id, b.id]})
    await a.client.post(
        f"/api/v2/groups/{a.id}/radio", json={"url": "http://radio.example/g", "name": "G"}
    )
    await settle(lan, players)
    res = await a.client.put(f"/api/v2/groups/{a.id}/members", json={"members": [a.id]})
    assert res.status_code == 200, res.text
    await settle(lan, players)
    assert a.engine.source == "radio" and a.engine.radio.station["name"] == "G"
    assert b.engine.group["follow"] is None and b.engine.source == "none"


async def test_adding_a_playing_room_to_an_idle_room_keeps_the_music(
    lan: Lan, make_player
) -> None:
    """0.6.20: from idle room A, add playing room B: B's music plays in both."""

    a, b = await setup_household(make_player, ("10.0.0.1", "Pi 5"), ("10.0.0.2", "Pi 4"))
    players = [a, b]
    await settle(lan, players)
    res = await b.client.post(
        f"/api/v2/groups/{b.id}/radio", json={"url": "http://radio.example/b", "name": "B FM"}
    )
    assert res.status_code == 200, res.text
    await settle(lan, players)
    res = await a.client.put(f"/api/v2/groups/{a.id}/members", json={"members": [a.id, b.id]})
    assert res.status_code == 200, res.text
    await settle(lan, players)
    assert b.engine.group["follow"] is None and b.engine.source == "radio"
    assert b.engine.radio.station["name"] == "B FM", "the playing room kept its music"
    assert a.engine.group["follow"] == b.id, "the idle room joined it"


async def test_adding_an_idle_room_to_a_playing_room_is_unchanged(lan: Lan, make_player) -> None:
    a, b = await setup_household(make_player, ("10.0.0.1", "Pi 5"), ("10.0.0.2", "Pi 4"))
    players = [a, b]
    await settle(lan, players)
    await a.client.post(
        f"/api/v2/groups/{a.id}/radio", json={"url": "http://radio.example/a", "name": "A FM"}
    )
    await settle(lan, players)
    await a.client.put(f"/api/v2/groups/{a.id}/members", json={"members": [a.id, b.id]})
    await settle(lan, players)
    assert a.engine.source == "radio" and b.engine.group["follow"] == a.id

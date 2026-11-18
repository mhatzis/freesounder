"""Spotify in-app playback plumbing and now-playing metadata."""

from __future__ import annotations

import asyncio
import base64

from freesounderd.artwork import AirplayArt, split_stream_title
from freesounderd.spotify import clear_credentials, read_state, render_config, stored_username

from .conftest import Lan, settle
from .test_daemon import setup_household


async def test_spotify_play_goes_to_the_group_leader(lan: Lan, make_player) -> None:
    a, b = await setup_household(make_player, ("10.0.0.1", "Pi 5"), ("10.0.0.2", "Pi 4"))
    await settle(lan, [a, b])
    res = await a.client.put(f"/api/v2/groups/{a.id}/members", json={"members": [a.id, b.id]})
    assert res.status_code == 200, res.text
    await settle(lan, [a, b])
    leader = a if a.engine.is_leader else b
    other = b if leader is a else a
    # Not signed in yet: a clear 409, nothing played.
    res = await other.client.post(
        f"/api/v2/groups/{other.id}/spotify/play", json={"uri": "spotify:album:abc"}
    )
    assert res.status_code == 409, res.text
    assert "signed in" in res.json()["detail"]
    lan.spotify[leader.ip] = {
        "username": "me",
        "device_id": "dev1",
        "stopped": True,
        "paused": False,
        "track": None,
    }
    info = (await other.client.post(f"/api/v2/groups/{other.id}/spotify")).json()
    assert info["logged_in"] and info["username"] == "me" and info["device_id"] == "dev1"
    assert info["leader_id"] == leader.id and info["device_name"].endswith("+ 1")
    res = await other.client.post(
        f"/api/v2/groups/{other.id}/spotify/play",
        json={"uri": "spotify:playlist:p1", "skip_to_uri": "spotify:track:t2"},
    )
    assert res.status_code == 200, res.text
    host, path, body = lan.spotify_calls[-1]
    assert (host, path) == (leader.ip, "/player/play")
    assert body == {"uri": "spotify:playlist:p1", "skip_to_uri": "spotify:track:t2"}
    assert leader.engine.source == "spotify"
    res = await other.client.post(f"/api/v2/groups/{other.id}/seek", json={"position_ms": 5000})
    assert res.status_code == 200, res.text
    assert lan.spotify_calls[-1][1:] == ("/player/seek", {"position": 5000})
    res = await other.client.post(
        f"/api/v2/groups/{other.id}/spotify/context-tracks", json={"uri": "spotify:playlist:p1"}
    )
    assert res.status_code == 200 and res.json()["uri"] == "spotify:playlist:p1"


async def test_spotify_login_validates_and_never_echoes_token(make_player) -> None:
    (a,) = await setup_household(make_player, ("10.0.0.1", "Pi 5"))
    res = await a.client.post(f"/api/v2/groups/{a.id}/spotify/login", json={"username": "me"})
    assert res.status_code == 422
    res = await a.client.post(
        f"/api/v2/groups/{a.id}/spotify/login",
        json={"username": "me", "access_token": "secret-token-value"},
    )
    assert res.status_code == 200
    assert "secret-token-value" not in res.text


def test_spotify_config_and_state(tmp_path) -> None:
    normal = render_config(device_name="Kitchen", fifo="/f")
    assert "type: zeroconf" in normal and "persist_credentials: true" in normal
    assert "access_token" not in normal and "metadata:\n  enabled: true" in normal
    login = render_config(device_name="Kitchen", fifo="/f", token_login=("me", "tok"))
    assert "type: spotify_token" in login and 'access_token: "tok"' in login
    (tmp_path / "state.json").write_text(
        '{"device_id": "d1", "credentials": {"username": "me", "data": "QUJD"}}'
    )
    assert stored_username(tmp_path) == "me"
    clear_credentials(tmp_path)
    assert stored_username(tmp_path) is None
    assert read_state(tmp_path)["device_id"] == "d1"


def test_split_stream_title() -> None:
    assert split_stream_title("Daft Punk - One More Time") == ("Daft Punk", "One More Time")
    assert split_stream_title("Station jingle") == (None, "Station jingle")
    assert split_stream_title(None) == (None, None)


async def test_radio_art_lookup_falls_back_to_station_logo(lan: Lan, make_player) -> None:
    (a,) = await setup_household(make_player, ("10.0.0.1", "Pi 5"))
    await a.client.post(
        f"/api/v2/groups/{a.id}/radio",
        json={"url": "http://radio.example/s", "name": "Radio X", "favicon": "http://logo/x.png"},
    )
    await settle(lan, [a], rounds=1)
    a.engine.radio.stream_title = "Daft Punk - One More Time"
    lan.itunes = [
        {"artistName": "Daft Punk", "artworkUrl100": "https://is1.example/a/100x100bb.jpg"}
    ]
    await settle(lan, [a], rounds=1)
    np = (await a.client.get(f"/api/v2/groups/{a.id}/now-playing")).json()
    # First tick: lookup started, station logo meanwhile.
    assert np["title"] == "One More Time" and np["artist"] == "Daft Punk"
    assert np["station_image_url"] == "http://logo/x.png"
    for _ in range(20):
        await asyncio.sleep(0.01)
    await settle(lan, [a], rounds=1)
    np = (await a.client.get(f"/api/v2/groups/{a.id}/now-playing")).json()
    assert np["image_url"] == "https://is1.example/a/600x600bb.jpg"
    rooms = (await a.client.get("/api/v2/rooms")).json()
    assert rooms[0]["now_playing"]["title"] == "One More Time"
    a.engine.radio.stream_title = "Nobody - Unknown Song"
    lan.itunes = []
    await settle(lan, [a], rounds=1)
    for _ in range(20):
        await asyncio.sleep(0.01)
    await settle(lan, [a], rounds=1)
    np = (await a.client.get(f"/api/v2/groups/{a.id}/now-playing")).json()
    assert np["image_url"] == "http://logo/x.png"


async def test_airplay_cover_is_served_by_the_player(make_player) -> None:
    (a,) = await setup_household(make_player, ("10.0.0.1", "Pi 5"))
    jpeg = b"\xff\xd8\xff fake jpeg"
    a.engine.source = "airplay"
    np = a.engine._now_playing(
        {
            "properties": {
                "metadata": {
                    "title": "Song",
                    "artist": ["Band"],
                    "album": "LP",
                    "artData": {"data": base64.b64encode(jpeg).decode(), "extension": "jpg"},
                }
            }
        }
    )
    assert np["title"] == "Song" and np["artist"] == "Band"
    assert np["image_url"].startswith("http://10.0.0.1:8080/api/v2/art/airplay?t=")
    res = await a.client.get("/api/v2/art/airplay")
    assert res.status_code == 200 and res.content == jpeg
    art = AirplayArt()
    assert art.update({"artUrl": "http://host:1780/__image_cache?name=x.png"})
    assert art.source_path == "/__image_cache?name=x.png"
    assert art.update({"artUrl": "http://evil/elsewhere"}) is None


async def test_spotify_connect_volume_two_way(lan: Lan, make_player) -> None:
    """Spotify app slider -> group volume; FreeSounder volume -> Spotify slider."""

    (a,) = await setup_household(make_player, ("10.0.0.1", "Pi 5"))
    await settle(lan, [a])
    await a.client.post(f"/api/v2/rooms/{a.id}/volume", json={"volume": 30})
    lan.spotify[a.ip] = {
        "username": "me",
        "device_id": "dev1",
        "stopped": False,
        "paused": False,
        "volume": 100,
        "volume_steps": 100,
        "track": {"name": "Song", "artist_names": ["Band"], "uri": "spotify:track:t1"},
    }

    def pushes():
        return [c[2]["volume"] for c in lan.spotify_calls if c[1] == "/player/volume"]

    await settle(lan, [a])
    # New session: Spotify is told our level; our volume does not jump to 100.
    assert pushes() and pushes()[-1] == 30
    assert a.engine.group["volume"] == 30
    lan.spotify[a.ip]["volume"] = 30
    await settle(lan, [a])
    # The Spotify app moves its slider.
    a.engine._spotify_push_at = 0
    lan.spotify[a.ip]["volume"] = 70
    await settle(lan, [a])
    await settle(lan, [a])
    assert a.engine.group["volume"] == 70
    # FreeSounder moves the room volume: Spotify follows.
    n = len(pushes())
    await a.client.post(f"/api/v2/rooms/{a.id}/volume", json={"volume": 20})
    await settle(lan, [a])
    assert pushes()[n:] and pushes()[-1] == 20
    assert a.engine.group["volume"] == 20


async def test_airplay_sender_volume_becomes_group_volume(lan: Lan, make_player) -> None:
    (a,) = await setup_household(make_player, ("10.0.0.1", "Pi 5"))
    await settle(lan, [a])
    await a.client.post(f"/api/v2/rooms/{a.id}/volume", json={"volume": 35})
    a.engine.source = "airplay"
    # First report of a session (the sender's stored level) does not jump the room.
    res = await a.client.post("/api/v2/airplay/volume", json={"airplay_db": 0})
    assert res.json() == {"applied": False}
    assert a.engine.group_for_room(a.id)["volume"] == 35
    res = await a.client.post("/api/v2/airplay/volume", json={"airplay_db": -15})
    assert res.json() == {"applied": True, "volume": 50}
    assert a.engine.group_for_room(a.id)["volume"] == 50
    res = await a.client.post("/api/v2/airplay/volume", json={"airplay_db": -144})
    assert res.json()["muted"] is True
    assert (
        await a.client.post("/api/v2/airplay/volume", json={"airplay_db": 5})
    ).status_code == 422


async def test_grouped_radio_song_changes_reach_every_member(lan: Lan, make_player) -> None:
    """Two rooms grouped on radio: a new song shows on the member too, with new art."""
    a, b = await setup_household(make_player, ("10.0.0.1", "Pi 5"), ("10.0.0.2", "Pi 5"))
    await settle(lan, [a, b])
    await a.client.put(f"/api/v2/groups/{a.id}/members", json={"members": [a.id, b.id]})
    titles = ["Daft Punk - One More Time", "Air - Sexy Boy"]

    async def fetcher(url: str, ua: str) -> str | None:
        return titles[0]

    radio = a.engine.radio
    radio.poll_titles, radio.title_poll, radio.title_fetcher = True, 0.01, fetcher
    await a.client.post(
        f"/api/v2/groups/{a.id}/radio",
        json={"url": "http://radio.example/s", "name": "Radio X", "favicon": "http://logo/x.png"},
    )
    lan.itunes = [
        {"artistName": "Daft Punk", "artworkUrl100": "https://is1.example/dp/100x100bb.jpg"}
    ]
    for _ in range(3):
        await asyncio.sleep(0.05)
        await settle(lan, [a, b])
    np = (await b.client.get("/api/v2/now-playing", params={"player_id": b.id})).json()
    assert np["title"] == "One More Time"
    assert np["image_url"] == "https://is1.example/dp/600x600bb.jpg"
    first_update = np["meta_updated_at"]
    assert first_update and np["title_checked_at"] and np["meta_from"]

    titles[0] = "Air - Sexy Boy"
    lan.itunes = [{"artistName": "Air", "artworkUrl100": "https://is1.example/air/100x100bb.jpg"}]
    for _ in range(3):
        await asyncio.sleep(0.05)
        await settle(lan, [a, b])
    np = (await b.client.get("/api/v2/now-playing", params={"player_id": b.id})).json()
    assert (np["artist"], np["title"]) == ("Air", "Sexy Boy")
    assert np["image_url"] == "https://is1.example/air/600x600bb.jpg"
    assert np["meta_updated_at"] > first_update
    rooms = (await b.client.get("/api/v2/rooms")).json()
    assert {r["now_playing"]["title"] for r in rooms} == {"Sexy Boy"}
    await radio.stop()

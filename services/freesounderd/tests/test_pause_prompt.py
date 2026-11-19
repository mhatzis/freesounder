"""Pause is prompt for every source, even while a slow tick holds the lock.

0.6.14 with a Bluetooth speaker: pause took ~9 s, the icon ended up wrong and
it took two presses to resume. Play/pause waited behind the tick (systemctl,
snapserver/Spotify/MPD status, all under the engine lock), and the buffered
audio (snapserver 1 s + the 400 ms Bluetooth ALSA buffer) still played after
the source stopped.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from freesounderd import engine as engine_mod

from .conftest import Lan, Player, settle
from .test_daemon import setup_household

SPOTIFY_PLAYING = {
    "username": "me",
    "device_id": "dev1",
    "stopped": False,
    "paused": False,
    "volume": 40,
    "volume_steps": 100,
    "track": {"name": "Song", "artist_names": ["Band"], "uri": "spotify:track:t1"},
}


def _group(lan: Lan, p: Player) -> dict[str, Any]:
    server = lan.snapservers[p.ip]
    return next(g for g in server.groups if any(c["id"] == p.id for c in g["clients"]))


async def _state(p: Player) -> str:
    np = (await p.client.get("/api/v2/now-playing", params={"player_id": p.id})).json()
    return np["state"]


async def _start(source: str, lan: Lan, p: Player) -> None:
    if source == "spotify":
        lan.spotify[p.ip] = dict(SPOTIFY_PLAYING)
    elif source == "airplay":
        stream = next(s for s in lan.snapservers[p.ip].streams if s["id"] == "AirPlay")
        stream["status"] = "playing"
    elif source == "radio":
        res = await p.client.post(
            f"/api/v2/groups/{p.id}/radio", json={"url": "http://r/s", "name": "R FM"}
        )
        assert res.status_code == 200
    elif source == "upnp":
        p.mpd.external_play("http://nas/song.flac")
    await settle(lan, [p])
    assert p.engine.source == source
    assert await _state(p) == "playing"
    assert _group(lan, p)["stream_id"] == engine_mod.SOURCE_STREAM[source]


def _source_paused(source: str, lan: Lan, p: Player) -> bool:
    if source == "spotify":
        return any(path == "/player/pause" for _, path, _ in lan.spotify_calls)
    if source == "airplay":
        return ("Stream.Control", {"id": "AirPlay", "command": "pause"}) in lan.snapservers[
            p.ip
        ].calls
    if source == "radio":
        return p.engine.radio.paused
    return p.mpd.state == "pause"


SOURCES = ["spotify", "airplay", "radio", "upnp"]


@pytest.mark.parametrize("source", SOURCES)
async def test_pause_mutes_and_reports_paused_at_once_during_a_slow_tick(
    source: str, lan: Lan, make_player
) -> None:
    (a,) = await setup_household(make_player, ("10.0.0.1", "Pi 5"))
    await settle(lan, [a])
    await _start(source, lan, a)
    async with a.engine._lock:  # a tick busy with systemctl and status calls
        task = asyncio.create_task(a.client.post(f"/api/v2/groups/{a.id}/pause"))
        await asyncio.sleep(0.05)
        # The room is silent and shows paused before the tick lets go.
        assert _group(lan, a).get("muted") is True
        assert await _state(a) == "paused"
        if source != "radio":  # radio stops ffmpeg under the lock (watchdog)
            res = await asyncio.wait_for(task, timeout=1)
            assert res.status_code == 200
            assert _source_paused(source, lan, a)
    res = await asyncio.wait_for(task, timeout=2)
    assert res.status_code == 200
    assert _source_paused(source, lan, a)


@pytest.mark.parametrize("source", SOURCES)
async def test_one_press_resumes_and_a_stale_read_does_not_flip_the_state(
    source: str, lan: Lan, make_player
) -> None:
    (a,) = await setup_household(make_player, ("10.0.0.1", "Pi 5"))
    await settle(lan, [a])
    await _start(source, lan, a)
    assert (await a.client.post(f"/api/v2/groups/{a.id}/pause")).status_code == 200
    # The source has not reported the pause yet (go-librespot / snapserver
    # lag): a tick reading "playing" must not flip the state back.
    if source == "spotify":
        assert lan.spotify[a.ip]["paused"] is False
    await settle(lan, [a], rounds=1)
    assert await _state(a) == "paused"
    assert _group(lan, a).get("muted") is True, "still within the tail mute"
    # One press resumes: an explicit play, unmuted, playing.
    assert (await a.client.post(f"/api/v2/groups/{a.id}/play")).status_code == 200
    assert _group(lan, a).get("muted") is False
    assert await _state(a) == "playing"
    if source == "spotify":
        paths = [path for _, path, _ in lan.spotify_calls if path.startswith("/player/")]
        assert paths[-2:] == ["/player/pause", "/player/resume"]
    if source == "radio":
        assert a.engine.radio.running and not a.engine.radio.paused
    if source == "upnp":
        assert a.mpd.state == "play"


async def test_toggle_twice_pauses_then_resumes(lan: Lan, make_player) -> None:
    (a,) = await setup_household(make_player, ("10.0.0.1", "Pi 5"))
    await settle(lan, [a])
    await _start("spotify", lan, a)
    await a.client.post(f"/api/v2/groups/{a.id}/toggle")
    assert await _state(a) == "paused"
    await a.client.post(f"/api/v2/groups/{a.id}/toggle")  # status still stale "playing"
    assert await _state(a) == "playing"
    paths = [path for _, path, _ in lan.spotify_calls if path.startswith("/player/")]
    assert paths[-2:] == ["/player/pause", "/player/resume"], "explicit, not two playpause"


async def test_tail_mute_ends_once_the_source_is_paused(lan: Lan, make_player, monkeypatch) -> None:
    (a,) = await setup_household(make_player, ("10.0.0.1", "Pi 5"))
    await settle(lan, [a])
    await _start("spotify", lan, a)
    await a.client.post(f"/api/v2/groups/{a.id}/pause")
    assert _group(lan, a)["muted"] is True
    lan.spotify[a.ip]["paused"] = True  # go-librespot caught up
    monkeypatch.setattr(engine_mod, "PAUSE_TAIL_MUTE", 0.0)
    await settle(lan, [a], rounds=1)
    assert _group(lan, a)["muted"] is False, "silent anyway; the room is not left muted"
    assert await _state(a) == "paused"
    # Resumed from the Spotify app: plays and is heard.
    lan.spotify[a.ip]["paused"] = False
    await settle(lan, [a], rounds=1)
    assert await _state(a) == "playing" and _group(lan, a)["muted"] is False


async def test_a_pause_the_source_never_confirms_does_not_mute_forever(
    lan: Lan, make_player, monkeypatch
) -> None:
    (a,) = await setup_household(make_player, ("10.0.0.1", "Pi 5"))
    await settle(lan, [a])
    await _start("airplay", lan, a)
    await a.client.post(f"/api/v2/groups/{a.id}/pause")  # the sender ignores it
    monkeypatch.setattr(engine_mod, "PAUSE_TAIL_MUTE", 0.0)
    await settle(lan, [a], rounds=1)
    assert _group(lan, a)["muted"] is True
    monkeypatch.setattr(engine_mod, "PAUSE_MUTE_MAX", 0.0)
    monkeypatch.setattr(engine_mod, "CMD_STATE_HOLD", 0.0)
    await settle(lan, [a], rounds=1)
    assert _group(lan, a)["muted"] is False
    assert await _state(a) == "playing", "the truth shows once the hold is over"


async def test_a_group_left_muted_by_a_restart_is_unmuted(lan: Lan, make_player) -> None:
    (a,) = await setup_household(make_player, ("10.0.0.1", "Pi 5"))
    await settle(lan, [a])
    _group(lan, a)["muted"] = True  # snapserver kept it in server.json
    await settle(lan, [a], rounds=1)
    assert _group(lan, a)["muted"] is False


async def test_pause_from_another_room_shows_there_at_once(lan: Lan, make_player) -> None:
    a, b = await setup_household(make_player, ("10.0.0.1", "Pi 5"), ("10.0.0.2", "Pi 4"))
    await settle(lan, [a, b])
    await _start("spotify", lan, a)
    await settle(lan, [a, b])
    # The app is connected to b and pauses a's room: forwarded to a.
    assert (await b.client.post(f"/api/v2/groups/{a.id}/pause")).status_code == 200
    assert _group(lan, a)["muted"] is True
    np = (await b.client.get("/api/v2/now-playing", params={"player_id": a.id})).json()
    assert np["state"] == "paused", "no wait for b's next peer poll"

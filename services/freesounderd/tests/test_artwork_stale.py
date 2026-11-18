"""Covers must never stick to the previous song."""

import asyncio
import base64
import json

import httpx
import pytest

from freesounderd.artwork import AirplayArt, ArtLookup
from freesounderd.engine import Engine


def _pic(n: int) -> dict:
    return {"data": base64.b64encode(bytes([n]) * 32).decode(), "extension": "jpg"}


def test_new_airplay_song_without_picture_gets_no_old_cover() -> None:
    art = AirplayArt()
    a = art.update({"title": "One", "artist": "X", "album": "Alpha", "artData": _pic(1)})
    assert a
    assert art.update({"title": "One", "artist": "X", "album": "Alpha"}) == a  # same song
    # Next song: title arrives before its picture.
    assert art.update({"title": "Two", "artist": "Y", "album": "Beta"}) is None
    # snapserver still holds the old picture: not shown for a different album.
    assert art.update({"title": "Two", "artist": "Y", "album": "Beta", "artData": _pic(1)}) is None
    b = art.update({"title": "Two", "artist": "Y", "album": "Beta", "artData": _pic(2)})
    assert b and b != a
    assert art.data == bytes([2]) * 32


def test_same_album_next_track_keeps_the_album_cover() -> None:
    art = AirplayArt()
    a = art.update({"title": "One", "album": "Alpha", "artData": _pic(1)})
    assert art.update({"title": "Two", "album": "Alpha", "artData": _pic(1)}) == a


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_itunes_error_is_retried_not_cached_as_no_art() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        body = {"results": [{"artistName": "Air", "artworkUrl100": "https://is1/x/100x100bb.jpg"}]}
        return httpx.Response(200, content=json.dumps(body))

    async with _client(handler) as http:
        look = ArtLookup(http)
        look.RETRY_AFTER = 0.0
        assert look.cached("Air", "La femme") is None
        await asyncio.sleep(0.05)
        assert look.cached("Air", "La femme") is None  # error: retry started
        await asyncio.sleep(0.05)
        assert look.cached("Air", "La femme") == "https://is1/x/600x600bb.jpg"
        assert calls["n"] == 2


async def test_no_match_is_cached() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, content=b'{"results": []}')

    async with _client(handler) as http:
        look = ArtLookup(http)
        look.cached("Nobody", "Nothing")
        await asyncio.sleep(0.05)
        assert look.cached("Nobody", "Nothing") is None
        await asyncio.sleep(0.05)
        assert calls["n"] == 1


@pytest.mark.parametrize("backlog", [0, 99])
def test_slow_client_gets_the_newest_snapshot(backlog: int) -> None:
    """A full event queue drops the stale backlog, never the newest state."""

    engine = Engine.__new__(Engine)
    engine._listeners = set()
    engine._last_broadcast = None
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=100)
    for i in range(backlog):
        queue.put_nowait(f"old-{i}")
    engine._listeners.add(queue)
    events = ["rooms", "np-new-song", "groups"]
    engine.snapshot_events = lambda: events  # type: ignore[method-assign]
    engine.broadcast_if_changed()
    items = [queue.get_nowait() for _ in range(queue.qsize())]
    assert items[-3:] == events

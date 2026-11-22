"""Sonos speakers as FreeSounder rooms (0.6.20)."""

from __future__ import annotations

import httpx
import pytest

from freesounderd.sonos import SonosManager, parse_didl, radio_uri

from .conftest import Lan, settle
from .fake_sonos import FakeSonos
from .test_daemon import setup_household

KITCHEN, DEN = "RINCON_K1400", "RINCON_D1400"


async def _with_sonos(lan: Lan, make_player):
    (a,) = await setup_household(make_player, ("10.0.0.1", "Pi 5"))
    fake = FakeSonos({"10.0.0.50": (KITCHEN, "Sonos Kitchen"), "10.0.0.51": (DEN, "Den")})

    async def discover() -> set[str]:
        return {"10.0.0.50"}

    mgr = SonosManager(
        httpx.AsyncClient(transport=httpx.MockTransport(fake.handle)), discover=discover
    )
    mgr.on_change = a.engine.broadcast_if_changed
    a.engine.sonos = mgr
    await mgr.refresh(force=True)
    await settle(lan, [a])
    return a, fake, mgr


def test_radio_uri_and_metadata() -> None:
    assert radio_uri("http://ice.example/fm.mp3") == "x-rincon-mp3radio://ice.example/fm.mp3"
    assert radio_uri("https://ice.example/fm") == "https://ice.example/fm"
    meta = (
        '<DIDL-Lite xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" '
        'xmlns:r="urn:schemas-rinconnetworks-com:metadata-1-0/" '
        'xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/"><item id="1">'
        "<dc:title>x</dc:title><r:streamContent>Band - Song</r:streamContent>"
        "<upnp:albumArtURI>/getaa?u=1</upnp:albumArtURI></item></DIDL-Lite>"
    )
    np = parse_didl(meta, "http://10.0.0.50:1400")
    assert (np["artist"], np["title"]) == ("Band", "Song")
    assert np["image_url"] == "http://10.0.0.50:1400/getaa?u=1"


async def test_sonos_speakers_are_rooms_and_obey(lan: Lan, make_player) -> None:
    a, fake, _ = await _with_sonos(lan, make_player)
    rooms = {r["name"]: r for r in (await a.client.get("/api/v2/rooms")).json()}
    kitchen = rooms["Sonos Kitchen"]
    assert kitchen["kind"] == "sonos" and kitchen["url"] is None and kitchen["volume"] == 20
    assert "Den" in rooms
    rid = kitchen["id"]
    # Volume and mute.
    assert (
        await a.client.post(f"/api/v2/rooms/{rid}/volume", json={"volume": 35})
    ).status_code == 200
    assert fake.by_ip["10.0.0.50"]["volume"] == 35
    await a.client.post(f"/api/v2/rooms/{rid}/volume", json={"muted": True})
    assert fake.by_ip["10.0.0.50"]["muted"] is True
    # Radio plays on it as a live stream with the station name.
    res = await a.client.post(
        f"/api/v2/groups/{rid}/radio", json={"url": "http://ice.example/fm.mp3", "name": "FM One"}
    )
    assert res.status_code == 200, res.text
    t = fake.transport[KITCHEN]
    assert t["uri"] == "x-rincon-mp3radio://ice.example/fm.mp3" and t["state"] == "PLAYING"
    await a.engine.sonos.refresh()
    np = (await a.client.get("/api/v2/now-playing", params={"player_id": rid})).json()
    assert np["state"] == "playing" and np["title"] == "FM One" and np["source"] == "sonos"
    # Pause / play.
    assert (await a.client.post(f"/api/v2/groups/{rid}/pause")).status_code == 200
    assert t["state"] == "PAUSED_PLAYBACK"
    # Spotify is not played through FreeSounder on Sonos: a clear answer.
    res = await a.client.post(f"/api/v2/groups/{rid}/spotify/play", json={"uri": "spotify:album:x"})
    assert res.status_code == 409 and "Spotify app" in res.json()["detail"]


async def test_sonos_groups_only_with_sonos(lan: Lan, make_player) -> None:
    a, fake, mgr = await _with_sonos(lan, make_player)
    k, d = "sonos-" + KITCHEN, "sonos-" + DEN
    res = await a.client.put(f"/api/v2/groups/{k}/members", json={"members": [k, d]})
    assert res.status_code == 200, res.text
    assert fake.coord[DEN] == KITCHEN
    rooms = {r["id"]: r for r in (await a.client.get("/api/v2/rooms")).json()}
    assert rooms[d]["group_id"] == k and rooms[k]["group_name"] == "Sonos Kitchen + 1"
    # Mixed with a FreeSounder player: refused, with the reason.
    res = await a.client.put(f"/api/v2/groups/{k}/members", json={"members": [k, a.id]})
    assert res.status_code == 409 and "only with other Sonos" in res.json()["detail"]
    # Ungroup.
    res = await a.client.put(f"/api/v2/groups/{k}/members", json={"members": [k]})
    assert res.status_code == 200 and fake.coord[DEN] == DEN


async def test_library_album_plays_through_the_sonos_queue(lan: Lan, make_player) -> None:
    from .test_music_sources import _library_on

    a, fake, _ = await _with_sonos(lan, make_player)
    await _library_on(a, lan)
    await settle(lan, [a])
    tracks = (await a.client.get("/api/v2/library/album", params={"album": "Album X"})).json()
    rid = "sonos-" + KITCHEN
    res = await a.client.post(
        f"/api/v2/groups/{rid}/library/play", json={"tracks": tracks, "start": 1}
    )
    assert res.status_code == 200, res.text
    t = fake.transport[KITCHEN]
    assert len(t["queue"]) == len(tracks) and t["track"] == 1 and t["state"] == "PLAYING"
    assert t["uri"] == f"x-rincon-queue:{KITCHEN}#0"
    assert t["queue"][0]["uri"].startswith("http://10.0.0.1:8080/api/v2/library/file?")


async def test_unreachable_sonos_is_shown_offline(lan: Lan, make_player) -> None:
    a, fake, mgr = await _with_sonos(lan, make_player)
    fake.by_ip.clear()  # whole Sonos system off the network
    await mgr.refresh()
    rooms = {r["name"]: r for r in (await a.client.get("/api/v2/rooms")).json()}
    assert rooms["Den"]["available"] is False
    res = await a.client.post(f"/api/v2/groups/{rooms['Den']['id']}/play")
    assert res.status_code == 503


@pytest.mark.parametrize("hms,secs", [("0:03:05", 185.0), ("NOT_IMPLEMENTED", None), ("", None)])
def test_time_parsing(hms, secs) -> None:
    from freesounderd.sonos import _secs

    assert _secs(hms) == secs

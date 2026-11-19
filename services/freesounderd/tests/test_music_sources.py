"""0.6.12 sources: music library (SMB/NFS/USB via MPD), podcasts, UPnP/DLNA."""

from __future__ import annotations

import os
import stat
from urllib.parse import parse_qs, urlparse

from freesounderd.library import friendly_mount_error, mount_argv
from freesounderd.podcasts import merge_docs, parse_duration, parse_feed
from freesounderd.upnp import parse_didl, render_conf

from .conftest import Lan, settle
from .test_daemon import setup_household

ALBUM = [
    {
        "file": f"smb-abc123/Artist A/Album X/0{i} Song {i}.flac",
        "Title": f"Song {i}",
        "Artist": "Artist A",
        "AlbumArtist": "Artist A",
        "Album": "Album X",
        "Track": str(i),
        "duration": "200.0",
    }
    for i in (2, 1, 3)
] + [
    {
        "file": "smb-abc123/Other/Live/01 Intro.mp3",
        "Title": "Intro",
        "Artist": "Band B",
        "AlbumArtist": "Band B",
        "Album": "Live",
        "Track": "1/10",
        "duration": "61.5",
    }
]

FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
<channel><title>Morning Show</title><itunes:author>Host</itunes:author>
<itunes:image href="https://pods.example/cover.jpg"/>
<description>&lt;p&gt;Daily news&lt;/p&gt;</description>
<item><title>Ep 2</title><guid>ep-2</guid><pubDate>Tue, 17 Nov 2026 06:00:00 +0000</pubDate>
<itunes:duration>1:02:03</itunes:duration>
<enclosure url="https://pods.example/ep2.mp3" type="audio/mpeg" length="1"/></item>
<item><title>Ep 1</title><guid>ep-1</guid><itunes:duration>1800</itunes:duration>
<enclosure url="https://pods.example/ep1.mp3" type="audio/mpeg" length="1"/></item>
<item><title>No audio</title><guid>x</guid></item>
</channel></rss>"""


async def _library_on(player, lan: Lan) -> str:
    res = await player.client.post(
        "/api/v2/library/shares",
        json={
            "kind": "smb",
            "host": "nas.local",
            "share": "Music",
            "username": "me",
            "password": "s3cret!pw",
        },
    )
    assert res.status_code == 200, res.text
    assert "s3cret" not in res.text
    sid = res.json()["share"]["id"]
    player.mpd.db = [{**r, "file": r["file"].replace("smb-abc123", sid)} for r in ALBUM]
    return sid


# --------------------------------------------------------------------- units
def test_mount_commands_keep_the_password_out_of_argv(tmp_path) -> None:
    share = {"id": "smb-1", "kind": "smb", "host": "nas", "share": "Music", "username": "me"}
    argv = mount_argv(share, tmp_path / "m", tmp_path / "c")
    assert argv[:4] == ["mount", "-t", "cifs", "//nas/Music"]
    assert f"credentials={tmp_path / 'c'}" in argv[-1] and "ro," in argv[-1]
    guest = mount_argv({**share, "username": None}, tmp_path / "m", tmp_path / "c")
    assert "guest" in guest[-1] and "credentials" not in guest[-1]
    nfs = mount_argv(
        {"id": "n", "kind": "nfs", "host": "nas", "share": "volume1/music"}, tmp_path, tmp_path
    )
    assert nfs[3] == "nas:/volume1/music"
    usb = mount_argv({"id": "u", "kind": "usb", "uuid": "1234-ABCD"}, tmp_path, tmp_path)
    assert usb[-2] == "UUID=1234-ABCD"
    assert "password" in friendly_mount_error("mount error(13): Permission denied")


def test_feed_parsing_and_merge() -> None:
    feed = parse_feed("https://pods.example/feed.xml", FEED)
    assert feed["title"] == "Morning Show" and feed["author"] == "Host"
    assert [e["title"] for e in feed["episodes"]] == ["Ep 2", "Ep 1"]
    assert feed["episodes"][0]["duration"] == 3723 and feed["episodes"][0]["date"]
    assert feed["episodes"][0]["image"] == "https://pods.example/cover.jpg"
    assert feed["description"] == "Daily news"
    assert parse_duration("45:00") == 2700 and parse_duration("bad") is None
    evil = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><rss><channel/></rss>'
    try:
        parse_feed("u", evil)
        raise AssertionError("entity feed accepted")
    except Exception as exc:  # noqa: BLE001
        assert "plain" in str(exc)
    a = {"subs_rev": 1, "subs": [{"feed": "a"}], "pos": {"k": {"p": 10, "t": 5}}}
    b = {
        "subs_rev": 2,
        "subs": [{"feed": "b"}],
        "pos": {"k": {"p": 3, "t": 1}, "j": {"p": 1, "t": 9}},
    }
    m = merge_docs(a, b)
    assert m["subs"] == [{"feed": "b"}] and m["pos"]["k"]["p"] == 10 and "j" in m["pos"]


def test_upnp_conf_and_didl() -> None:
    conf = render_conf(name="Kitchen + 1\nx", cache_dir="/run/x")
    assert "friendlyname = Kitchen + 1 x" in conf and "openhome = 1" in conf
    didl = (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/"><item id="1">'
        "<dc:title>Track</dc:title><upnp:artist>Band</upnp:artist><upnp:album>LP</upnp:album>"
        "<upnp:albumArtURI>http://cdn/x.jpg</upnp:albumArtURI>"
        '<res duration="0:03:30.000">http://x</res></item></DIDL-Lite>'
    )
    info = parse_didl(didl)
    assert info == {
        "title": "Track",
        "artist": "Band",
        "album": "LP",
        "image_url": "http://cdn/x.jpg",
        "duration": 210.0,
    }
    assert parse_didl("<!DOCTYPE x><a/>") == {}


# --------------------------------------------------------------------- library
async def test_add_share_browse_and_password_stays_private(lan: Lan, make_player) -> None:
    (a,) = await setup_household(make_player, ("10.0.0.1", "Pi 5"))
    sid = await _library_on(a, lan)
    cred = a.settings.state_dir / "library" / f"{sid}.cred"
    assert "password=s3cret!pw" in cred.read_text()
    assert stat.S_IMODE(os.stat(cred).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(a.settings.state_dir / "library.json").st_mode) == 0o600
    assert "s3cret" not in (a.settings.state_dir / "library.json").read_text()
    summary = (await a.client.get("/api/v2/library")).json()
    assert summary["shares"][0]["has_password"] is True and summary["stats"]["songs"] == 4
    assert "s3cret" not in str(summary)
    assert (await a.client.get("/api/v2/library/artists")).json() == ["Artist A", "Band B"]
    albums = (await a.client.get("/api/v2/library/albums")).json()
    assert {"album": "Album X", "artist": "Artist A"} in albums
    tracks = (
        await a.client.get(
            "/api/v2/library/album", params={"album": "Album X", "artist": "Artist A"}
        )
    ).json()
    assert [t["title"] for t in tracks] == ["Song 1", "Song 2", "Song 3"]
    top = (await a.client.get("/api/v2/library/folder")).json()
    assert top["folders"] == [{"path": sid, "name": "Music"}]
    found = (await a.client.get("/api/v2/library/search", params={"q": "band"})).json()
    assert found["artists"] == ["Band B"] and found["tracks"][0]["title"] == "Intro"
    bad = await a.client.post(
        "/api/v2/library/shares", json={"kind": "smb", "host": "x;rm", "share": "a"}
    )
    assert bad.status_code == 400
    dup = await a.client.post(
        "/api/v2/library/shares", json={"kind": "smb", "host": "nas.local", "share": "Music"}
    )
    assert dup.status_code == 409
    assert (await a.client.delete(f"/api/v2/library/shares/{sid}")).status_code == 200
    assert not cred.exists()


async def test_library_plays_on_another_group_by_signed_url(lan: Lan, make_player) -> None:
    a, b = await setup_household(make_player, ("10.0.0.1", "Pi 5"), ("10.0.0.2", "Pi 4"))
    await _library_on(a, lan)
    await settle(lan, [a, b])
    # Browsing from Living (no shares) is answered by Kitchen, the library player.
    assert (await b.client.get("/api/v2/library/artists")).json() == ["Artist A", "Band B"]
    tracks = (await b.client.get("/api/v2/library/album", params={"album": "Album X"})).json()
    res = await b.client.post(
        f"/api/v2/groups/{b.id}/library/play",
        json={"tracks": tracks, "start": 1, "context_name": "Album X"},
    )
    assert res.status_code == 200, res.text
    await settle(lan, [a, b])
    assert b.engine.source == "library" and a.engine.source == "none"
    assert b.mpd.state == "play" and b.mpd.pos == 1
    url = urlparse(b.mpd.queue[1])
    assert url.netloc == "10.0.0.1:8080" and url.path == "/api/v2/library/file"
    np = (await b.client.get("/api/v2/now-playing", params={"player_id": b.id})).json()
    assert np["source"] == "library" and np["title"] == "Song 2" and np["artist"] == "Artist A"
    assert np["image_url"].startswith("http://10.0.0.1:8080/api/v2/library/art?uri=")
    # The stream URL is a capability for that one file: a changed uri is refused.
    q = parse_qs(url.query)
    other = await a.client.get(
        "/api/v2/library/file", params={"uri": "smb-abc123/../../etc/shadow", "sig": q["sig"][0]}
    )
    assert other.status_code == 403
    # Pause / next go to MPD.
    assert (await b.client.post(f"/api/v2/groups/{b.id}/pause")).status_code == 200
    assert b.mpd.state == "pause"
    await settle(lan, [a, b])
    assert (await b.client.get("/api/v2/now-playing", params={"player_id": b.id})).json()[
        "state"
    ] == "paused"
    await b.client.post(f"/api/v2/groups/{b.id}/play")
    await b.client.post(f"/api/v2/groups/{b.id}/next")
    assert b.mpd.pos == 2 and b.mpd.state == "play"
    # Radio takes over: MPD stops.
    await b.client.post(f"/api/v2/groups/{b.id}/radio", json={"url": "http://r/s", "name": "R"})
    assert b.mpd.state == "stop" and b.engine.source == "radio"


async def test_library_file_serves_the_file(lan: Lan, make_player) -> None:
    (a,) = await setup_household(make_player, ("10.0.0.1", "Pi 5"))
    sid = await _library_on(a, lan)
    song = a.settings.music_dir / sid / "x.flac"
    song.parent.mkdir(parents=True, exist_ok=True)
    song.write_bytes(b"fLaC" + b"\0" * 100)
    url, art = a.engine.library_urls(a.id, f"{sid}/x.flac")
    path = url.split("8080", 1)[1]
    res = await a.client.get(path)
    assert res.status_code == 200 and res.content.startswith(b"fLaC")
    part = await a.client.get(path, headers={"Range": "bytes=0-3"})
    assert part.status_code == 206 and part.content == b"fLaC"
    a.mpd.pictures[f"{sid}/x.flac"] = b"\xff\xd8jpeg"
    cover = await a.client.get(art.split("8080", 1)[1])
    assert cover.status_code == 200 and cover.content == b"\xff\xd8jpeg"
    (song.parent / "folder.jpg").write_bytes(b"\xff\xd8folder")
    a.engine.library._art.clear()
    assert (
        await a.client.get("/api/v2/library/art", params={"uri": f"{sid}/y.flac"})
    ).content == b"\xff\xd8folder"


# --------------------------------------------------------------------- upnp
async def test_upnp_controller_takes_over_and_drives_volume(lan: Lan, make_player) -> None:
    (a,) = await setup_household(make_player, ("10.0.0.1", "Pi 5"))
    await settle(lan, [a])
    await a.client.post(f"/api/v2/groups/{a.id}/radio", json={"url": "http://r/s", "name": "R"})
    await settle(lan, [a])
    assert a.engine.source == "radio"
    a.upnp_meta = {"title": "Cast", "artist": "Phone", "image_url": "http://cdn/a.jpg"}
    a.mpd.volume = 77  # the controller's stored level: not applied on connect
    a.mpd.external_play("http://tidal.example/track.flac")
    await settle(lan, [a])
    assert a.engine.source == "upnp" and not a.engine.radio.running
    np = (await a.client.get("/api/v2/now-playing", params={"player_id": a.id})).json()
    assert (np["source"], np["title"], np["artist"]) == ("upnp", "Cast", "Phone")
    group_volume = a.engine.groups()[0]["volume"]
    assert a.mpd.volume == group_volume
    a.mpd.volume = 20  # the controller's slider moved
    await settle(lan, [a])
    assert a.engine.groups()[0]["volume"] == 20
    # The app pauses it like any source.
    await a.client.post(f"/api/v2/groups/{a.id}/pause")
    assert a.mpd.state == "pause"
    snap = lan.snapservers["10.0.0.1"]
    assert any(
        c == ("Group.SetStream", {"id": g["id"], "stream_id": "Media"})
        for c in snap.calls
        for g in snap.groups
    )


async def test_upnp_replacing_our_library_queue_is_detected(lan: Lan, make_player) -> None:
    (a,) = await setup_household(make_player, ("10.0.0.1", "Pi 5"))
    await _library_on(a, lan)
    await settle(lan, [a])
    tracks = (await a.client.get("/api/v2/library/album", params={"album": "Live"})).json()
    await a.client.post(f"/api/v2/groups/{a.id}/library/play", json={"tracks": tracks})
    await settle(lan, [a])
    assert a.engine.source == "library"
    a.mpd.external_play("http://phone/song.mp3")
    await settle(lan, [a])
    assert a.engine.source == "upnp"
    np = a.engine.np
    assert np["title"] == "song.mp3"


# --------------------------------------------------------------------- podcasts
async def test_podcast_search_subscribe_sync_and_resume(lan: Lan, make_player) -> None:
    a, b = await setup_household(make_player, ("10.0.0.1", "Pi 5"), ("10.0.0.2", "Pi 4"))
    lan.itunes = [
        {
            "collectionName": "Morning Show",
            "artistName": "Host",
            "feedUrl": "http://pods.example/feed.xml",
            "artworkUrl600": "https://pods.example/600.jpg",
        },
        {"collectionName": "No feed"},
    ]
    lan.feeds["pods.example"] = FEED
    found = (await a.client.get("/api/v2/podcasts/search", params={"q": "morning"})).json()
    assert [f["title"] for f in found] == ["Morning Show"]
    res = await a.client.post("/api/v2/podcasts/subscriptions", json=found[0])
    assert res.status_code == 200
    await settle(lan, [a, b])
    # Shared home setting: the other player has it too.
    subs = (await b.client.get("/api/v2/podcasts")).json()["subscriptions"]
    assert subs[0]["feed"] == "http://pods.example/feed.xml"
    feed = (await b.client.get("/api/v2/podcasts/feed", params={"url": subs[0]["feed"]})).json()
    assert feed["subscribed"] is True and len(feed["episodes"]) == 2
    ep = feed["episodes"][0]
    body = {"episode": ep, "podcast": {"title": feed["title"], "image": feed["image"]}}
    res = await b.client.post(f"/api/v2/groups/{b.id}/podcast/play", json=body)
    assert res.status_code == 200, res.text
    await settle(lan, [a, b])
    assert b.engine.source == "podcast" and b.mpd.queue == ["https://pods.example/ep2.mp3"]
    np = b.engine.np
    assert np["title"] == "Ep 2" and np["artist"] == "Morning Show"
    b.mpd.elapsed = 600.0
    await b.client.post(f"/api/v2/groups/{b.id}/pause")
    await settle(lan, [a, b])
    # The position reached Kitchen too; playing there resumes at 10:00.
    feed_a = (await a.client.get("/api/v2/podcasts/feed", params={"url": subs[0]["feed"]})).json()
    assert feed_a["episodes"][0]["position"] == 600.0
    res = await a.client.post(f"/api/v2/groups/{a.id}/podcast/play", json=body)
    assert res.json()["position"] == 600.0
    assert ["seek", "0", "600.0"] in a.mpd.calls
    # Not seekable: starts from the top instead of failing.
    a.mpd.fail_seek = True
    res = await a.client.post(f"/api/v2/groups/{a.id}/podcast/play", json=body)
    assert res.status_code == 200 and a.mpd.state == "play"
    # Seek in an episode.
    assert (
        await a.client.post(f"/api/v2/groups/{a.id}/seek", json={"position_ms": 90_000})
    ).status_code == 200
    assert a.mpd.elapsed == 90.0
    await a.client.request(
        "DELETE", "/api/v2/podcasts/subscriptions", params={"feed": subs[0]["feed"]}
    )
    await settle(lan, [a, b])
    assert (await b.client.get("/api/v2/podcasts")).json()["subscriptions"] == []


async def test_member_stops_renderer_and_media(lan: Lan, make_player) -> None:
    a, b = await setup_household(make_player, ("10.0.0.1", "Pi 5"), ("10.0.0.2", "Pi 4"))
    await settle(lan, [a, b])
    b.mpd.external_play("http://phone/x.mp3")
    await settle(lan, [a, b])
    assert b.engine.source == "upnp"
    res = await a.client.put(f"/api/v2/groups/{a.id}/members", json={"members": [a.id, b.id]})
    assert res.status_code == 200
    await settle(lan, [a, b])
    member = a if not a.engine.is_leader else b
    assert member.mpd.state == "stop"

"""freesounderd: setup, peers, grouping, leader handover, radio, volume."""

from __future__ import annotations

from freesounderd import peers
from freesounderd.engine import group_name, score
from freesounderd.snapcast import render_snapserver_conf
from freesounderd.spotify import render_config

from .conftest import Lan, age_out, introduce, settle


async def setup_household(make_player, *ips_models):
    players = [make_player(ip, model) for ip, model in ips_models]
    first, *rest = players
    res = await first.client.post("/api/v2/setup", json={"room": "Kitchen"})
    assert res.status_code == 200, res.text
    names = iter(["Living", "Office", "Bedroom"])
    for p in rest:
        res = await first.client.post(
            "/api/v2/players/adopt", json={"url": f"http://{p.ip}:8080", "room": next(names)}
        )
        assert res.status_code == 200, res.text
    introduce(players)
    return players


async def test_setup_creates_household_once(make_player) -> None:
    p = make_player("10.0.0.1")
    info = (await p.client.get("/api/v2/info")).json()
    assert info["state"] == "unclaimed" and info["household"] is None
    res = await p.client.post("/api/v2/setup", json={"room": "Kitchen"})
    assert res.status_code == 200
    body = res.json()
    assert body["state"] == "ready" and body["room"] == "Kitchen"
    assert "key" not in str(body["household"])
    again = await p.client.post("/api/v2/setup", json={"room": "Other"})
    assert again.status_code == 409
    health = (await p.client.get("/health")).json()
    assert health["state"] == "ready" and health["api"] == "v2"


async def test_adopt_shares_household_without_exposing_key(make_player) -> None:
    a, b = await setup_household(
        make_player, ("10.0.0.1", "Raspberry Pi 5"), ("10.0.0.2", "Raspberry Pi 4")
    )
    assert a.store.household().id == b.store.household().id
    assert a.store.household().key == b.store.household().key
    assert b.store.load_settings().name == "Living"
    hh = (await a.client.get("/api/v2/household")).json()
    assert "key" not in hh


async def test_peer_state_requires_household_signature(make_player) -> None:
    a, b = await setup_household(make_player, ("10.0.0.1", "Pi 5"), ("10.0.0.2", "Pi 4"))
    assert (await a.client.get("/api/v2/peer/state")).status_code == 403
    hh = a.store.household()
    headers = peers.sign(hh.key, hh.id, "GET", "/api/v2/peer/state")
    res = await a.client.get("/api/v2/peer/state", headers=headers)
    assert res.status_code == 200 and res.json()["id"] == a.id
    bad = peers.sign("00" * 32, hh.id, "GET", "/api/v2/peer/state")
    assert (await a.client.get("/api/v2/peer/state", headers=bad)).status_code == 403


async def test_rooms_join_and_leave(lan: Lan, make_player) -> None:
    a, b, c = await setup_household(
        make_player,
        ("10.0.0.1", "Raspberry Pi 5 Model B"),
        ("10.0.0.2", "Raspberry Pi 4 Model B"),
        ("10.0.0.3", "Raspberry Pi Zero 2 W"),
    )
    players = [a, b, c]
    await settle(lan, players)
    rooms = (await a.client.get("/api/v2/rooms")).json()
    assert sorted(r["name"] for r in rooms) == ["Kitchen", "Living", "Office"]
    assert all(not r["is_group"] for r in rooms)

    # Ask Kitchen's player to move Living (another player) into Kitchen's group.
    res = await a.client.post(f"/api/v2/rooms/{b.id}/join", json={"group": a.id})
    assert res.status_code == 200, res.text
    await settle(lan, players)
    assert b.engine.group["follow"] == a.id
    assert b.engine._snap_target == a.ip
    groups = (await c.client.get("/api/v2/groups")).json()
    kitchen = next(g for g in groups if g["leader_id"] == a.id)
    assert kitchen["name"] == "Kitchen + 1"
    assert kitchen["member_ids"] == [a.id, b.id]
    assert a.engine.my_group_name() == "Kitchen + 1"
    # Both clients end up in one Snapcast group on the leader.
    server = lan.snapservers[a.ip]
    main = [g for g in server.groups if len(g["clients"]) == 2]
    assert main, server.groups

    # Office joins via a room id in the group (Living), from Office's own player.
    res = await c.client.post(f"/api/v2/rooms/{c.id}/join", json={"room": b.id})
    assert res.status_code == 200
    await settle(lan, players)
    assert c.engine.group["follow"] == a.id
    assert a.engine.my_group_name() == "Kitchen + 2"

    # Living leaves: it becomes its own leader again.
    await b.client.post(f"/api/v2/rooms/{b.id}/leave")
    await settle(lan, players)
    assert b.engine.group["follow"] is None
    assert b.engine._snap_target == "127.0.0.1"
    assert a.engine.my_group_name() == "Kitchen + 1"


async def test_group_joining_another_group_flattens(lan: Lan, make_player) -> None:
    a, b, c = await setup_household(
        make_player, ("10.0.0.1", "Pi 5"), ("10.0.0.2", "Pi 4"), ("10.0.0.3", "Pi 3")
    )
    players = [a, b, c]
    await settle(lan, players)
    await a.client.post(f"/api/v2/rooms/{c.id}/join", json={"group": b.id})
    await settle(lan, players)
    assert c.engine.group["follow"] == b.id
    # Living (leading Office) joins Kitchen: Office follows to Kitchen.
    await a.client.put(f"/api/v2/groups/{a.id}/members", json={"members": [a.id, b.id]})
    await settle(lan, players, rounds=4)
    assert b.engine.group["follow"] == a.id
    assert c.engine.group["follow"] == a.id
    assert a.engine.my_group_name() == "Kitchen + 2"


async def test_leader_leaving_idle_group_hands_over(lan: Lan, make_player) -> None:
    a, b, c = await setup_household(
        make_player,
        ("10.0.0.1", "Raspberry Pi 5"),
        ("10.0.0.2", "Raspberry Pi Zero 2 W"),
        ("10.0.0.3", "Raspberry Pi 4"),
    )
    players = [a, b, c]
    await settle(lan, players)
    await a.client.put(f"/api/v2/groups/{a.id}/members", json={"members": [a.id, b.id, c.id]})
    await settle(lan, players)
    assert a.engine.my_group_name() == "Kitchen + 2"
    await a.client.post(f"/api/v2/rooms/{a.id}/leave")
    await settle(lan, players)
    # Pi 4 beats Zero 2 W, so Office leads the remaining group.
    assert c.engine.group["follow"] is None
    assert b.engine.group["follow"] == c.id
    assert c.engine.group["term"] >= 1
    assert a.engine.group["follow"] is None


async def test_leader_leaving_playing_group_relays(lan: Lan, make_player) -> None:
    a, b = await setup_household(make_player, ("10.0.0.1", "Pi 5"), ("10.0.0.2", "Pi 4"))
    players = [a, b]
    await settle(lan, players)
    await a.client.post(f"/api/v2/rooms/{b.id}/join", json={"group": a.id})
    await settle(lan, players)
    res = await a.client.post(
        f"/api/v2/groups/{a.id}/radio", json={"url": "http://radio.example/stream", "name": "R1"}
    )
    assert res.status_code == 200, res.text
    await settle(lan, players)
    assert a.engine.source == "radio"
    await a.client.post(f"/api/v2/rooms/{a.id}/leave")
    await settle(lan, players)
    assert a.engine.group["relay"] is True
    assert b.engine.group["follow"] == a.id
    rooms = {r["id"]: r for r in (await b.client.get("/api/v2/rooms")).json()}
    assert rooms[a.id]["group_id"] == a.id + ":solo"
    assert rooms[a.id]["relaying_for"] == "Living"
    assert rooms[b.id]["group_name"] == "Living"
    # The relaying leader's own speaker is muted.
    server = lan.snapservers[a.ip]
    assert server.clients[a.id]["config"]["volume"]["muted"] is True
    np = (await b.client.get("/api/v2/now-playing", params={"player_id": b.id})).json()
    assert np["source"] == "radio" and np["title"] == "R1"


async def test_failover_elects_best_member_and_resumes_radio(lan: Lan, make_player) -> None:
    a, b, c = await setup_household(
        make_player,
        ("10.0.0.1", "Raspberry Pi 5"),
        ("10.0.0.2", "Raspberry Pi 3"),
        ("10.0.0.3", "Raspberry Pi 4"),
    )
    players = [a, b, c]
    await settle(lan, players)
    await a.client.put(f"/api/v2/groups/{a.id}/members", json={"members": [a.id, b.id, c.id]})
    await a.client.post(
        f"/api/v2/groups/{a.id}/radio", json={"url": "http://radio.example/s", "name": "R"}
    )
    await settle(lan, players)
    lan.down.add(a.ip)
    for p in (b, c):
        p.engine._started -= 100
        age_out(p, a.id)
        p.engine._lost_since = 0.0001  # leader lost long ago
    await settle(lan, [b, c], rounds=3)
    assert c.engine.group["follow"] is None
    assert b.engine.group["follow"] == c.id
    assert c.engine.source == "radio"


async def test_room_and_group_volume(lan: Lan, make_player) -> None:
    a, b = await setup_household(make_player, ("10.0.0.1", "Pi 5"), ("10.0.0.2", "Pi 4"))
    players = [a, b]
    await settle(lan, players)
    await a.client.post(f"/api/v2/rooms/{b.id}/join", json={"group": a.id})
    await settle(lan, players)
    res = await a.client.post(f"/api/v2/rooms/{b.id}/volume", json={"volume": 70})
    assert res.status_code == 200
    assert b.engine.group["volume"] == 70
    await settle(lan, players, rounds=1)
    assert lan.snapservers[a.ip].clients[b.id]["config"]["volume"]["percent"] == 70
    # Group volume moves every room by the same amount.
    await a.client.post(f"/api/v2/rooms/{a.id}/volume", json={"volume": 30})
    await settle(lan, players, rounds=1)
    res = await b.client.post(f"/api/v2/groups/{a.id}/volume", json={"volume": 60})
    assert res.status_code == 200, res.text
    assert a.engine.group["volume"] == 40
    assert b.engine.group["volume"] == 80


async def test_volume_limit_scales_snapcast_volume(lan: Lan, make_player) -> None:
    (a,) = await setup_household(make_player, ("10.0.0.1", "Pi 5"))
    await a.client.patch(f"/api/v2/rooms/{a.id}/settings", json={"volume_limit": 50})
    await a.client.post(f"/api/v2/rooms/{a.id}/volume", json={"volume": 80})
    await settle(lan, [a], rounds=2)
    assert lan.snapservers[a.ip].clients[a.id]["config"]["volume"]["percent"] == 40


async def test_spotify_takes_over_from_radio(lan: Lan, make_player) -> None:
    (a,) = await setup_household(make_player, ("10.0.0.1", "Pi 5"))
    await a.client.post(
        f"/api/v2/groups/{a.id}/radio", json={"url": "http://radio.example/s", "name": "R"}
    )
    await settle(lan, [a], rounds=1)
    assert a.engine.source == "radio"
    lan.spotify[a.ip] = {
        "stopped": False,
        "paused": False,
        "track": {
            "name": "Song",
            "artist_names": ["Band"],
            "album_name": "LP",
            "album_cover_url": "http://img",
            "position": 1000,
            "duration": 200000,
        },
    }
    await settle(lan, [a], rounds=2)
    assert a.engine.source == "spotify"
    assert not a.engine.radio.running
    np = (await a.client.get(f"/api/v2/groups/{a.id}/now-playing")).json()
    assert np["title"] == "Song" and np["artist"] == "Band" and np["state"] == "playing"
    server = lan.snapservers[a.ip]
    group = next(g for g in server.groups if any(c["id"] == a.id for c in g["clients"]))
    assert group["stream_id"] == "Spotify"


async def test_settings_are_per_room_and_forwarded(lan: Lan, make_player) -> None:
    a, b = await setup_household(make_player, ("10.0.0.1", "Pi 5"), ("10.0.0.2", "Pi 4"))
    await settle(lan, [a, b])
    res = await a.client.patch(f"/api/v2/rooms/{b.id}/settings", json={"bass": 4})
    assert res.status_code == 200, res.text
    assert b.store.load_settings().bass == 4
    assert a.store.load_settings().bass == 0
    got = (await a.client.get(f"/api/v2/rooms/{b.id}/settings")).json()
    assert got["bass"] == 4 and got["name"] == "Living"


async def test_websocket_sends_rooms_snapshot(make_player) -> None:
    from starlette.testclient import TestClient

    p = make_player("10.0.0.9")
    with TestClient(p.app) as client, client.websocket_connect("/api/v2/ws") as ws:
        first = ws.receive_json()
        assert first["event"] == "rooms" and first["data"][0]["id"] == p.id


def test_helpers() -> None:
    assert group_name(["Kitchen"]) == "Kitchen"
    assert group_name(["Kitchen", "Living", "Office"]) == "Kitchen + 2"
    eth = {"id": "b", "net": "eth", "model": "Raspberry Pi Zero 2 W"}
    wifi = {"id": "a", "net": "wifi5", "model": "Raspberry Pi 5"}
    assert min([wifi, eth], key=score) is eth
    conf = render_snapserver_conf(
        run_dir="/run/freesounder",
        airplay_name="Kitchen + 1",
        shairport_bin="/usr/bin/shairport-sync",
        airplay_version=2,
    )
    assert "mdns_enabled = false" in conf
    assert "devicename=Kitchen%20%2B%201&port=7000" in conf
    assert "pipe:///run/freesounder/radio.fifo?name=Radio&mode=read" in conf
    assert "airplay" not in render_snapserver_conf(
        run_dir="/r", airplay_name="x", shairport_bin="/s", airplay_version=0
    )
    spot = render_config(device_name='Kitchen "+" 1', fifo="/run/freesounder/spotify.fifo")
    assert 'device_name: "Kitchen \\"+\\" 1"' in spot
    assert "audio_backend: pipe" in spot

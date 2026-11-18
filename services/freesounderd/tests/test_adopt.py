"""Adding second and third players: retries, mDNS-free peer discovery, gossip."""

from __future__ import annotations

import asyncio

from freesounderd.discovery import Found

from .conftest import Lan, settle


def _room_ids(player) -> set[str]:
    return {r["id"] for r in player.engine.rooms()}


async def test_second_player_is_seen_by_both_without_mdns(lan: Lan, make_player) -> None:
    a, b = make_player("10.0.0.1"), make_player("10.0.0.2")
    assert (await a.client.post("/api/v2/setup", json={"room": "Kitchen"})).status_code == 200
    body = {"ip": b.ip, "id": b.id, "room": "Lounge"}
    res = await a.client.post("/api/v2/players/adopt", json=body)
    assert res.status_code == 200, res.text
    # No introduce(): the players learn each other from the adopt itself.
    await settle(lan, [a, b])
    assert _room_ids(a) == {a.id, b.id}
    assert _room_ids(b) == {a.id, b.id}
    # ... and remember it across restarts.
    assert a.store.known_peers()[b.id] == ("10.0.0.2", 8080)
    assert b.store.known_peers()[a.id] == ("10.0.0.1", 8080)
    # The app connected to either player sees both rooms.
    for p in (a, b):
        rooms = (await p.client.get("/api/v2/rooms")).json()
        assert {r["id"] for r in rooms} == {a.id, b.id}


async def test_third_player_is_found_through_gossip(lan: Lan, make_player) -> None:
    a, b, c = make_player("10.0.0.1"), make_player("10.0.0.2"), make_player("10.0.0.3")
    await a.client.post("/api/v2/setup", json={"room": "Kitchen"})
    await a.client.post("/api/v2/players/adopt", json={"ip": b.ip, "room": "Lounge"})
    await settle(lan, [a, b])
    # Adopt the third through the *second* player: works from any member.
    res = await b.client.post("/api/v2/players/adopt", json={"ip": c.ip, "room": "Office"})
    assert res.status_code == 200, res.text
    await settle(lan, [a, b, c], rounds=4)
    for p in (a, b, c):
        assert _room_ids(p) == {a.id, b.id, c.id}, p.ip


async def test_adopt_waits_for_a_board_that_just_joined_wifi(lan: Lan, make_player) -> None:
    a, b = make_player("10.0.0.1"), make_player("10.0.0.2")
    a.settings.adopt_wait = 5
    await a.client.post("/api/v2/setup", json={"room": "Kitchen"})
    lan.down.add(b.ip)

    async def comes_up() -> None:
        await asyncio.sleep(0.5)
        lan.down.discard(b.ip)

    task = asyncio.create_task(comes_up())
    res = await a.client.post("/api/v2/players/adopt", json={"ip": b.ip, "room": "Lounge"})
    await task
    assert res.status_code == 200, res.text
    assert b.store.household().id == a.store.household().id


async def test_adopt_names_every_address_it_tried(lan: Lan, make_player) -> None:
    a = make_player("10.0.0.1")
    await a.client.post("/api/v2/setup", json={"room": "Kitchen"})
    res = await a.client.post("/api/v2/players/adopt", json={"ip": "10.0.0.9", "room": "Lounge"})
    assert res.status_code == 502
    assert "10.0.0.9:8080" in res.json()["detail"]


async def test_adopt_falls_back_to_the_mdns_address_for_that_player(lan: Lan, make_player) -> None:
    a, b = make_player("10.0.0.1"), make_player("10.0.0.2")
    await a.client.post("/api/v2/setup", json={"room": "Kitchen"})

    class FakeDiscovery:
        found = {"b": Found(name="b", ip=b.ip, port=8080, txt={"id": b.id, "state": "unclaimed"})}

        async def refresh(self) -> None:
            return None

    a.engine.discovery = FakeDiscovery()  # type: ignore[assignment]
    # The app only had a stale/wrong IP, but the player id matches mDNS.
    res = await a.client.post(
        "/api/v2/players/adopt", json={"ip": "10.0.0.77", "id": b.id, "room": "Lounge"}
    )
    assert res.status_code == 200, res.text
    assert b.store.household() is not None


async def test_board_already_in_another_home_is_refused_until_reset(lan: Lan, make_player) -> None:
    a, b = make_player("10.0.0.1"), make_player("10.0.0.2")
    await a.client.post("/api/v2/setup", json={"room": "Kitchen"})
    await b.client.post("/api/v2/setup", json={"room": "Lounge"})  # its own home by mistake
    res = await a.client.post("/api/v2/players/adopt", json={"ip": b.ip, "room": "Lounge"})
    assert res.status_code == 409 and "already set up" in res.json()["detail"]
    assert (await b.client.post("/api/v2/reset", json={"confirm": True})).status_code == 200
    assert b.store.known_peers() == {}
    res = await a.client.post("/api/v2/players/adopt", json={"ip": b.ip, "room": "Lounge"})
    assert res.status_code == 200, res.text

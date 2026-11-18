"""A home of up to 10 players: everyone sees everyone, all 10 group together."""

from __future__ import annotations

import time

from .conftest import Lan, age_out, settle

NAMES = [
    "Kitchen",
    "Lounge",
    "Office",
    "Bedroom",
    "Patio",
    "Study",
    "Garage",
    "Bath",
    "Hall",
    "Den",
]


async def ten_players(lan: Lan, make_player):
    models = ["Raspberry Pi 5 Model B", "Raspberry Pi 4 Model B", "Raspberry Pi Zero 2 W"]
    players = [make_player(f"10.0.0.{i + 1}", models[i % 3]) for i in range(10)]
    first = players[0]
    res = await first.client.post("/api/v2/setup", json={"room": NAMES[0]})
    assert res.status_code == 200, res.text
    for i, p in enumerate(players[1:], start=1):
        # Each new board is added through a different, already-joined member
        # (whichever player the app happens to be connected to). No mDNS.
        via = players[(i - 1) // 2]
        body = {"ip": p.ip, "id": p.id, "room": NAMES[i]}
        res = await via.client.post("/api/v2/players/adopt", json=body)
        assert res.status_code == 200, (i, res.text)
        await settle(lan, players[: i + 1], rounds=1)
    await settle(lan, players, rounds=4)
    return players


async def test_ten_players_all_visible_from_every_player(lan: Lan, make_player) -> None:
    players = await ten_players(lan, make_player)
    ids = {p.id for p in players}
    assert len({p.store.household().id for p in players}) == 1
    for p in players:
        rooms = (await p.client.get("/api/v2/rooms")).json()
        assert {r["id"] for r in rooms} == ids, p.ip
        assert sorted(r["name"] for r in rooms) == sorted(NAMES)
        assert all(r["available"] for r in rooms)


async def test_group_all_ten_and_fail_over(lan: Lan, make_player) -> None:
    players = await ten_players(lan, make_player)
    lead = players[0]
    everyone = [p.id for p in players]
    res = await players[5].client.put(
        f"/api/v2/groups/{lead.id}/members", json={"members": everyone}
    )
    assert res.status_code == 200, res.text
    await settle(lan, players, rounds=5)
    for p in players[1:]:
        assert p.engine.group["follow"] == lead.id, p.ip
        assert p.engine._snap_target == lead.ip
    assert lead.engine.my_group_name() == "Kitchen + 9"
    # One Snapcast group on the leader with all 10 clients (itself + 9).
    server = lan.snapservers[lead.ip]
    assert any(len(g["clients"]) == 10 for g in server.groups), server.groups
    groups = (await players[9].client.get("/api/v2/groups")).json()
    assert [len(g["member_ids"]) for g in groups] == [10]

    # Leader dies: one member takes over and the other eight follow it.
    lan.down.add(lead.ip)
    for p in players[1:]:
        p.engine._started -= 100
        age_out(p, lead.id)
        p.engine._lost_since = 0.0001  # leader lost long ago
    rest = players[1:]
    await settle(lan, rest, rounds=8)
    leaders = {p.engine.leader_id for p in rest}
    assert len(leaders) == 1, leaders
    new_lead = leaders.pop()
    assert new_lead != lead.id
    # Never a Zero 2 W when a Pi 4/5 is available.
    chosen = next(p for p in rest if p.id == new_lead)
    assert "Zero" not in chosen.engine._model


async def test_players_with_wrong_clocks_still_see_each_other(lan: Lan, make_player) -> None:
    """A reflashed Pi has no RTC: until NTP syncs its clock can be far off."""
    a, b = make_player("10.0.0.1"), make_player("10.0.0.2")
    b.engine.clock = lambda: time.time() - 3 * 86400
    await a.client.post("/api/v2/setup", json={"room": "Kitchen"})
    res = await a.client.post(
        "/api/v2/players/adopt", json={"ip": b.ip, "id": b.id, "room": "Patio"}
    )
    assert res.status_code == 200, res.text
    await settle(lan, [a, b], rounds=3)
    for p in (a, b):
        assert {r["id"] for r in p.engine.rooms()} == {a.id, b.id}, p.ip
    # Grouping (signed peer commands) works across the skew too.
    res = await a.client.post(f"/api/v2/rooms/{b.id}/join", json={"group": a.id})
    assert res.status_code == 200, res.text
    await settle(lan, [a, b])
    assert b.engine.group["follow"] == a.id


async def test_two_simultaneous_successors_merge(lan: Lan, make_player) -> None:
    players = await ten_players(lan, make_player)
    lead, x, y = players[0], players[3], players[6]
    everyone = [p.id for p in players]
    await lead.client.put(f"/api/v2/groups/{lead.id}/members", json={"members": everyone})
    await settle(lan, players, rounds=5)
    lan.down.add(lead.ip)
    # Both Pi 5s claimed the group in the same instant.
    for p in (x, y):
        p.engine._set_group(follow=None, term=9, relay=False, took_over=lead.id)
    rest = players[1:]
    for p in rest:
        p.engine._started -= 100
        age_out(p, lead.id)
        p.engine._lost_since = 0.0001
    await settle(lan, rest, rounds=8)
    assert len({p.engine.leader_id for p in rest}) == 1

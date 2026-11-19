"""Play after pause always plays, for every source (0.6.15 regression).

Reported on 0.6.15: pause was instant, but play then played ~2 s and
stopped, and nothing played again until the player was restarted.
"""

from __future__ import annotations

import asyncio
import time as _time
from typing import Any

import pytest

from freesounderd import engine as engine_mod
from freesounderd import radio as radio_mod

from .conftest import Lan, Player, settle
from .test_daemon import setup_household
from .test_pause_prompt import _group, _start, _state

SOURCES = ["spotify", "airplay", "radio", "upnp"]


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def monotonic(self) -> float:
        return self.t

    def time(self) -> float:
        return _time.time()


def realistic(lan: Lan, p: Player) -> None:
    """go-librespot and the AirPlay stream report what they were told."""

    orig = p.engine.spotify.command

    async def command(name: str, *args: Any, **kw: Any) -> Any:
        res = await orig(name, *args, **kw)
        st = lan.spotify.get(p.ip)
        if st:
            if name == "pause":
                st["paused"] = True
            elif name == "resume":
                st["paused"] = False
        return res

    p.engine.spotify.command = command  # type: ignore[method-assign]
    orig_sc = p.engine.snap_local.stream_control

    async def stream_control(sid: str, cmd: str) -> None:
        await orig_sc(sid, cmd)
        stream = next(x for x in lan.snapservers[p.ip].streams if x["id"] == sid)
        if cmd == "pause":
            stream["status"] = "idle"
        elif cmd == "play":
            stream["status"] = "playing"

    p.engine.snap_local.stream_control = stream_control  # type: ignore[method-assign]


def audible(source: str, lan: Lan, p: Player) -> str:
    g = _group(lan, p)
    if g.get("muted"):
        return "group muted"
    if lan.snapservers[p.ip].clients[p.id]["config"]["volume"].get("muted"):
        return "client muted"
    if g["stream_id"] != engine_mod.SOURCE_STREAM[source] or p.engine.source != source:
        return f"stream {g['stream_id']} / source {p.engine.source}"
    if source == "spotify" and lan.spotify[p.ip]["paused"]:
        return "spotify paused"
    if source == "airplay":
        stream = next(x for x in lan.snapservers[p.ip].streams if x["id"] == "AirPlay")
        if stream["status"] != "playing":
            return "airplay idle"
    if source == "radio" and not p.engine.radio.running:
        return "radio off"
    if source == "upnp" and p.mpd.state != "play":
        return f"mpd {p.mpd.state}"
    return "ok"


def mutes(lan: Lan, p: Player) -> list[bool]:
    return [c["mute"] for m, c in lan.snapservers[p.ip].calls if m == "Group.SetMute"]


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("gap", [0, 0.5, 1, 2, 2.9, 3.1, 5, 16])
async def test_pause_then_play_after_a_gap_then_play_again(
    source: str, gap: float, lan: Lan, make_player, monkeypatch
) -> None:
    clock = Clock()
    monkeypatch.setattr(engine_mod, "time", clock)
    (a,) = await setup_household(make_player, ("10.0.0.1", "Pi 5"))
    await settle(lan, [a])
    await _start(source, lan, a)
    realistic(lan, a)

    async def ticks(n: int, dt: float = 0.5) -> None:
        for _ in range(n):
            clock.t += dt
            await settle(lan, [a], rounds=1)

    assert (await a.client.post(f"/api/v2/groups/{a.id}/pause")).status_code == 200
    await ticks(int(gap / 0.5))
    clock.t += gap % 0.5
    assert (await a.client.post(f"/api/v2/groups/{a.id}/play")).status_code == 200
    assert _group(lan, a)["muted"] is False, "play unmutes at once"
    n = len(mutes(lan, a))
    seen = []
    for _ in range(40):  # 20 s: past every pause timer (3 s, 6 s, 15 s)
        await ticks(1)
        seen.append(audible(source, lan, a))
    assert set(seen) == {"ok"}, seen
    assert True not in mutes(lan, a)[n:], "nothing re-mutes after a play"
    assert await _state(a) == "playing"
    # Later: play again (no-op), then pause and play once more.
    assert (await a.client.post(f"/api/v2/groups/{a.id}/play")).status_code == 200
    await ticks(4)
    assert audible(source, lan, a) == "ok"
    await a.client.post(f"/api/v2/groups/{a.id}/pause")
    await ticks(2)
    await a.client.post(f"/api/v2/groups/{a.id}/play")
    await ticks(40)
    assert audible(source, lan, a) == "ok"
    assert await _state(a) == "playing"


async def test_a_slow_radio_pause_and_a_queued_pause_do_not_undo_play(
    lan: Lan, make_player
) -> None:
    """A radio pause that is slow to stop ffmpeg, a second pause queued behind
    it, then play: play wins, and the queued pause never runs after it."""

    (a,) = await setup_household(make_player, ("10.0.0.1", "Pi 5"))
    await settle(lan, [a])
    await _start("radio", lan, a)
    radio = a.engine.radio
    stops = []
    orig_stop = radio.stop

    async def slow_stop(**kw: Any) -> None:
        stops.append(kw)
        await asyncio.sleep(0.4)
        await orig_stop(**kw)

    radio.stop = slow_stop  # type: ignore[method-assign]
    url = f"/api/v2/groups/{a.id}"
    p1 = asyncio.create_task(a.client.post(f"{url}/pause"))
    await asyncio.sleep(0.05)
    p2 = asyncio.create_task(a.client.post(f"{url}/pause"))
    await asyncio.sleep(0.05)
    play = await a.client.post(f"{url}/play")
    assert play.status_code == 200
    await asyncio.gather(p1, p2)
    assert len(stops) == 1, "the queued pause was superseded by the play"
    assert radio.running and not radio.paused
    assert _group(lan, a)["muted"] is False
    assert await _state(a) == "playing"
    await settle(lan, [a])
    assert audible("radio", lan, a) == "ok"


async def test_a_slow_mute_cannot_land_after_the_play_unmute(lan: Lan, make_player) -> None:
    (a,) = await setup_household(make_player, ("10.0.0.1", "Pi 5"))
    await settle(lan, [a])
    await _start("spotify", lan, a)
    realistic(lan, a)
    orig = a.engine.snap_local.set_group_mute

    async def set_group_mute(gid: str, muted: bool) -> None:
        if muted:
            await asyncio.sleep(0.3)  # snapserver slow to take the pause mute
        await orig(gid, muted)

    a.engine.snap_local.set_group_mute = set_group_mute  # type: ignore[method-assign]
    pause = asyncio.create_task(a.client.post(f"/api/v2/groups/{a.id}/pause"))
    await asyncio.sleep(0.05)
    await a.client.post(f"/api/v2/groups/{a.id}/play")
    await pause
    assert _group(lan, a)["muted"] is False
    assert audible("spotify", lan, a) == "ok"


async def test_play_unmutes_a_stuck_group_without_waiting_for_a_tick(lan: Lan, make_player) -> None:
    (a,) = await setup_household(make_player, ("10.0.0.1", "Pi 5"))
    await settle(lan, [a])
    await _start("spotify", lan, a)
    realistic(lan, a)
    lan.spotify[a.ip]["paused"] = True
    await settle(lan, [a], rounds=1)
    _group(lan, a)["muted"] = True  # left muted by anything at all
    a.engine._tail_mute = {"gids": ["gone"], "at": 0.0, "source": "radio", "seen_paused": True}
    async with a.engine._lock:  # and the tick is busy
        res = await asyncio.wait_for(a.client.post(f"/api/v2/groups/{a.id}/play"), timeout=1)
    assert res.status_code == 200
    assert _group(lan, a)["muted"] is False and a.engine._tail_mute is None
    assert audible("spotify", lan, a) == "ok"


async def test_a_wedged_control_never_needs_a_restart(lan: Lan, make_player, monkeypatch) -> None:
    (a,) = await setup_household(make_player, ("10.0.0.1", "Pi 5"))
    await settle(lan, [a])
    await _start("spotify", lan, a)
    realistic(lan, a)
    await a.client.post(f"/api/v2/groups/{a.id}/pause")
    monkeypatch.setattr(engine_mod, "CONTROL_WAIT", 0.2)
    await a.engine._control_lock.acquire()  # something hung while holding it
    try:
        res = await asyncio.wait_for(a.client.post(f"/api/v2/groups/{a.id}/play"), timeout=2)
        assert res.status_code == 503, "busy answer, not a hung request"
        assert _group(lan, a)["muted"] is False, "unmuted even so"
    finally:
        a.engine._control_lock.release()
    assert (await a.client.post(f"/api/v2/groups/{a.id}/play")).status_code == 200
    assert audible("spotify", lan, a) == "ok"


async def test_closing_a_station_that_never_closes_is_bounded(monkeypatch) -> None:
    monkeypatch.setattr(radio_mod, "CLOSE_WAIT", 0.1)

    class Transport:
        aborted = False

        def abort(self) -> None:
            self.aborted = True

    class Writer:
        transport = Transport()

        def close(self) -> None:
            pass

        async def wait_closed(self) -> None:
            await asyncio.sleep(60)  # TLS close that never comes

    stream = radio_mod.IcyStream(asyncio.StreamReader(), Writer(), "200", {}, "https://r")  # type: ignore[arg-type]
    await asyncio.wait_for(stream.close(), timeout=1)
    assert Writer.transport.aborted


async def test_radio_stop_does_not_hang_on_a_stuck_stream_task(monkeypatch) -> None:
    monkeypatch.setattr(radio_mod, "TASK_WAIT", 0.1)
    player = radio_mod.RadioPlayer("ffmpeg", "/tmp/none.fifo", managed=False)
    player.station = {"url": "http://r/s", "name": "R"}

    release = asyncio.Event()

    async def stubborn() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            await release.wait()  # stuck in a close, ignoring the cancel

    task = asyncio.create_task(stubborn())
    player._pump = task
    await asyncio.sleep(0)
    await asyncio.wait_for(player.pause(), timeout=1)
    assert player.paused
    release.set()
    await task

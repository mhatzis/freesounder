"""Speaker Spotify sign-in with a spotify.com/pair code (0.6.19)."""

from __future__ import annotations

import asyncio
import json

from freesounderd.spotify import render_config

from .conftest import Lan, settle
from .test_daemon import setup_household


def test_device_auth_config_turns_zeroconf_off() -> None:
    conf = render_config(device_name="Lounge", fifo="/run/f", device_auth=True)
    assert "type: device_auth" in conf and "zeroconf_enabled: false" in conf
    normal = render_config(device_name="Lounge", fifo="/run/f")
    assert "type: zeroconf" in normal and "zeroconf_enabled: true" in normal
    assert "persist_credentials: true" in normal


class FakeLibrespot:
    def __init__(self) -> None:
        self.session: dict | None = None
        self.code: dict | None = None

    async def status(self):
        return self.session

    async def auth_code(self):
        return self.code


async def _pairing_player(lan: Lan, make_player, monkeypatch):
    (a,) = await setup_household(make_player, ("10.0.0.1", "Pi 5"))
    await settle(lan, [a])
    fake = FakeLibrespot()
    monkeypatch.setattr(a.engine.spotify, "status", fake.status)
    monkeypatch.setattr(a.engine.spotify, "auth_code", fake.auth_code)
    monkeypatch.setattr(a.engine, "SPOTIFY_PAIR_WAIT", 5.0)
    a.settings.manage_system = True
    state = a.settings.spotify_dir / "state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    old = {"username": "old", "data": "b2xk"}
    state.write_text(json.dumps({"device_id": "d1", "credentials": old}))
    return a, fake, state, old


async def test_pairing_shows_the_code_then_keeps_the_new_login(lan: Lan, make_player, monkeypatch):
    a, fake, state, _old = await _pairing_player(lan, make_player, monkeypatch)
    fake.code = {
        "code": "ABCD-EFGH",
        "url": "https://spotify.com/pair?code=ABCDEFGH",
        "expires_at": "x",
    }
    res = await a.client.post(f"/api/v2/groups/{a.id}/spotify/pair")
    assert res.status_code == 200, res.text
    info = res.json()
    assert info["pairing"]["code"] == "ABCD-EFGH" and info["signing_in"] is True
    conf = (a.settings.spotify_dir / "config.yml").read_text()
    assert "type: device_auth" in conf
    assert json.loads(state.read_text())["credentials"]["data"] is None, "old login set aside"
    # Approved on spotify.com/pair: go-librespot stores the new login.
    state.write_text(
        json.dumps({"device_id": "d1", "credentials": {"username": "me", "data": "bmV3"}})
    )
    fake.session = {"username": "me", "device_id": "d1"}
    await asyncio.wait_for(a.engine._spotify_pair_task, 5)
    info = (await a.client.post(f"/api/v2/groups/{a.id}/spotify")).json()
    assert info["pairing"] is None and info["signing_in"] is False and info["logged_in"]
    assert "type: zeroconf" in (a.settings.spotify_dir / "config.yml").read_text()
    assert json.loads(state.read_text())["credentials"]["username"] == "me"


async def test_cancelled_pairing_puts_the_old_login_back(lan: Lan, make_player, monkeypatch):
    a, fake, state, old = await _pairing_player(lan, make_player, monkeypatch)
    fake.code = {"code": "WXYZ", "url": "https://spotify.com/pair", "expires_at": None}
    assert (await a.client.post(f"/api/v2/groups/{a.id}/spotify/pair")).status_code == 200
    res = await a.client.post(f"/api/v2/groups/{a.id}/spotify/pair/cancel")
    assert res.status_code == 200 and res.json()["pairing"] is None
    assert json.loads(state.read_text())["credentials"] == old
    assert "type: zeroconf" in (a.settings.spotify_dir / "config.yml").read_text()


async def test_no_code_from_spotify_is_a_clear_error(lan: Lan, make_player, monkeypatch):
    a, fake, state, old = await _pairing_player(lan, make_player, monkeypatch)
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)
    res = await a.client.post(f"/api/v2/groups/{a.id}/spotify/pair")
    assert res.status_code == 502 and "pairing code" in res.json()["detail"]
    assert json.loads(state.read_text())["credentials"] == old


_real_sleep = asyncio.sleep


async def _fast_sleep(_seconds: float) -> None:
    await _real_sleep(0)

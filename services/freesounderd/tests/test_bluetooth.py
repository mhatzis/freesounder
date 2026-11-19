"""EXPERIMENTAL Bluetooth speaker output (exp/bt-transmitter), without hardware."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from freesounderd import bluetooth as bt
from freesounderd.bluetooth import BtError, BtOutput
from freesounderd.dsp import render_alsa_conf

from .conftest import Lan, settle
from .test_daemon import setup_household

SPEAKER = "AA:BB:CC:DD:EE:01"
PHONE = "AA:BB:CC:DD:EE:02"
LE_TAG = "AA:BB:CC:DD:EE:03"


class FakeBlueZ:
    """BlueZ + bluealsa as seen through BtOutput's Backend protocol."""

    def __init__(self) -> None:
        self.devs: dict[str, dict[str, Any]] = {}
        self.pcms: dict[str, str] = {}
        self.calls: list[tuple[str, str]] = []
        self.reachable = True
        self.has_adapter = True

    def add(self, mac: str, **kw: Any) -> None:
        self.devs[mac] = {
            "mac": mac,
            "name": kw.get("name", mac),
            "paired": kw.get("paired", False),
            "trusted": False,
            "connected": False,
            "rssi": kw.get("rssi"),
            "uuids": kw.get("uuids", []),
            "class": kw.get("cls"),
            "icon": kw.get("icon"),
            "le_only": kw.get("le_only", False),
        }

    async def adapter(self) -> dict[str, Any] | None:
        return (
            {"path": "/org/bluez/hci0", "address": "B8:27:EB:00:00:01"}
            if self.has_adapter
            else None
        )

    async def devices(self) -> list[dict[str, Any]]:
        return [dict(d) for d in self.devs.values()]

    async def discover(self, seconds: float) -> None:
        self.calls.append(("discover", str(seconds)))
        if SPEAKER not in self.devs:
            self.add(SPEAKER, name="Patio Boom", cls=0x240414, rssi=-50)

    async def pair(self, mac: str, wait: float) -> None:
        self.calls.append(("pair", mac))
        self.devs[mac]["paired"] = True

    async def set_trusted(self, mac: str, value: bool) -> None:
        self.calls.append(("trust", mac))
        self.devs[mac]["trusted"] = value

    async def connect(self, mac: str, wait: float) -> None:
        self.calls.append(("connect", mac))
        if not self.reachable:
            raise BtError("org.bluez.Error.Failed br-connection-page-timeout")
        self.devs[mac]["connected"] = True
        self.pcms[mac] = "SBC"

    async def disconnect(self, mac: str) -> None:
        self.calls.append(("disconnect", mac))
        if mac in self.devs:
            self.devs[mac]["connected"] = False
        self.pcms.pop(mac, None)

    async def remove(self, mac: str) -> None:
        self.calls.append(("remove", mac))
        self.devs.pop(mac, None)

    async def a2dp_pcms(self) -> dict[str, str]:
        return dict(self.pcms)

    def drop(self, mac: str) -> None:
        """The speaker went away (switched off / out of range)."""
        self.devs[mac]["connected"] = False
        self.pcms.pop(mac, None)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make_output(tmp_path: Path, model: str = "Raspberry Pi 5", net: str = "eth"):
    fake = FakeBlueZ()
    events: list[str] = []
    units: list[tuple[str, str]] = []
    clock = Clock()

    async def reselect() -> None:
        events.append("reselect")

    async def unit(action: str, name: str) -> None:
        units.append((action, name))

    out = BtOutput(
        fake,
        config_path=tmp_path / "bt-output",
        reselect=reselect,
        unit=unit,
        model=model,
        net=lambda: net,
        clock=clock,
    )
    return out, fake, events, units, clock


# ------------------------------------------------------------------ pure helpers
def test_mac_and_config_round_trip(tmp_path: Path) -> None:
    assert bt.norm_mac("aa-bb-cc-dd-ee-01") == SPEAKER
    with pytest.raises(ValueError):
        bt.norm_mac("AA:BB:CC:DD:EE")
    with pytest.raises(ValueError):
        bt.norm_mac('AA:BB:CC:DD:EE:01"\npcm.x {')
    path = tmp_path / "bt-output"
    assert bt.read_config(path)["latency_ms"] == bt.DEFAULT_LATENCY_MS
    bt.write_config(
        {"mac": SPEAKER, "name": 'Boom "$(reboot)"\nx', "enabled": True, "latency_ms": 180}, path
    )
    cfg = bt.read_config(path)
    assert cfg == {"mac": SPEAKER, "name": "Boom (reboot) x", "enabled": True, "latency_ms": 180}
    assert "$" not in path.read_text() and path.read_text().count("\n") == 4


def test_audio_sink_detection() -> None:
    assert bt.is_audio_sink({"uuids": [bt.A2DP_SINK_UUID]})
    assert bt.is_audio_sink({"class": 0x240414})  # loudspeaker
    assert bt.is_audio_sink({"icon": "audio-headphones"})
    assert not bt.is_audio_sink({"class": 0x5A020C})  # smartphone
    assert bt.mac_from_path("/org/bluealsa/hci0/dev_AA_BB_CC_DD_EE_01/a2dpsrc/sink") == SPEAKER


def test_shared_radio_warning() -> None:
    assert "share one 2.4 GHz radio" in bt.shared_radio_warning(
        "Raspberry Pi Zero 2 W Rev 1.0", "wifi24"
    )
    assert "share one" in bt.shared_radio_warning("Raspberry Pi 3 Model B Plus Rev 1.3", "eth")
    assert "5 GHz" in bt.shared_radio_warning("Raspberry Pi 5 Model B Rev 1.0", "wifi24")
    assert bt.shared_radio_warning("Raspberry Pi 5 Model B Rev 1.0", "wifi5") is None


# ------------------------------------------------------------------ manager
async def test_scan_lists_only_classic_speakers(tmp_path: Path) -> None:
    out, fake, _events, units, _ = make_output(tmp_path)
    fake.add(PHONE, name="Phone", cls=0x5A020C, rssi=-40)
    fake.add(LE_TAG, name="FS-1234", le_only=True, icon="audio-card")
    fake.add("AA:BB:CC:DD:EE:04", name="Old cans", paired=True, uuids=[bt.A2DP_SINK_UUID])
    found = await out.scan(4)
    assert [d["mac"] for d in found] == ["AA:BB:CC:DD:EE:04", SPEAKER]  # paired first
    assert ("start", bt.BLUEALSA_UNIT) in units


async def test_pair_connect_and_choose_as_output(tmp_path: Path) -> None:
    out, fake, events, _units, _ = make_output(tmp_path)
    status = await out.use(SPEAKER.lower(), None)
    assert [c[0] for c in fake.calls] == ["discover", "pair", "trust", "connect"]
    assert status["active"] and status["speaker"]["connected"] and status["codec"] == "SBC"
    assert status["experimental"] and status["latency_ms"] == 200
    assert bt.read_config(tmp_path / "bt-output")["name"] == "Patio Boom"
    assert "reselect" in events
    # Already paired: no second pairing on reconnect.
    fake.calls.clear()
    await out.use(SPEAKER)
    assert "pair" not in [c[0] for c in fake.calls]


async def test_disconnect_falls_back_then_reconnects_with_backoff(tmp_path: Path) -> None:
    out, fake, events, _units, clock = make_output(tmp_path)
    await out.use(SPEAKER)
    events.clear()
    fake.drop(SPEAKER)
    fake.reachable = False
    await out.check()  # notices the drop, tries once, re-picks the wired output
    assert not out.active() and events == ["reselect"]
    tries = sum(1 for c in fake.calls if c == ("connect", SPEAKER))
    await out.check()  # still inside the backoff window: no new page attempt
    assert sum(1 for c in fake.calls if c == ("connect", SPEAKER)) == tries
    clock.now += bt.RETRY_MIN
    await out.check()
    assert sum(1 for c in fake.calls if c == ("connect", SPEAKER)) == tries + 1
    assert out._retry == bt.RETRY_MIN * 4  # 10 -> 20 -> 40 s
    # The speaker comes back.
    fake.reachable = True
    clock.now += 60
    events.clear()
    await out.check()
    assert out.active() and events == ["reselect"]


async def test_speaker_reconnecting_by_itself_is_picked_up(tmp_path: Path) -> None:
    out, fake, events, _units, clock = make_output(tmp_path)
    await out.use(SPEAKER)
    fake.drop(SPEAKER)
    fake.reachable = False
    await out.check()
    events.clear()
    fake.devs[SPEAKER]["connected"] = True
    fake.pcms[SPEAKER] = "SBC"
    await out.check()
    assert out.active() and events == ["reselect"]


async def test_wired_choice_disconnects_and_forget_removes_bond(tmp_path: Path) -> None:
    out, fake, events, units, _ = make_output(tmp_path)
    await out.use(SPEAKER)
    status = await out.set_enabled(False)
    assert not status["active"] and ("disconnect", SPEAKER) in fake.calls
    assert not fake.devs[SPEAKER]["connected"]
    fake.calls.clear()
    await out.check()
    assert ("connect", SPEAKER) not in fake.calls, "no reconnects while wired is chosen"
    status = await out.forget()
    assert ("remove", SPEAKER) in fake.calls and status["speaker"] is None
    assert not (tmp_path / "bt-output").exists()
    assert units[-1] == ("stop", bt.BLUEALSA_UNIT)
    with pytest.raises(BtError):
        await out.set_enabled(True)


async def test_errors_are_reported_not_raised_raw(tmp_path: Path) -> None:
    out, fake, _e, _u, _ = make_output(tmp_path)
    fake.reachable = False
    with pytest.raises(BtError, match="connect: .*page-timeout"):
        await out.use(SPEAKER)
    assert "page-timeout" in (await out.status())["last_error"]
    assert not (tmp_path / "bt-output").exists()


# ------------------------------------------------------------------ ALSA / output pick
def test_alsa_chain_keeps_fsdsp_in_front_of_bluealsa() -> None:
    text = render_alsa_conf(slave_pcm=f"bluealsa:DEV={SPEAKER},PROFILE=a2dp", kind="bluetooth")
    dsp = text.index("pcm.freesounder_dsp")
    hw = text.index("pcm.freesounder_hw")
    assert dsp < hw and "type fsdsp" in text
    assert f'pcm "bluealsa:DEV={SPEAKER},PROFILE=a2dp"' in text
    main = text.split("pcm.freesounder_selftest")[0]
    # Pinned to the transport's params (default 44.1 kHz S16 stereo), and the
    # whole chain runs at that rate: no empty hw-params interval.
    hw_block = main[hw:]
    assert "format S16_LE" in hw_block and "rate 44100" in hw_block and "channels 2" in hw_block
    assert "rate 44100" in main[: main.index("pcm.freesounder_dsp")]


def test_alsa_chain_uses_transport_rate_and_direct_mode() -> None:
    bt = {"rate": "48000", "format": "S16_LE", "channels": "2", "codec": "SBC"}
    pcm = f"bluealsa:DEV={SPEAKER},PROFILE=a2dp"
    text = render_alsa_conf(slave_pcm=pcm, kind="bluetooth", bt=bt)
    assert "rate 48000" in text.split("pcm.freesounder_selftest")[0]
    direct = render_alsa_conf(slave_pcm=pcm, kind="bluetooth", bt={**bt, "direct": "1"})
    main = direct.split("pcm.freesounder_selftest")[0]
    assert "type fsdsp" not in main and "DIRECT" in main
    assert f'pcm "{pcm}"' in main and "rate 48000" in main
    # A hostile format value is not written into the ALSA config.
    odd = render_alsa_conf(slave_pcm=pcm, kind="bluetooth", bt={"format": 'S16"; x'})
    assert 'S16"' not in odd and "format S16_LE" in odd


SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "images/player/pi-gen/stage-freesounder/01-filesystem/files/usr/lib/freesounder"
    / "select-audio-output"
)


INFO_SBC = """Device: /org/bluez/hci0/dev_AA_BB_CC_DD_EE_01
Transport: A2DP-source
Mode: sink
Running: false
Format: S16_LE
Channels: 2
Rate: 48000 Hz
Available codecs: SBC
Selected codec: SBC
"""


def _select(
    tmp_path: Path, bt_output: str | None, pcms: str, info: str = INFO_SBC
) -> dict[str, str]:
    (tmp_path / "cards").write_text(" 0 [sndrpihifiberry]: HifiberryDacp - snd_rpi_hifiberry\n")
    if bt_output is not None:
        (tmp_path / "bt-output").write_text(bt_output)
    (tmp_path / "pcms").write_text(pcms)
    out = tmp_path / "audio-output"
    script = SCRIPT.read_text().replace("OUT=/etc/freesounder/audio-output", f"OUT={out}")
    env = {
        **os.environ,
        "FREESOUNDER_NO_RENDER": "1",
        "FREESOUNDER_ASOUND_CARDS": str(tmp_path / "cards"),
        "FREESOUNDER_DRM_DIR": str(tmp_path / "drm"),
        "FREESOUNDER_BT_OUTPUT": str(tmp_path / "bt-output"),
        "FREESOUNDER_BLUEALSA_PCMS": str(tmp_path / "pcms"),
        "FREESOUNDER_BLUEALSA_INFO": str(tmp_path / "info"),
        "FREESOUNDER_PLAYER_ENV": str(tmp_path / "player.env"),
        "FREESOUNDER_UPTIME": "999",
    }
    (tmp_path / "info").write_text(info)
    subprocess.run(["bash", "-c", script], check=True, env=env, capture_output=True)
    return dict(line.split("=", 1) for line in out.read_text().splitlines())


needs_bash = pytest.mark.skipif(
    not SCRIPT.is_file() or shutil.which("bash") is None, reason="needs repo + bash"
)
CONNECTED = "/org/bluealsa/hci0/dev_AA_BB_CC_DD_EE_01/a2dpsrc/sink\n"


@needs_bash
def test_select_picks_connected_speaker(tmp_path: Path) -> None:
    got = _select(tmp_path, f"mac={SPEAKER}\nname=Patio Boom\nenabled=1\n", CONNECTED)
    assert got["pcm"] == f"bluealsa:DEV={SPEAKER},PROFILE=a2dp"
    assert got["kind"] == "bluetooth" and "Patio Boom" in got["reason"]
    assert got["card"] == "0"


@needs_bash
def test_select_records_the_transport_params(tmp_path: Path) -> None:
    got = _select(tmp_path, f"mac={SPEAKER}\nname=Aiwa ASP-0217\nenabled=1\n", CONNECTED)
    assert got["rate"] == "48000" and got["format"] == "S16_LE" and got["channels"] == "2"
    assert got["codec"] == "SBC" and "SBC 48000 Hz" in got["reason"]
    # bluez-alsa 4.0 prints "Sampling:" instead of "Rate:".
    old_cli = INFO_SBC.replace("Rate: 48000 Hz", "Sampling: 44100 Hz")
    got = _select(tmp_path, f"mac={SPEAKER}\nenabled=1\n", CONNECTED, old_cli)
    assert got["rate"] == "44100"
    # Not configured yet: still picked, with safe defaults.
    got = _select(tmp_path, f"mac={SPEAKER}\nenabled=1\n", CONNECTED, "Format: S16_LE\n")
    assert got["kind"] == "bluetooth" and got["rate"] == "44100"


@needs_bash
def test_select_gives_snapclient_a_bigger_buffer_for_bluetooth(tmp_path: Path) -> None:
    _select(tmp_path, f"mac={SPEAKER}\nenabled=1\n", CONNECTED)
    env = (tmp_path / "player.env").read_text()
    assert env == 'SNAPCLIENT_PLAYER="--player alsa:buffer_time=400,fragments=8"\n'
    _select(tmp_path, f"mac={SPEAKER}\nenabled=0\n", CONNECTED)
    assert (tmp_path / "player.env").read_text() == "SNAPCLIENT_PLAYER=\n"


@needs_bash
def test_select_falls_back_to_wired_when_speaker_is_away(tmp_path: Path) -> None:
    got = _select(tmp_path, f"mac={SPEAKER}\nname=Patio Boom\nenabled=1\n", "")
    assert got["pcm"] == "hw:CARD=sndrpihifiberry,DEV=0" and got["kind"] == "card"
    assert "Patio Boom not connected" in got["reason"]


@needs_bash
def test_select_ignores_disabled_or_bad_config(tmp_path: Path) -> None:
    assert _select(tmp_path, f"mac={SPEAKER}\nenabled=0\n", CONNECTED)["kind"] == "card"
    bad = 'mac=AA:BB:CC:DD:EE:01",x\nenabled=1\n'
    assert _select(tmp_path, bad, CONNECTED)["kind"] == "card"
    assert _select(tmp_path, None, CONNECTED)["kind"] == "card"


# ------------------------------------------------------------------ engine + API
async def test_bluetooth_output_sets_snapcast_latency(lan: Lan, make_player, tmp_path) -> None:
    (a,) = await setup_household(make_player, ("10.0.0.1", "Pi 5"))
    out, fake, _e, _u, _ = make_output(tmp_path)
    a.engine.bt = out
    await settle(lan, [a])
    server = lan.snapservers[a.ip]
    assert not any(m == "Client.SetLatency" for m, _ in server.calls), "wired: left alone"
    await out.use(SPEAKER)
    (a.settings.config_dir / "audio-output").write_text(
        f"pcm=bluealsa:DEV={SPEAKER},PROFILE=a2dp\ncard=0\nkind=bluetooth\nreason=x\n"
    )
    await settle(lan, [a], rounds=1)
    assert server.clients[a.id]["config"]["latency"] == 200
    res = await a.client.post(f"/api/v2/rooms/{a.id}/bluetooth/latency", json={"latency_ms": 250})
    assert res.status_code == 200 and res.json()["latency_ms"] == 250
    await settle(lan, [a], rounds=1)
    assert server.clients[a.id]["config"]["latency"] == 250
    # Speaker gone: wired output again, no extra delay.
    (a.settings.config_dir / "audio-output").write_text("pcm=default:CARD=x\ncard=0\nkind=card\n")
    await settle(lan, [a], rounds=1)
    assert server.clients[a.id]["config"]["latency"] == 0


async def test_missing_adapter_is_brought_up_in_the_background(tmp_path: Path) -> None:
    """No adapter: status() answers at once with a reason and switches
    Bluetooth on in the background (rfkill unblock, start bluetooth.service),
    at most every 30 s. 0.6.13 did this inside the request."""
    import asyncio

    out, fake, _e, units, clock = make_output(tmp_path)
    gate = asyncio.Event()
    brought_up: list[str] = []

    async def bring_up() -> None:
        brought_up.append("up")
        await gate.wait()  # rfkill is slow
        fake.has_adapter = True

    out.bring_up = bring_up
    fake.has_adapter = False
    st = await asyncio.wait_for(out.status(), 1)  # does not wait for the bring-up
    assert st["available"] is False
    assert (
        "No Bluetooth adapter" in st["reason"] and "Trying to switch Bluetooth on" in st["reason"]
    )
    await asyncio.sleep(0)
    assert brought_up == ["up"]
    st = await asyncio.wait_for(out.status(), 1)  # one bring-up at a time
    assert brought_up == ["up"]
    gate.set()
    await out._bring_up_task
    assert ("start", "bluetooth.service") in units
    assert ("start", "hciuart.service") not in units
    st = await out.status()
    assert st["available"] is True and st["reason"] is None

    # Missing again: not retried within 30 s, then retried.
    fake.has_adapter = False
    gate.clear()
    gate.set()
    st = await out.status()
    assert st["available"] is False and brought_up == ["up"]
    clock.now += 31
    await out.status()
    await out._bring_up_task
    assert brought_up == ["up", "up"]
    await out.stop()


async def test_status_answers_fast_when_bluez_hangs_or_raises(tmp_path: Path, monkeypatch) -> None:
    import asyncio

    monkeypatch.setattr(bt, "ADAPTER_LOOKUP_WAIT", 0.05)
    out, fake, _e, _u, _c = make_output(tmp_path)

    async def hang() -> dict[str, Any] | None:
        await asyncio.sleep(3600)
        return None

    async def bring_up() -> None:
        return None

    out.bring_up = bring_up
    fake.adapter = hang  # type: ignore[method-assign]
    st = await asyncio.wait_for(out.status(), 1)
    assert st["available"] is False and "did not answer" in st["reason"]

    async def broken() -> dict[str, Any] | None:
        raise BtError("system D-Bus unavailable: no socket")

    fake.adapter = broken  # type: ignore[method-assign]
    st = await asyncio.wait_for(out.status(), 1)
    assert st["available"] is False and "BlueZ not reachable" in st["reason"]
    await out.stop()


async def test_bluetooth_api_always_answers_json(lan: Lan, make_player, tmp_path) -> None:
    """A status() that raises or hangs must never drop the HTTP request
    (the app saw "Connection reset by peer")."""
    import asyncio

    from freesounderd import app as app_mod

    (a,) = await setup_household(make_player, ("10.0.0.1", "Pi 5"))
    out, _fake, _e, _u, _ = make_output(tmp_path)
    a.engine.bt = out

    async def boom() -> dict[str, Any]:
        raise RuntimeError("kaboom")

    out.status = boom  # type: ignore[method-assign]
    res = await a.client.get(f"/api/v2/rooms/{a.id}/bluetooth")
    assert res.status_code == 200
    assert res.json()["available"] is False and "kaboom" in res.json()["reason"]

    async def slow() -> dict[str, Any]:
        await asyncio.sleep(3600)
        return {}

    out.status = slow  # type: ignore[method-assign]
    app_mod.BT_STATUS_WAIT = 0.05
    try:
        res = await a.client.get(f"/api/v2/rooms/{a.id}/bluetooth")
    finally:
        app_mod.BT_STATUS_WAIT = 6.0
    assert res.status_code == 200 and res.json()["available"] is False
    assert res.json()["reason"].startswith("Bluetooth status failed")


async def test_bluez_backend_reconnects_after_a_dropped_bus() -> None:
    backend = bt.BluezBackend()
    calls: list[str] = []

    class DeadBus:
        connected = True

        async def call(self, msg: Any) -> Any:
            raise EOFError("connection closed")

        def disconnect(self) -> None:
            calls.append("disconnect")

    backend._bus = DeadBus()
    backend._adapter_path = "/org/bluez/hci0"
    with pytest.raises(BtError):
        await backend.adapter()
    assert backend._bus is None and backend._adapter_path is None
    assert calls == ["disconnect"]


async def test_api_through_another_room(lan: Lan, make_player, tmp_path) -> None:
    a, b = await setup_household(make_player, ("10.0.0.1", "Pi 5"), ("10.0.0.2", "Pi Zero 2 W"))
    await settle(lan, [a, b])
    none = (await a.client.get(f"/api/v2/rooms/{a.id}/bluetooth")).json()
    assert none["available"] is False and none["experimental"] is True
    assert none["reason"]
    assert (await a.client.post(f"/api/v2/rooms/{a.id}/bluetooth/scan", json={})).status_code == 503
    out, fake, _e, _u, _ = make_output(tmp_path, model="Raspberry Pi Zero 2 W Rev 1.0")
    b.engine.bt = out
    res = await a.client.post(f"/api/v2/rooms/{b.id}/bluetooth/scan", json={"seconds": 3})
    assert res.status_code == 200 and res.json()["speakers"][0]["name"] == "Patio Boom"
    res = await a.client.post(f"/api/v2/rooms/{b.id}/bluetooth/connect", json={"mac": SPEAKER})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["speaker"]["connected"] and "share one 2.4 GHz radio" in body["warning"]
    res = await a.client.post(
        f"/api/v2/rooms/{b.id}/bluetooth/connect", json={"mac": "nope-nope-nope-no"}
    )
    assert res.status_code == 400
    res = await a.client.post(f"/api/v2/rooms/{b.id}/bluetooth/output", json={"enabled": False})
    assert res.status_code == 200 and res.json()["active"] is False
    res = await a.client.delete(f"/api/v2/rooms/{b.id}/bluetooth")
    assert res.status_code == 200 and res.json()["speaker"] is None


# ------------------------------------------------------------------ real D-Bus calls
@pytest.fixture
def private_bus(tmp_path: Path):
    if shutil.which("dbus-daemon") is None:
        pytest.skip("needs dbus-daemon")
    pytest.importorskip("dbus_next")
    proc = subprocess.Popen(
        ["dbus-daemon", "--session", "--nofork", "--print-address"],
        stdout=subprocess.PIPE,
        text=True,
    )
    address = proc.stdout.readline().strip()
    yield address
    proc.terminate()
    proc.wait(timeout=5)


async def test_bluez_backend_speaks_real_dbus(private_bus: str) -> None:
    """Signatures, the pairing agent and Pairable handling against a fake BlueZ."""

    from . import fake_bluez

    world = await fake_bluez.start(private_bus)
    backend = bt.BluezBackend(bus_address=private_bus)
    world.client_name = (await backend._conn()).unique_name
    adapter = await backend.adapter()
    assert adapter and adapter["address"] == "B8:27:EB:00:00:01" and adapter["pairable"] is False
    await backend.discover(0.05)
    assert ("filter", {"Transport": "bredr"}) in world.log, "classic only, never LE"
    devs = {d["mac"]: d for d in await backend.devices()}
    assert devs[SPEAKER]["name"] == "Patio Boom" and bt.is_audio_sink(devs[SPEAKER])
    await backend.pair(SPEAKER, wait=5)
    assert ("agent", "NoInputNoOutput") in world.log
    assert ("confirm", "METHOD_RETURN") in world.log
    assert ("other", "ERROR") in world.log, "the agent answers only the speaker being paired"
    assert ("Pairable", True) in world.log and world.log[-1] == ("Pairable", False)
    assert world.agent is None
    await backend.set_trusted(SPEAKER, True)
    await backend.connect(SPEAKER, wait=5)
    assert ("connect", bt.A2DP_SINK_UUID) in world.log
    assert await backend.a2dp_pcms() == {SPEAKER: "SBC"}
    devs = {d["mac"]: d for d in await backend.devices()}
    assert devs[SPEAKER]["paired"] and devs[SPEAKER]["trusted"] and devs[SPEAKER]["connected"]
    await backend.remove(SPEAKER)
    assert SPEAKER not in {d["mac"] for d in await backend.devices()}


async def test_bluetooth_api_through_another_room_when_the_owner_fails(
    lan: Lan, make_player, tmp_path
) -> None:
    """The phone may talk to a different player than the room's own (forwarded)."""
    a, b = await setup_household(make_player, ("10.0.0.1", "Pi 5"), ("10.0.0.2", "Pi Zero 2 W"))
    await settle(lan, [a, b])
    out, _fake, _e, _u, _ = make_output(tmp_path)
    b.engine.bt = out

    async def boom() -> dict[str, Any]:
        raise RuntimeError("bluez went away")

    out.status = boom  # type: ignore[method-assign]
    res = await a.client.get(f"/api/v2/rooms/{b.id}/bluetooth")
    assert res.status_code == 200
    assert res.json()["available"] is False and "bluez went away" in res.json()["reason"]


async def test_diagnostics_report_bluetooth_state(monkeypatch) -> None:
    from freesounderd import diagnostics

    async def fake_run(*cmd: str, wait: float = 8.0) -> str:
        if cmd[:2] == ("systemctl", "is-active"):
            return "inactive\n"
        if cmd[0] == "rfkill":
            return "0 bluetooth blocked unblocked\n"
        if "freesounderd.service" in cmd:
            return (
                "2026-11-20T01:00:00 pi systemd[1]: Started freesounderd.service\n"
                "2026-11-20T01:00:05 pi freesounderd[9]: bluetooth: No Bluetooth adapter\n"
                "2026-11-20T01:00:06 pi freesounderd[9]: GET /health 200\n"
                "2026-11-20T01:01:00 pi systemd[1]: Started freesounderd.service\n"
            )
        return ""

    monkeypatch.setattr(diagnostics, "_run", fake_run)
    info = await diagnostics.bluetooth_info()
    assert info["bt_bluetoothd"] == "inactive"
    assert info["bt_rfkill"] == ["0 bluetooth blocked unblocked"]
    assert info["daemon_starts_this_boot"] == 2
    assert any("No Bluetooth adapter" in ln for ln in info["bt_log"])
    assert not any("/health" in ln for ln in info["bt_log"])

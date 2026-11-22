"""Experimental feature (every player since 0.6.15): a Bluetooth speaker as this room's output.

The player is an A2DP *source*: bluealsa (bluez-alsa) encodes the room's audio
(SBC) and sends it to a paired speaker. The speaker is just another output
target for the `freesounder` ALSA chain, so fsdsp (tone, balance) and the
snapclient volume still apply, and Snapcast (as leader or follower) plays
through it:

    snapclient -> plug -> fsdsp -> plug -> bluealsa:DEV=<mac>,PROFILE=a2dp

select-audio-output picks the speaker while bluealsa has its A2DP PCM (the
speaker is connected). While it is away the room plays to the null sink, not
the wired output (a room uses Bluetooth or its DAC/HDMI, never both; 0.6.18),
and the speaker is tried every 10 s until it is back.

Coexistence with the Improv BLE setup service:
* only BR/EDR (classic) is used: discovery is filtered to Transport=bredr,
  bluealsa runs with the A2DP source profile only (no HFP/HSP, no LE audio),
* the adapter is made pairable only while the app pairs a speaker, and a
  pairing agent that answers only that one speaker is registered for that
  time; Pairable is restored afterwards (the BLE service keeps it off),
* nothing here touches LE advertising, the adapter's power or rfkill.

State lives in /etc/freesounder/bt-output (mac, name, enabled, latency_ms),
which select-audio-output reads too.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

BT_OUTPUT_FILE = Path("/etc/freesounder/bt-output")
BLUEALSA_UNIT = "bluealsa.service"
A2DP_SINK_UUID = "0000110b-0000-1000-8000-00805f9b34fb"
DEFAULT_LATENCY_MS = 200  # typical SBC speaker delay that is not reported to ALSA
MAX_LATENCY_MS = 1000
MAC_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")
BRING_UP_EVERY = 30.0  # s: how often a missing adapter triggers a bring-up attempt
# Only bluetoothd: hciuart / modprobe hci_uart re-attach the UART controller,
# which on the Pi's combined Wi-Fi + Bluetooth chip can disturb Wi-Fi.
CONTROLLER_UNIT = "bluetooth.service"
ADAPTER_LOOKUP_WAIT = 3.0  # s: GET /bluetooth answers fast even if BlueZ hangs
BRING_UP_STEP_WAIT = 20.0  # s: each background bring-up step
# A scan keeps discovering this much longer while a speaker still has no name:
# BlueZ only learns a name from the speaker's reply to a name request, which
# often lands after the first few seconds (the app then showed MACs).
NAME_WAIT = 8.0
NAME_ROUND = 2.0
# While Bluetooth is a room's output and the speaker is away, try to connect
# every 10 s until it is back (0.6.18: it backed off to 2 min). The room plays
# nothing meanwhile; it never falls back to the wired output.
RETRY_MIN = 10.0
RETRY_MAX = 10.0
WATCH_CONFIGURED = 5.0  # s between connection checks with a speaker set up
WATCH_IDLE = 30.0

BLUEZ = "org.bluez"
BLUEALSA = "org.bluealsa"
OM = "org.freedesktop.DBus.ObjectManager"
PROPS = "org.freedesktop.DBus.Properties"
ADAPTER = "org.bluez.Adapter1"
DEVICE = "org.bluez.Device1"


class BtError(RuntimeError):
    pass


def norm_mac(mac: str) -> str:
    value = (mac or "").strip().upper().replace("-", ":")
    if not MAC_RE.match(value):
        raise ValueError(f"Not a Bluetooth address: {mac!r}")
    return value


def mac_from_path(path: str) -> str | None:
    match = re.search(r"dev_([0-9A-Fa-f]{2}(?:_[0-9A-Fa-f]{2}){5})", path or "")
    return match.group(1).replace("_", ":").upper() if match else None


_MAC_LIKE = re.compile(r"^([0-9A-F]{2}[-:_]){5}[0-9A-F]{2}$", re.I)


def device_name(name: Any, alias: Any) -> str | None:
    """The speaker's real name, or None while BlueZ has not learned it.

    BlueZ's Alias falls back to the address with dashes (18-66-96-C3-7A-28)
    until the name is known, so an address-shaped alias is not a name.
    """

    for value in (alias, name):
        text = str(value or "").strip()
        if text and not _MAC_LIKE.match(text):
            return text
    return None


def speaker_kind(dev: dict[str, Any]) -> str:
    """headphones / speaker / car / other, for the app's icon."""

    cls = dev.get("class")
    icon = str(dev.get("icon") or "")
    if isinstance(cls, int) and (cls >> 8) & 0x1F == 0x04:
        minor = (cls >> 2) & 0x3F
        if minor in (0x01, 0x06):  # wearable headset, headphones
            return "headphones"
        if minor == 0x08:  # car audio
            return "car"
        return "speaker"
    if "headphone" in icon or "headset" in icon:
        return "headphones"
    return "speaker" if icon.startswith("audio-") else "other"


def signal_label(rssi: Any) -> str | None:
    """A rough, human label for RSSI (dBm)."""

    if not isinstance(rssi, int | float):
        return None
    if rssi >= -60:
        return "strong"
    if rssi >= -75:
        return "good"
    return "weak"


def is_audio_sink(dev: dict[str, Any]) -> bool:
    """A speaker/headphones: advertises A2DP sink, or its class says audio."""

    uuids = [str(u).lower() for u in dev.get("uuids") or []]
    if A2DP_SINK_UUID in uuids:
        return True
    cls = dev.get("class")
    if isinstance(cls, int) and (cls >> 8) & 0x1F == 0x04:  # major class Audio/Video
        return True
    return str(dev.get("icon") or "").startswith("audio-")


def _is_speaker(dev: dict[str, Any]) -> bool:
    return not dev.get("le_only") and is_audio_sink(dev)


def shared_radio_warning(model: str, net: str) -> str | None:
    m = model.lower()
    if "zero 2" in m or "pi 3" in m:
        return (
            "This player's Wi-Fi and Bluetooth share one 2.4 GHz radio. Expect "
            "dropouts, especially in a group; Ethernet (USB adapter) helps."
        )
    if net == "wifi24":
        return (
            "Wi-Fi is on 2.4 GHz, which shares the air with Bluetooth. A 5 GHz "
            "network or Ethernet gives fewer dropouts."
        )
    return None


# ------------------------------------------------------------------ config file
def read_config(path: Path = BT_OUTPUT_FILE) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "mac": None,
        "name": None,
        "enabled": False,
        "latency_ms": DEFAULT_LATENCY_MS,
    }
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return cfg
    for line in lines:
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key == "mac":
            with contextlib.suppress(ValueError):
                cfg["mac"] = norm_mac(value)
        elif key == "name":
            cfg["name"] = value or None
        elif key == "enabled":
            cfg["enabled"] = value == "1"
        elif key == "latency_ms":
            with contextlib.suppress(ValueError):
                cfg["latency_ms"] = max(0, min(MAX_LATENCY_MS, int(value)))
    return cfg


def _clean_name(name: str | None) -> str:
    # One line, shell- and ALSA-config-safe (select-audio-output reads it).
    flat = re.sub(r"\s+", " ", str(name or ""))
    return re.sub(r"[^\w .'()&+-]", "", flat)[:60].strip()


def write_config(cfg: dict[str, Any], path: Path = BT_OUTPUT_FILE) -> None:
    text = (
        f"mac={cfg.get('mac') or ''}\n"
        f"name={_clean_name(cfg.get('name'))}\n"
        f"enabled={1 if cfg.get('enabled') else 0}\n"
        f"latency_ms={int(cfg.get('latency_ms', DEFAULT_LATENCY_MS))}\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ------------------------------------------------------------------ backends
class Backend(Protocol):
    async def adapter(self) -> dict[str, Any] | None: ...
    async def devices(self) -> list[dict[str, Any]]: ...
    async def discover(self, seconds: float) -> None: ...
    async def pair(self, mac: str, wait: float) -> None: ...
    async def set_trusted(self, mac: str, value: bool) -> None: ...
    async def connect(self, mac: str, wait: float) -> None: ...
    async def disconnect(self, mac: str) -> None: ...
    async def remove(self, mac: str) -> None: ...
    async def a2dp_pcms(self) -> dict[str, str]: ...


def _unwrap(value: Any) -> Any:
    inner = getattr(value, "value", value) if type(value).__name__ == "Variant" else value
    if isinstance(inner, dict):
        return {k: _unwrap(v) for k, v in inner.items()}
    if isinstance(inner, list):
        return [_unwrap(v) for v in inner]
    return inner


class BluezBackend:
    """BlueZ and bluealsa over the system D-Bus (dbus-next)."""

    def __init__(self, bus_address: str | None = None) -> None:
        self._bus: Any = None
        self._adapter_path: str | None = None
        self._bus_address = bus_address  # tests: a private bus with a fake BlueZ

    async def _conn(self) -> Any:
        if self._bus is None or not getattr(self._bus, "connected", False):
            from dbus_next import BusType
            from dbus_next.aio import MessageBus

            if self._bus_address:
                bus = MessageBus(bus_address=self._bus_address)
            else:
                bus = MessageBus(bus_type=BusType.SYSTEM)
            self._bus = await bus.connect()
        return self._bus

    async def _call(
        self,
        dest: str,
        path: str,
        iface: str,
        member: str,
        signature: str = "",
        body: list[Any] | None = None,
        wait: float = 10.0,
    ) -> list[Any]:
        from dbus_next import Message, MessageType

        try:
            bus = await self._conn()
        except Exception as exc:  # noqa: BLE001 - no system bus / dbus-daemon restarting
            self.reset()
            raise BtError(f"system D-Bus unavailable: {exc or type(exc).__name__}") from exc
        msg = Message(
            destination=dest,
            path=path,
            interface=iface,
            member=member,
            signature=signature,
            body=body or [],
        )
        try:
            reply = await asyncio.wait_for(bus.call(msg), wait)
        except TimeoutError as exc:
            self.reset()
            raise BtError(f"{member} timed out after {wait:.0f} s") from exc
        except Exception as exc:  # noqa: BLE001 - dropped connection etc.
            self.reset()
            raise BtError(f"{member}: {exc or type(exc).__name__}") from exc
        if reply.message_type == MessageType.ERROR:
            detail = reply.body[0] if reply.body else ""
            raise BtError(f"{member}: {reply.error_name} {detail}".strip())
        return list(reply.body)

    def reset(self) -> None:
        """Forget the bus connection and adapter; the next call reconnects."""
        bus, self._bus, self._adapter_path = self._bus, None, None
        if bus is not None:
            with contextlib.suppress(Exception):
                bus.disconnect()

    async def _objects(self, dest: str = BLUEZ, path: str = "/") -> dict[str, Any]:
        body = await self._call(dest, path, OM, "GetManagedObjects")
        return _unwrap(body[0]) if body else {}

    async def _set(self, path: str, iface: str, prop: str, sig: str, value: Any) -> None:
        from dbus_next import Variant

        await self._call(BLUEZ, path, PROPS, "Set", "ssv", [iface, prop, Variant(sig, value)])

    async def adapter(self) -> dict[str, Any] | None:
        objs = await self._objects()
        found = sorted(p for p, ifaces in objs.items() if ADAPTER in ifaces)
        if not found:
            self._adapter_path = None
            return None
        path = "/org/bluez/hci0" if "/org/bluez/hci0" in found else found[0]
        self._adapter_path = path
        props = objs[path][ADAPTER]
        return {
            "path": path,
            "address": props.get("Address"),
            "powered": bool(props.get("Powered")),
            "pairable": bool(props.get("Pairable")),
        }

    async def _adapter(self) -> str:
        if self._adapter_path is None and await self.adapter() is None:
            raise BtError("No Bluetooth adapter")
        assert self._adapter_path is not None
        return self._adapter_path

    async def _device_path(self, mac: str) -> str:
        return f"{await self._adapter()}/dev_{mac.replace(':', '_')}"

    async def devices(self) -> list[dict[str, Any]]:
        objs = await self._objects()
        out = []
        for path, ifaces in objs.items():
            dev = ifaces.get(DEVICE)
            if not dev or not dev.get("Address"):
                continue
            out.append(
                {
                    "mac": str(dev["Address"]).upper(),
                    "name": device_name(dev.get("Name"), dev.get("Alias")),
                    "paired": bool(dev.get("Paired")),
                    "trusted": bool(dev.get("Trusted")),
                    "connected": bool(dev.get("Connected")),
                    "rssi": dev.get("RSSI"),
                    "uuids": list(dev.get("UUIDs") or []),
                    "class": dev.get("Class"),
                    "icon": dev.get("Icon"),
                    "le_only": dev.get("AddressType") == "random" and dev.get("Class") is None,
                    "path": path,
                }
            )
        return out

    async def discover(self, seconds: float) -> None:
        from dbus_next import Variant

        path = await self._adapter()
        # Classic only: never interferes with the BLE (Improv) advertising.
        await self._call(
            BLUEZ,
            path,
            ADAPTER,
            "SetDiscoveryFilter",
            "a{sv}",
            [{"Transport": Variant("s", "bredr")}],
        )
        await self._call(BLUEZ, path, ADAPTER, "StartDiscovery")
        try:
            await asyncio.sleep(seconds)
        finally:
            with contextlib.suppress(Exception):
                await self._call(BLUEZ, path, ADAPTER, "StopDiscovery")

    async def pair(self, mac: str, wait: float) -> None:
        from freesounderd.bt_agent import AGENT_PATH, PairingAgent

        adapter = await self._adapter()
        dev = await self._device_path(mac)
        bus = await self._conn()
        info = await self.adapter() or {}
        was_pairable = bool(info.get("pairable"))
        agent = PairingAgent(dev)
        bus.export(AGENT_PATH, agent)
        registered = False
        try:
            await self._set(adapter, ADAPTER, "Pairable", "b", True)
            await self._call(
                BLUEZ,
                "/org/bluez",
                "org.bluez.AgentManager1",
                "RegisterAgent",
                "os",
                [AGENT_PATH, "NoInputNoOutput"],
            )
            registered = True
            await self._call(BLUEZ, dev, DEVICE, "Pair", wait=wait)
        finally:
            if registered:
                with contextlib.suppress(Exception):
                    await self._call(
                        BLUEZ,
                        "/org/bluez",
                        "org.bluez.AgentManager1",
                        "UnregisterAgent",
                        "o",
                        [AGENT_PATH],
                    )
            with contextlib.suppress(Exception):
                bus.unexport(AGENT_PATH)
            with contextlib.suppress(Exception):
                await self._set(adapter, ADAPTER, "Pairable", "b", was_pairable)

    async def set_trusted(self, mac: str, value: bool) -> None:
        await self._set(await self._device_path(mac), DEVICE, "Trusted", "b", value)

    async def connect(self, mac: str, wait: float) -> None:
        dev = await self._device_path(mac)
        try:
            await self._call(BLUEZ, dev, DEVICE, "ConnectProfile", "s", [A2DP_SINK_UUID], wait)
        except BtError as exc:
            if "AlreadyConnected" in str(exc):
                return
            await self._call(BLUEZ, dev, DEVICE, "Connect", wait=wait)

    async def disconnect(self, mac: str) -> None:
        await self._call(BLUEZ, await self._device_path(mac), DEVICE, "Disconnect")

    async def remove(self, mac: str) -> None:
        adapter = await self._adapter()
        dev = await self._device_path(mac)
        await self._call(BLUEZ, adapter, ADAPTER, "RemoveDevice", "o", [dev])

    async def a2dp_pcms(self) -> dict[str, str]:
        """mac -> codec for bluealsa's A2DP source PCMs (empty if bluealsa is down)."""

        for root in ("/", "/org/bluealsa"):
            try:
                objs = await self._objects(BLUEALSA, root)
            except Exception:  # noqa: BLE001 - not running / other layout
                continue
            out: dict[str, str] = {}
            for path, ifaces in objs.items():
                pcm = ifaces.get("org.bluealsa.PCM1")
                if not pcm or "a2dpsrc" not in path:
                    continue
                mac = mac_from_path(str(pcm.get("Device") or path))
                if mac:
                    out[mac] = str(pcm.get("Codec") or "SBC")
            return out
        return {}


# ------------------------------------------------------------------ manager
async def unblock_bluetooth() -> None:
    """Best effort: clear an rfkill soft block on Bluetooth (bounded, never raises).

    The setup (BLE provisioning) service unblocks the radio before
    advertising; once a player is set up it no longer runs.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "rfkill",
            "unblock",
            "bluetooth",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(proc.wait(), 5)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
    except Exception:  # noqa: BLE001
        logger.debug("bluetooth bring-up: rfkill failed", exc_info=True)


class BtOutput:
    """Pairing, the chosen speaker, reconnects and the wired fallback."""

    def __init__(
        self,
        backend: Backend,
        *,
        config_path: Path = BT_OUTPUT_FILE,
        reselect: Callable[[], Awaitable[None]] | None = None,
        unit: Callable[[str, str], Awaitable[Any]] | None = None,
        on_change: Callable[[], None] | None = None,
        model: str = "unknown",
        net: Callable[[], str] = lambda: "unknown",
        clock: Callable[[], float] = time.monotonic,
        bring_up: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.backend = backend
        self.bring_up = bring_up
        self._bring_up_at = 0.0
        self._bring_up_task: asyncio.Task[None] | None = None
        self.reason: str | None = None  # why there is no adapter, for the app
        self.config_path = config_path
        self.reselect = reselect
        self.unit = unit
        self.on_change = on_change
        self.model = model
        self.net = net
        self.clock = clock
        self.lock = asyncio.Lock()
        self.busy: str | None = None
        self.last_error: str | None = None
        self.ready: bool | None = None  # bluealsa has the speaker's A2DP PCM
        self.codec: str | None = None
        self.connected = False
        self._retry_at = 0.0
        self._retry = RETRY_MIN
        self._task: asyncio.Task[None] | None = None
        self._last_active: bool | None = None

    # -- config
    def config(self) -> dict[str, Any]:
        return read_config(self.config_path)

    def latency_ms(self) -> int:
        return int(self.config()["latency_ms"])

    def active(self) -> bool:
        cfg = self.config()
        return bool(cfg["mac"] and cfg["enabled"] and self.ready)

    # -- helpers
    async def _unit(self, action: str) -> None:
        if self.unit is not None:
            with contextlib.suppress(Exception):
                await self.unit(action, BLUEALSA_UNIT)

    async def _reselect(self) -> None:
        if self.reselect is not None:
            try:
                await self.reselect()
            except Exception:  # noqa: BLE001
                logger.warning("select-audio-output failed", exc_info=True)
        if self.on_change is not None:
            self.on_change()

    def _fail(self, what: str, exc: BaseException) -> str:
        text = f"{what}: {exc or type(exc).__name__}"
        self.last_error = text
        logger.warning("bluetooth %s", text)
        return text

    async def _lookup_adapter(self) -> tuple[dict[str, Any] | None, str | None]:
        try:
            adapter = await self.backend.adapter()
        except Exception as exc:  # noqa: BLE001
            return None, f"BlueZ not reachable: {exc or type(exc).__name__}"
        if adapter is None:
            return (
                None,
                "No Bluetooth adapter found on this player (radio off or controller missing)",
            )
        return adapter, None

    def _bring_up_due(self) -> bool:
        running = self._bring_up_task is not None and not self._bring_up_task.done()
        return not running and self.clock() >= self._bring_up_at

    async def find_adapter(self) -> dict[str, Any] | None:
        """The adapter, looked up quickly; a missing one is brought up in the background.

        Never blocks the request on rfkill / systemctl / bluetoothd: 0.6.13
        did the bring-up inside GET /bluetooth and the request died
        ("Connection reset by peer" in the app).
        """
        try:
            adapter, reason = await asyncio.wait_for(self._lookup_adapter(), ADAPTER_LOOKUP_WAIT)
        except TimeoutError:
            adapter, reason = None, f"BlueZ did not answer within {ADAPTER_LOOKUP_WAIT:.0f} s"
        if adapter is None and self._bring_up_due():
            self._bring_up_at = self.clock() + BRING_UP_EVERY
            logger.warning("bluetooth: %s; switching Bluetooth on in the background", reason)
            self._bring_up_task = asyncio.create_task(self._bring_up_controller())
        if adapter is None and self._bring_up_task is not None and not self._bring_up_task.done():
            reason = f"{reason}. Trying to switch Bluetooth on…"
        self.reason = reason
        return adapter

    async def _bring_up_controller(self) -> None:
        """rfkill unblock + start bluetooth.service, each bounded, off the request path."""
        try:
            if self.bring_up is not None:
                await asyncio.wait_for(self.bring_up(), BRING_UP_STEP_WAIT)
        except Exception:  # noqa: BLE001
            logger.warning("bluetooth: bring-up step failed", exc_info=True)
        if self.unit is not None:
            try:
                await asyncio.wait_for(self.unit("start", CONTROLLER_UNIT), BRING_UP_STEP_WAIT)
            except Exception:  # noqa: BLE001
                logger.warning("bluetooth: could not start %s", CONTROLLER_UNIT, exc_info=True)
        if self.on_change is not None:
            with contextlib.suppress(Exception):
                self.on_change()

    # -- API
    async def status(self) -> dict[str, Any]:
        cfg = self.config()
        adapter: dict[str, Any] | None = None
        speaker: dict[str, Any] | None = None
        try:
            adapter = await self.find_adapter()
            if cfg["mac"]:
                listed = await asyncio.wait_for(self.backend.devices(), ADAPTER_LOOKUP_WAIT)
                devs = {d["mac"]: d for d in listed}
                dev = devs.get(cfg["mac"])
                speaker = {
                    "mac": cfg["mac"],
                    "name": (dev or {}).get("name") or cfg["name"] or cfg["mac"],
                    "paired": bool(dev and dev["paired"]),
                    "connected": bool(dev and dev["connected"]),
                }
        except Exception as exc:  # noqa: BLE001
            self._fail("status", exc)
        return {
            "experimental": True,
            "available": adapter is not None,
            "reason": None if adapter is not None else self.reason,
            "enabled": bool(cfg["enabled"]),
            "active": self.active(),
            "speaker": speaker,
            "codec": self.codec if self.ready else None,
            "latency_ms": cfg["latency_ms"],
            "default_latency_ms": DEFAULT_LATENCY_MS,
            "busy": self.busy,
            "last_error": self.last_error,
            "warning": shared_radio_warning(self.model, self.net()),
        }

    async def scan(self, seconds: float = 8.0) -> list[dict[str, Any]]:
        if self.lock.locked():
            raise BtError(f"Bluetooth is busy ({self.busy or 'working'})")
        async with self.lock:
            self.busy = "scanning"
            try:
                await self._unit("start")
                await self.backend.discover(max(2.0, min(20.0, seconds)))
                devs = await self.backend.devices()
                waited = 0.0
                while waited < NAME_WAIT and any(
                    not d.get("name") for d in devs if _is_speaker(d)
                ):
                    await self.backend.discover(NAME_ROUND)
                    waited += NAME_ROUND
                    devs = await self.backend.devices()
            except Exception as exc:
                raise BtError(self._fail("scan", exc)) from exc
            finally:
                self.busy = None
        speakers = [
            {
                **{k: d.get(k) for k in ("mac", "name", "paired", "connected", "rssi")},
                "kind": speaker_kind(d),
                "signal": signal_label(d.get("rssi")),
            }
            for d in devs
            if _is_speaker(d)
        ]
        # Paired first, then named before unnamed, then nearest first.
        speakers.sort(
            key=lambda d: (not d["paired"], not d["name"], -(d.get("rssi") or -999))
        )
        return speakers

    async def use(self, mac: str, name: str | None = None) -> dict[str, Any]:
        """Pair (if needed), trust, connect, and make it this room's output."""

        mac = norm_mac(mac)
        if self.lock.locked():
            raise BtError(f"Bluetooth is busy ({self.busy or 'working'})")
        async with self.lock:
            self.last_error = None
            await self._unit("start")
            try:
                devs = {d["mac"]: d for d in await self.backend.devices()}
                if mac not in devs:
                    self.busy = "scanning"
                    await self.backend.discover(6.0)
                    devs = {d["mac"]: d for d in await self.backend.devices()}
                dev = devs.get(mac) or {}
                if not dev.get("paired"):
                    self.busy = "pairing"
                    await self.backend.pair(mac, wait=40.0)
                await self.backend.set_trusted(mac, True)
                self.busy = "connecting"
                await self.backend.connect(mac, wait=20.0)
            except Exception as exc:
                raise BtError(self._fail("connect", exc)) from exc
            finally:
                self.busy = None
            cfg = self.config()
            old = cfg["mac"]
            # A name from the app may be the MAC it showed before the speaker's
            # name was known; prefer BlueZ's real name (learned by now, pairing
            # asks the speaker for it).
            fresh = dev
            with contextlib.suppress(Exception):
                fresh = {d["mac"]: d for d in await self.backend.devices()}.get(mac, dev)
            real = device_name(name, None) or fresh.get("name") or dev.get("name")
            cfg.update(mac=mac, name=real or cfg["name"], enabled=True)
            write_config(cfg, self.config_path)
            if old and old != mac:
                with contextlib.suppress(Exception):
                    await self.backend.disconnect(old)
            self._retry, self._retry_at = RETRY_MIN, 0.0
        # bluealsa needs a moment to create the PCM after the connect.
        for _ in range(20):
            await self.check(force=True)
            if self.ready:
                break
            await asyncio.sleep(0.5)
        return await self.status()

    async def set_enabled(self, enabled: bool) -> dict[str, Any]:
        cfg = self.config()
        if enabled and not cfg["mac"]:
            raise BtError("Pick a Bluetooth speaker first")
        cfg["enabled"] = enabled
        write_config(cfg, self.config_path)
        if not enabled:
            # Wired output again: free the radio (matters on a Zero 2 W).
            with contextlib.suppress(Exception):
                await self.backend.disconnect(cfg["mac"])
        self._retry, self._retry_at = RETRY_MIN, 0.0
        await self.check(force=True)
        return await self.status()

    async def set_latency(self, ms: int) -> dict[str, Any]:
        cfg = self.config()
        cfg["latency_ms"] = max(0, min(MAX_LATENCY_MS, int(ms)))
        write_config(cfg, self.config_path)
        if self.on_change is not None:
            self.on_change()
        return await self.status()

    async def forget(self) -> dict[str, Any]:
        cfg = self.config()
        async with self.lock:
            if cfg["mac"]:
                with contextlib.suppress(Exception):
                    await self.backend.disconnect(cfg["mac"])
                with contextlib.suppress(Exception):
                    await self.backend.remove(cfg["mac"])
            with contextlib.suppress(OSError):
                self.config_path.unlink()
            self.last_error = None
        await self.check(force=True)
        await self._unit("stop")
        return await self.status()

    # -- watch
    async def check(self, *, force: bool = False) -> None:
        """One step: notice (dis)connects, reconnect with backoff, re-pick the output."""

        cfg = self.config()
        ready = False
        if cfg["mac"]:
            try:
                pcms = await self.backend.a2dp_pcms()
                devs = {d["mac"]: d for d in await self.backend.devices()}
            except Exception:  # noqa: BLE001 - bluealsa/bluetoothd restarting
                pcms, devs = {}, {}
            self.connected = bool(devs.get(cfg["mac"], {}).get("connected"))
            self.codec = pcms.get(cfg["mac"])
            ready = cfg["mac"] in pcms
            want = cfg["enabled"]
            if want and not ready:
                # bluealsa runs only while a speaker is set up (cached check).
                await self._unit("start")
            now = self.clock()
            if want and not self.connected and not self.lock.locked() and now >= self._retry_at:
                try:
                    await self.backend.connect(cfg["mac"], wait=15.0)
                    self._retry = RETRY_MIN
                    self._retry_at = now + RETRY_MIN
                    self.connected = True
                    with contextlib.suppress(Exception):
                        pcms = await self.backend.a2dp_pcms()
                        self.codec = pcms.get(cfg["mac"])
                        ready = cfg["mac"] in pcms
                except Exception as exc:  # noqa: BLE001
                    self._retry_at = now + self._retry
                    logger.info(
                        "bluetooth speaker %s not reachable (%s); next try in %.0f s",
                        cfg["mac"],
                        exc,
                        self._retry,
                    )
                    self._retry = min(RETRY_MAX, self._retry * 2)
        active = bool(ready and cfg["mac"] and cfg["enabled"])
        self.ready = ready
        if force or self._last_active is None or active != self._last_active:
            if self._last_active is not None or active:
                logger.info(
                    "bluetooth output %s", "active" if active else "inactive (wired output)"
                )
            self._last_active = active
            await self._reselect()

    async def run(self) -> None:
        while True:
            try:
                await self.check()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("bluetooth watch failed")
            await asyncio.sleep(WATCH_CONFIGURED if self.config()["mac"] else WATCH_IDLE)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._bring_up_task is not None and not self._bring_up_task.done():
            self._bring_up_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._bring_up_task
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

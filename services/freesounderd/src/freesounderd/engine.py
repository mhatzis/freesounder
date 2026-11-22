"""The player engine: household peers, group membership, sources, Snapcast.

Every player runs the same engine. A player with ``follow is None`` leads its
group: it runs snapserver, Spotify Connect (go-librespot), AirPlay
(shairport-sync, started by snapserver) and radio (ffmpeg), and its own
snapclient plays from 127.0.0.1. A member stops those and points its snapclient
at the leader. Each player is the only writer of its own membership record;
the others learn it by polling ``/api/v2/peer/state``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import shutil
import time
from typing import Any

import httpx

from freesounderd import __version__, peers
from freesounderd.artwork import AirplayArt, ArtLookup, split_stream_title
from freesounderd.audio import audio_state, play_tone
from freesounderd.bluetooth import BluezBackend, BtOutput, unblock_bluetooth
from freesounderd.config import DaemonSettings
from freesounderd.discovery import Discovery, local_ip
from freesounderd.dsp import read_audio_output
from freesounderd.library import Library
from freesounderd.media import MEDIA_SOURCES, MediaPlayer
from freesounderd.mpd import MpdClient, MpdError
from freesounderd.podcasts import Podcasts
from freesounderd.radio import RadioBrowser, RadioPlayer
from freesounderd.snapcast import (
    SOURCE_STREAM,
    SnapcastError,
    SnapcastRpc,
    clients_of,
    render_snapserver_conf,
    stream_status,
)
from freesounderd.spotify import (
    UNIT as SPOTIFY_UNIT,
)
from freesounderd.spotify import (
    LibrespotClient,
    clear_credentials,
    is_paused,
    is_playing,
    login_failure_reason,
    now_playing_fields,
    read_state,
    render_config,
    restore_credentials,
    saved_credentials,
    stored_username,
)
from freesounderd.store import Store, apply_amp_default, board_model, detected_hat, hostname
from freesounderd.system import System
from freesounderd.upnp import UNIT as UPNP_UNIT
from freesounderd.upnp import RendererInfo
from freesounderd.upnp import render_conf as render_upnp_conf

logger = logging.getLogger(__name__)

SNAPSERVER_UNIT = "snapserver.service"
SNAPCLIENT_UNIT = "snapclient.service"
SOLO_SUFFIX = ":solo"
RELAY_IDLE_END = 30.0
AIRPLAY_PAUSE_GRACE = 60.0
BOOT_GRACE = 15.0
PEER_FORGET = 600.0
# Pause: the room's snapcast group is muted at once, so the audio already
# buffered (snapserver's 1 s plus the client's ALSA buffer, 400 ms more on a
# Bluetooth speaker) is not heard after the tap. The mute ends on play, or once
# the paused source has had time to drain (and was seen paused), at most after
# PAUSE_MUTE_MAX if the source never confirmed the pause.
PAUSE_TAIL_MUTE = 3.0
PAUSE_MUTE_MAX = 15.0
# A play/pause just sent is what the player reports until a source read taken
# after the command agrees (or this long), so a stale read can't flip it back.
CMD_STATE_HOLD = 6.0
CONTROL_WAIT = 12.0  # s a play/pause waits for the one before it, at most


class EngineError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


NET_PROBE_EVERY = 60.0  # s between `iw link` probes on an unchanged route


def default_iface() -> str | None:
    """The default route's interface ("" if none, None if unreadable)."""

    try:
        with open("/proc/net/route", encoding="utf-8") as fh:
            for line in fh.readlines()[1:]:
                parts = line.split()
                if len(parts) > 2 and parts[1] == "00000000":
                    return parts[0]
    except OSError:
        return None
    return ""


def net_kind(iface: str | None = None) -> str:
    """eth, wifi5, wifi24 or unknown, from the default route's interface."""

    if iface is None:
        iface = default_iface()
    if iface is None:
        return "unknown"
    if iface.startswith(("eth", "en")):
        return "eth"
    if iface.startswith("wl"):
        iw = shutil.which("iw")
        if iw:
            import subprocess

            try:
                out = subprocess.run(
                    [iw, "dev", iface, "link"], capture_output=True, text=True, timeout=3
                ).stdout
            except (OSError, subprocess.SubprocessError):
                out = ""
            match = re.search(r"freq:\s*(\d+)", out)
            if match:
                return "wifi5" if int(match.group(1)) >= 4900 else "wifi24"
        return "wifi24"
    return "unknown"


def model_rank(model: str) -> int:
    m = model.lower()
    if "pi 5" in m:
        return 0
    if "pi 4" in m or "pi 400" in m or "compute module 4" in m:
        return 1
    if "pi 3" in m:
        return 2
    if "zero 2" in m:
        return 3
    return 4


NET_RANK = {"eth": 0, "wifi5": 1, "wifi24": 2}


def score(rec: dict[str, Any]) -> tuple[int, int, str]:
    """Lower is better: wired > 5 GHz > 2.4 GHz, then Pi 5 > 4 > 3 > Zero 2 W, then id."""

    return (NET_RANK.get(rec.get("net", ""), 3), model_rank(rec.get("model", "")), rec["id"])


def group_name(names: list[str]) -> str:
    if not names:
        return "FreeSounder"
    return names[0] if len(names) == 1 else f"{names[0]} + {len(names) - 1}"


class Engine:
    def __init__(
        self,
        settings: DaemonSettings,
        store: Store,
        *,
        system: System | None = None,
        http: httpx.AsyncClient | None = None,
        discovery: Discovery | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.system = system or System(managed=settings.manage_system)
        self.http = http or httpx.AsyncClient()
        self.discovery = discovery
        self.id = store.player_id
        self.group: dict[str, Any] = store.load_group()
        self.peers: dict[str, dict[str, Any]] = {}
        # Unverified addresses of household members (setup hints, gossip);
        # used as poll candidates, trusted only once a signed fetch succeeds.
        self.peer_hints: dict[str, tuple[str, int]] = {}
        # Wall clock used for peer signatures (overridable in tests), and the
        # learned offset of each peer's clock from ours.
        self.clock = time.time
        self.clock_offset: dict[str, float] = {}
        self.source = "none"
        self.np: dict[str, Any] = {}
        self._np_held: tuple[str, dict[str, Any]] | None = None
        self._prev_playing = {
            "spotify": False,
            "airplay": False,
            "radio": False,
            "library": False,
            "podcast": False,
            "upnp": False,
        }
        self._airplay_paused_since: float | None = None
        self._idle_since: float | None = None
        self._lost_since: float | None = None
        self._started = time.monotonic()
        self._lock = asyncio.Lock()
        # Play/pause do not wait for a tick (which holds _lock across slow
        # systemctl/status calls); they only queue behind each other.
        self._control_lock = asyncio.Lock()
        self._cmd_state: tuple[str, float, str] | None = None
        # Every play/pause gets a number; one still queued when a newer one
        # arrived is dropped (the newest intent wins, a stale pause can't
        # land after a play). Mute/unmute calls go out one at a time, so a
        # pause's mute can never reach snapserver after a play's unmute.
        self._cmd_seq = 0
        self._mute_lock = asyncio.Lock()
        self._tail_mute: dict[str, Any] | None = None
        self._sources_read_at = 0.0
        self._kick = asyncio.Event()
        self._listeners: set[asyncio.Queue[str]] = set()
        self._last_broadcast = ""
        self._snap_target: str | None = None
        self._applied_volume: tuple[int, bool] | None = None
        self._last_name: str | None = None
        self._server_status: dict[str, Any] = {}
        self._spotify_status: dict[str, Any] | None = None
        self._net = "unknown"
        # Bluetooth speaker output (experimental feature); set in start().
        self.bt: BtOutput | None = None
        self._net_iface: str | None = None
        self._net_at = -NET_PROBE_EVERY
        self._model = board_model()
        self._task: asyncio.Task[None] | None = None
        self.notices: list[dict[str, Any]] = []
        self._tone_lock = asyncio.Lock()
        # Spotify Connect volume <-> room/group volume (see _sync_spotify_volume).
        self._spotify_vol_seen: int | None = None
        self._spotify_vol_pushed: int | None = None
        self._pending_group_volume: int | None = None
        self._spotify_push_at = 0.0
        self._airplay_vol_at = 0.0
        self._tone_active = False
        self.snap_local = SnapcastRpc(settings.snapserver_rpc, self.http)
        self.spotify = LibrespotClient(settings.librespot_api, self.http)
        self.radio = RadioPlayer(
            settings.ffmpeg_bin, str(settings.run_dir / "radio.fifo"), settings.manage_system
        )
        self.radio_browser = RadioBrowser(self.http, settings.radio_browser_base)
        self.art = ArtLookup(self.http, enabled=settings.radio_art_lookup)
        self.art.on_found = self.kick
        # New song on the radio (ICY title poll): recompute now playing and
        # push it to the app and the group straight away.
        self.radio.on_title = self.kick
        self.airplay_art = AirplayArt()
        self._np_song: tuple[Any, ...] | None = None
        self._group_save: asyncio.Future[None] | None = None
        self._volume_task: asyncio.Future[None] | None = None
        self._volume_dirty = False
        self._np_changed_at: float | None = None
        self._spotify_login_active = False
        self._spotify_login_lock = asyncio.Lock()
        # Pairing with spotify.com/pair (device_auth), while it waits.
        self._spotify_pair: dict[str, Any] | None = None
        self._spotify_pair_task: asyncio.Task[None] | None = None
        self._spotify_pair_ctx: tuple[dict[str, Any] | None, str, str] | None = None
        # MPD: music library, podcasts and the UPnP/DLNA renderer (upmpdcli).
        self.media = MediaPlayer(MpdClient(settings.mpd_host, settings.mpd_port))
        self.library = Library(
            state_dir=settings.state_dir,
            music_dir=settings.music_dir,
            mpd=self.media.mpd,
            managed=settings.manage_system,
        )
        self.podcasts = Podcasts(settings.state_dir, self.http)
        self.renderer = RendererInfo(self.http, lambda: settings.advertise_ip or local_ip())
        self._upnp_meta: dict[str, Any] = {}
        self._upnp_meta_key: tuple[str, float] | None = None
        self._media_vol_seen: int | None = None
        self._media_vol_pushed: int | None = None
        self._pod_saved: tuple[str, float] = ("", 0.0)
        self._pod_last: tuple[str, float, float | None] | None = None
        self._mount_task: asyncio.Task[None] | None = None
        self._library_scan_at = time.monotonic()
        features = store.features()
        self.airplay_version = settings.airplay_version or int(features.get("airplay", 0) or 0)

    # ------------------------------------------------------------------ records
    @property
    def ready(self) -> bool:
        return self.store.household() is not None

    @property
    def is_leader(self) -> bool:
        return self.group.get("follow") is None

    @property
    def leader_id(self) -> str:
        return self.group.get("follow") or self.id

    def room_name(self) -> str:
        return self.store.load_settings().name

    def self_record(self) -> dict[str, Any]:
        hh = self.store.household()
        rec: dict[str, Any] = {
            "id": self.id,
            "room": self.room_name(),
            "hh": hh.id if hh else None,
            "model": self._model,
            "net": self._net,
            "ip": self.settings.advertise_ip or local_ip(),
            "port": self.settings.port,
            "hostname": hostname(),
            "version": __version__,
            "follow": self.group.get("follow"),
            "term": self.group.get("term", 0),
            "relay": bool(self.group.get("relay")),
            "took_over": self.group.get("took_over"),
            "volume": int(self.group.get("volume", 40)),
            "muted": bool(self.group.get("muted", False)),
            "available": True,
            "library": len(self.library.shares()),
            "pod_rev": self.podcasts.rev(),
            "known": {
                pid: [r.get("ip"), int(r.get("port", 8080))]
                for pid, r in self.peers.items()
                if r.get("ip") and self.peer_alive(pid)
            },
        }
        if self.is_leader:
            rec["source"] = self.source
            rec["state"] = self.play_state()
            rec["now_playing"] = self.np
            rec["station"] = self.radio.station
        return rec

    def play_state(self) -> str:
        cmd = self._cmd_state
        if cmd is not None and self.source != "none" and self.source == cmd[2]:
            if time.monotonic() - cmd[1] < CMD_STATE_HOLD:
                return cmd[0]
        return self._source_state()

    def _source_state(self) -> str:
        """What the source itself last reported (no pending command)."""

        if self.source == "none":
            return "idle"
        if self.source == "spotify":
            return "playing" if is_playing(self._spotify_status) else "paused"
        if self.source == "radio":
            return "paused" if self.radio.paused else "playing"
        if self.source == "airplay":
            return "paused" if self._airplay_paused_since else "playing"
        if self.source in MEDIA_SOURCES:
            return "playing" if self.media.playing else "paused"
        return "idle"

    def records(self) -> dict[str, dict[str, Any]]:
        now = time.monotonic()
        out = {self.id: self.self_record()}
        for pid, rec in self.peers.items():
            if pid == self.id:
                continue
            item = dict(rec)
            item["available"] = now - rec.get("_seen", 0) <= self.settings.leader_timeout
            out[pid] = item
        # Members this player has seen before but not lately (switched off,
        # unplugged, or not back since our restart): show them as offline
        # rather than letting the room vanish. "Remove" in the app forgets one.
        if self.store.household() is not None:
            names = self.store.known_peer_names()
            for pid, (ip, port) in self.store.known_peers().items():
                if pid in out:
                    continue
                out[pid] = {
                    "id": pid,
                    "room": names.get(pid) or "Offline player",
                    "ip": ip,
                    "port": port,
                    "available": False,
                    "follow": None,
                    "offline": True,
                }
        return out

    def _members(self, recs: dict[str, dict[str, Any]], leader: str) -> list[dict[str, Any]]:
        lead = recs.get(leader)
        members = [r for r in recs.values() if r.get("follow") == leader and r["id"] != leader]
        members.sort(key=lambda r: r.get("room", ""))
        if lead is not None and not lead.get("relay"):
            members.insert(0, lead)
        return members

    def groups(self) -> list[dict[str, Any]]:
        recs = self.records()
        out = []
        for rec in recs.values():
            if rec.get("offline"):
                continue
            if rec.get("follow"):
                leader = recs.get(rec["follow"])
                if leader is not None and leader.get("follow") is None:
                    continue
                if leader is None and rec["follow"] not in self.peers:
                    # Following a player we don't know (yet): show it on its own.
                    out.append(self._group_view(recs, rec["id"], solo=True))
                continue
            out.append(self._group_view(recs, rec["id"]))
            if rec.get("relay"):
                out.append(self._group_view(recs, rec["id"], solo=True))
        out.sort(key=lambda g: g["name"].lower())
        return out

    def _group_view(
        self, recs: dict[str, dict[str, Any]], leader: str, *, solo: bool = False
    ) -> dict[str, Any]:
        lead = recs.get(leader, {"id": leader})
        if solo:
            members = [recs[leader]] if leader in recs else []
            gid = leader + SOLO_SUFFIX if lead.get("relay") else leader
            return {
                "id": gid,
                "name": group_name([m.get("room", "?") for m in members]),
                "leader_id": leader,
                "member_ids": [m["id"] for m in members],
                "volume": members[0].get("volume", 0) if members else 0,
                "muted": members[0].get("muted", False) if members else False,
                "source": "none",
                "state": "idle",
                "relay": False,
                "now_playing": {},
            }
        members = self._members(recs, leader)
        vols = [int(m.get("volume", 0)) for m in members] or [0]
        return {
            "id": leader,
            "name": group_name([m.get("room", "?") for m in members]),
            "leader_id": leader,
            "member_ids": [m["id"] for m in members],
            "volume": round(sum(vols) / len(vols)),
            "muted": all(m.get("muted") for m in members) if members else False,
            "source": lead.get("source", "none"),
            "state": lead.get("state", "idle"),
            "relay": bool(lead.get("relay")),
            "now_playing": lead.get("now_playing") or {},
            "station": lead.get("station"),
        }

    def group_for_room(self, room_id: str) -> dict[str, Any] | None:
        for group in self.groups():
            if room_id in group["member_ids"]:
                return group
        return None

    def rooms(self) -> list[dict[str, Any]]:
        recs = self.records()
        out = []
        for rec in recs.values():
            group = self.group_for_room(rec["id"]) or self._group_view(recs, rec["id"], solo=True)
            ip = rec.get("ip")
            port = rec.get("port", 8080)
            out.append(
                {
                    "id": rec["id"],
                    "name": rec.get("room", rec["id"]),
                    "available": rec.get("available", True),
                    "volume": int(rec.get("volume", 0)),
                    "muted": bool(rec.get("muted", False)),
                    "group_id": group["id"],
                    "group_name": group["name"],
                    "is_group": False,
                    "member_ids": group["member_ids"],
                    "state": group["state"],
                    "leader_id": group["leader_id"],
                    "relaying_for": (
                        self._group_view(recs, rec["id"])["name"] if rec.get("relay") else None
                    ),
                    "model": rec.get("model"),
                    "ip": ip,
                    "port": port,
                    "url": f"http://{ip}:{port}" if ip else None,
                    "hostname": rec.get("hostname"),
                    "version": rec.get("version"),
                    "source": group["source"],
                    "now_playing": {
                        k: (group.get("now_playing") or {}).get(k)
                        for k in ("title", "artist", "album", "image_url")
                    },
                }
            )
        out.sort(key=lambda r: r["name"].lower())
        return out

    def now_playing_for_room(self, room_id: str) -> dict[str, Any]:
        group = self.group_for_room(room_id)
        if group is None:
            return {"player_id": room_id, "group_id": room_id, "source": "none", "state": "idle"}
        return self._np_view(group, room_id)

    def _np_view(self, group: dict[str, Any], player_id: str) -> dict[str, Any]:
        np = dict(group.get("now_playing") or {})
        return {
            "player_id": player_id,
            "group_id": group["id"],
            "group_name": group["name"],
            "source": group.get("source", "none"),
            "state": group.get("state", "idle"),
            "title": np.get("title"),
            "artist": np.get("artist"),
            "album": np.get("album"),
            "image_url": np.get("image_url"),
            "position": np.get("position"),
            "duration": np.get("duration"),
            "station": group.get("station"),
            "station_name": np.get("station_name"),
            "station_image_url": np.get("station_image_url"),
            "stream_title": np.get("stream_title"),
            "meta_updated_at": np.get("meta_updated_at"),
            "meta_from": np.get("meta_from"),
            "title_checked_at": np.get("title_checked_at"),
            "title_error": np.get("title_error"),
            "title_source": np.get("title_source"),
            "uri": np.get("uri"),
            "context_uri": np.get("context_uri"),
            "context_name": np.get("context_name"),
        }

    def txt(self) -> dict[str, str]:
        hh = self.store.household()
        return {
            "v": "2",
            "id": self.id,
            "hh": hh.id if hh else "",
            "room": self.room_name()[:60],
            "model": self._model[:60],
            "state": "ready" if hh else "unclaimed",
            "leader": self.leader_id,
            "path": "/api/v2",
            "version": __version__,
        }

    # ------------------------------------------------------------------ events
    def subscribe(self) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=100)
        self._listeners.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        self._listeners.discard(queue)

    def snapshot_events(self) -> list[str]:
        rooms = self.rooms()
        events = [json.dumps({"type": "event", "event": "rooms", "data": rooms})]
        for room in rooms:
            events.append(
                json.dumps(
                    {
                        "type": "event",
                        "event": "now_playing",
                        "data": self.now_playing_for_room(room["id"]),
                    }
                )
            )
        events.append(
            json.dumps({"type": "event", "event": "groups", "data": self._public_groups()})
        )
        return events

    def _public_groups(self) -> list[dict[str, Any]]:
        return [{k: v for k, v in g.items() if k != "now_playing"} for g in self.groups()]

    def broadcast_if_changed(self) -> None:
        events = self.snapshot_events()
        blob = "\n".join(events)
        if blob == self._last_broadcast:
            return
        self._last_broadcast = blob
        for queue in list(self._listeners):
            if queue.maxsize and queue.qsize() + len(events) > queue.maxsize:
                # A slow client fell behind. Each broadcast is a full snapshot,
                # so drop the stale backlog rather than the newest state (which
                # left the app showing the previous song and cover).
                while not queue.empty():
                    queue.get_nowait()
            for event in events:
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(event)

    def kick(self) -> None:
        self._kick.set()

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        # An amplifier HAT found by its EEPROM: apply the safe volume limit once.
        with contextlib.suppress(OSError, ValueError):
            current = self.store.load_settings()
            capped = apply_amp_default(current, detected_hat())
            if capped is not current:
                self.store.save_settings(capped)
        if self.bt is None and self.settings.manage_system:
            self.bt = BtOutput(
                BluezBackend(),
                bring_up=unblock_bluetooth,
                reselect=self._reselect_output,
                unit=self._bt_unit,
                on_change=self.kick,
                model=self._model,
                net=lambda: self._net,
            )
        if self.bt is not None:
            self.bt.start()
        self._task = asyncio.create_task(self._loop())
        if self.library.shares():
            self._mount_task = asyncio.create_task(self._mount_library())

    async def _mount_library(self) -> None:
        """Mount this player's library shares (the NAS may still be waking)."""

        for delay in (0, 20, 60, 180):
            await asyncio.sleep(delay)
            await self.library.mount_all()
            if all(v.get("mounted") for v in self.library.status.values()):
                break
        self._library_scan_at = time.monotonic()
        await self.library.rescan()

    async def _library_rules(self) -> None:
        """Re-index once a day (new albums on the NAS show up by themselves)."""

        if self.library.shares() and time.monotonic() - self._library_scan_at > 24 * 3600:
            self._library_scan_at = time.monotonic()
            await self.library.mount_all()
            await self.library.rescan()

    async def _bt_unit(self, action: str, unit: str) -> None:
        if action == "start":
            await self.system.ensure_active(unit)
        else:
            await self.system.ensure_stopped(unit)

    async def _reselect_output(self) -> None:
        """Re-pick the ALSA output (Bluetooth speaker or wired) and bounce
        snapclient only if it changed."""

        if not self.settings.manage_system:
            self.system.calls.append(("select-audio-output",))
            return
        proc = await asyncio.create_subprocess_exec(
            "/usr/lib/freesounder/select-audio-output",
            "--restart-if-changed",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(proc.wait(), timeout=30)
        except TimeoutError:
            proc.kill()
        self.system._active_cache.pop(SNAPCLIENT_UNIT, None)

    def output_kind(self) -> str:
        path = self.settings.config_dir / "audio-output"
        return self.store._cached("audio-output", (path,), lambda: read_audio_output(path))["kind"]

    def output_latency_ms(self) -> int:
        """Snapcast client latency for the active output (only Bluetooth needs one)."""

        if self.bt is not None and self.output_kind() == "bluetooth":
            return self.bt.latency_ms()
        return 0

    async def stop(self) -> None:
        if self.bt is not None:
            await self.bt.stop()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self.radio.stop()
        await self.http.aclose()

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - keep the daemon alive
                logger.exception("tick failed")
            self._kick.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._kick.wait(), timeout=self.settings.poll_interval)

    async def _refresh_net(self) -> None:
        """Network kind, re-probed only when the route changes or every 60 s.

        On Wi-Fi this runs `iw dev wlan0 link`; doing that every 2 s tick
        cost a process spawn (and a Wi-Fi firmware query) each time.
        """

        iface = default_iface()
        now = time.monotonic()
        if iface == self._net_iface and now - self._net_at < NET_PROBE_EVERY:
            return
        self._net = await asyncio.to_thread(net_kind, iface)
        self._net_iface, self._net_at = iface, now

    async def tick(self) -> None:
        if self.settings.manage_system:
            await self._refresh_net()
        else:
            self._net = "unknown"
        await self.poll_peers()
        async with self._lock:
            if self.ready:
                await self._membership_rules()
            if self.is_leader:
                await self._lead()
            else:
                await self._follow()
            await self._apply_volume()
        await self._library_rules()
        pending, self._pending_group_volume = self._pending_group_volume, None
        if pending is not None:
            # Moved in the Spotify app: becomes this group's volume (outside the lock).
            await self.set_group_volume(self.id, pending, None)
        self._write_status()
        if self.discovery is not None:
            await self.discovery.refresh()
        self.broadcast_if_changed()

    # ------------------------------------------------------------------ peers
    def _candidates(self) -> dict[str, tuple[str, int]]:
        hh = self.store.household()
        out: dict[str, tuple[str, int]] = {}
        if hh is not None:
            out.update(self.peer_hints)
            out.update(self.store.known_peers())
        out.pop(self.id, None)
        for pid, rec in self.peers.items():
            if rec.get("ip"):
                out[pid] = (rec["ip"], int(rec.get("port", 8080)))
        if self.discovery is not None and hh is not None:
            for found in self.discovery.found.values():
                if found.txt.get("hh") == hh.id and found.id and found.id != self.id:
                    out[found.id] = (found.ip, found.port)
        return out

    async def poll_peers(self) -> None:
        hh = self.store.household()
        if hh is None:
            return
        candidates = self._candidates()

        async def fetch(pid: str, ip: str, port: int) -> None:
            path = "/api/v2/peer/state"
            try:
                res = await self._signed(pid, "GET", f"http://{ip}:{port}{path}", path, b"", 2.5)
                if res.status_code != 200:
                    return
                rec = res.json()
            except (httpx.HTTPError, ValueError):
                return
            if rec.get("hh") != hh.id or rec.get("id") != pid:
                return
            rec["_seen"] = time.monotonic()
            rec["ip"] = rec.get("ip") or ip
            self.peers[pid] = rec
            if float(rec.get("pod_rev") or 0) > self.podcasts.rev():
                await self._pull_podcasts(pid, ip, port)
            self.peer_hints.pop(pid, None)
            try:
                self.store.remember_peer(pid, ip, port, rec.get("room"))
            except OSError:
                pass
            # Gossip: members it knows become candidates here too.
            for other, addr in (rec.get("known") or {}).items():
                if (
                    other != self.id
                    and other not in self.peers
                    and isinstance(addr, list)
                    and len(addr) == 2
                ):
                    self.peer_hints.setdefault(str(other), (str(addr[0]), int(addr[1])))

        await asyncio.gather(*(fetch(pid, ip, port) for pid, (ip, port) in candidates.items()))
        now = time.monotonic()
        for pid in [p for p, r in self.peers.items() if now - r.get("_seen", 0) > PEER_FORGET]:
            self.peers.pop(pid, None)

    async def _pull_podcasts(self, pid: str, ip: str, port: int) -> None:
        """A peer has newer podcast subscriptions or positions: merge them."""

        path = "/api/v2/peer/podcasts"
        try:
            res = await self._signed(pid, "GET", f"http://{ip}:{port}{path}", path, b"", 4)
            if res.status_code == 200:
                self.podcasts.adopt(res.json())
        except (httpx.HTTPError, ValueError, OSError):
            logger.debug("podcast sync from %s failed", pid, exc_info=True)

    def peer_alive(self, pid: str) -> bool:
        rec = self.peers.get(pid)
        return bool(rec) and time.monotonic() - rec.get("_seen", 0) <= self.settings.leader_timeout

    async def _membership_rules(self) -> None:
        follow = self.group.get("follow")
        if not follow:
            self._lost_since = None
            # Two members took over from the same lost leader at once: the
            # worse one (and so its followers) folds into the better one.
            old = self.group.get("took_over")
            if old and not self.peer_alive(old):
                rivals = [
                    p
                    for p in self.peers.values()
                    if p.get("took_over") == old
                    and p.get("follow") is None
                    and self.peer_alive(p["id"])
                ]
                if rivals:
                    best = min([self.self_record(), *rivals], key=score)
                    if best["id"] != self.id:
                        self._set_group(follow=best["id"], took_over=None)
                        return
            members = [p for p in self.peers.values() if p.get("follow") == self.id]
            if self.group.get("relay") and not any(self.peer_alive(m["id"]) for m in members):
                self._set_group(relay=False)
            return
        if self.peer_alive(follow):
            self._lost_since = None
            leader = self.peers[follow]
            target = leader.get("follow")
            if target:
                if target == self.id:
                    self._set_group(follow=None)
                else:
                    self._set_group(follow=target)
            return
        now = time.monotonic()
        if now - self._started < BOOT_GRACE:
            return
        if self._lost_since is None:
            self._lost_since = now
            return
        if now - self._lost_since < self.settings.leader_timeout:
            return
        await self._failover(follow)

    async def _failover(self, old_leader: str) -> None:
        # Someone in the group may already have taken over (players fail over
        # one after another; in a big group the first ones to notice have
        # already moved). Follow that successor rather than electing again.
        successors = [
            p
            for p in self.peers.values()
            if p.get("took_over") == old_leader
            and p.get("follow") is None
            and self.peer_alive(p["id"])
        ]
        if successors:
            best = min(successors, key=score)
            self._lost_since = None
            if best["id"] != self.id:
                self._set_group(follow=best["id"])
            return
        mates = [
            p
            for p in self.peers.values()
            if p.get("follow") == old_leader and self.peer_alive(p["id"])
        ]
        candidates = [self.self_record(), *mates]
        best = min(candidates, key=score)
        old = self.peers.get(old_leader, {})
        logger.warning("Leader %s lost; new leader %s", old_leader, best["id"])
        self._notice(f"{old.get('room', 'The group leader')} went offline")
        self._lost_since = None
        if best["id"] == self.id:
            term = max([int(c.get("term", 0)) for c in candidates] + [0]) + 1
            self._set_group(follow=None, term=term, relay=False, took_over=old_leader)
            station = old.get("station")
            if station and old.get("source") == "radio" and old.get("state") == "playing":
                await self._start_radio(station)
        else:
            self._set_group(follow=best["id"])

    def _notice(self, text: str) -> None:
        self.notices = [*self.notices[-9:], {"text": text, "at": time.time()}]

    def _set_group(self, **updates: Any) -> None:
        changed = any(self.group.get(k) != v for k, v in updates.items())
        if not changed:
            return
        self.group.update(updates)
        self.store.save_group(self.group)
        self.kick()

    # ------------------------------------------------------------------ leader
    def my_group_name(self) -> str:
        recs = self.records()
        return group_name(
            [m.get("room", "?") for m in self._members(recs, self.id)] or [self.room_name()]
        )

    async def _lead(self) -> None:
        run = self.settings.run_dir
        name = self.my_group_name()
        conf = render_snapserver_conf(
            run_dir=str(run),
            airplay_name=name,
            shairport_bin=self.settings.shairport_bin,
            airplay_version=self.airplay_version,
        )
        spotify_conf = render_config(device_name=name, fifo=str(run / "spotify.fifo"))
        if self.settings.manage_system:
            server_active = await self.system.is_active(SNAPSERVER_UNIT)
            try:
                current = self.settings.snapserver_conf.read_text(encoding="utf-8")
            except OSError:
                current = ""
            # Renaming the AirPlay receiver needs a snapserver restart; wait
            # until AirPlay stops rather than cut the music.
            if current != conf and not (server_active and self.source == "airplay"):
                self.system.write_if_changed(self.settings.snapserver_conf, conf)
                if server_active:
                    await self.system.systemctl("restart", SNAPSERVER_UNIT)
            await self.system.ensure_active(SNAPSERVER_UNIT)
            if not self._spotify_login_active:
                spot_path = self.settings.spotify_dir / "config.yml"
                spot_changed = self.system.write_if_changed(spot_path, spotify_conf, 0o600)
                if spot_changed and await self.system.is_active(SPOTIFY_UNIT):
                    if not await self.spotify.set_device_name(name):
                        await self.system.systemctl("restart", SPOTIFY_UNIT)
                await self.system.ensure_active(SPOTIFY_UNIT)
            upnp_conf = render_upnp_conf(name=name, cache_dir="/var/cache/upmpdcli")
            upnp_path = run / "upmpdcli.conf"
            # Like AirPlay: a rename restarts the renderer only when it is not playing.
            if not (self.source == "upnp" and self.media.playing):
                if self.system.write_if_changed(upnp_path, upnp_conf):
                    if await self.system.is_active(UPNP_UNIT):
                        await self.system.systemctl("restart", UPNP_UNIT)
            await self.system.ensure_active(UPNP_UNIT)
        await self._target_snapclient("127.0.0.1")
        await self._update_sources()
        await self._sync_spotify_volume()
        await self._sync_media_volume()
        await self._reconcile_snapcast()
        await self._relay_rules()

    async def _sync_spotify_volume(self) -> None:
        """One volume, two views: snapclient (group) volume is the only attenuation.

        go-librespot runs with external_volume, so it never scales the audio; it
        only reports the Connect slider. A change in the Spotify app becomes the
        group volume; a change in FreeSounder is pushed to Spotify so its slider
        follows. On a new session Spotify is told our level (never the reverse,
        so connecting does not jump the volume).
        """

        status = self._spotify_status
        if self.source != "spotify" or not status or status.get("volume") is None:
            self._spotify_vol_seen = self._spotify_vol_pushed = None
            return
        steps = int(status.get("volume_steps") or 100) or 100
        spotify = round(int(status["volume"]) * 100 / steps)
        group = self.group_for_room(self.id)
        ours = int(group["volume"]) if group else int(self.group.get("volume", 40))
        if self._spotify_vol_seen is None:
            if spotify != ours:
                await self.spotify.set_volume(ours, steps)
                self._spotify_push_at = time.monotonic()
            self._spotify_vol_seen = self._spotify_vol_pushed = ours
            return
        settling = time.monotonic() - self._spotify_push_at < 3.0
        if settling and spotify != self._spotify_vol_pushed:
            return  # go-librespot has not reported our last push yet
        if spotify != self._spotify_vol_seen and spotify != self._spotify_vol_pushed:
            self._spotify_vol_seen = self._spotify_vol_pushed = spotify
            if spotify != ours:
                self._pending_group_volume = spotify
            return
        self._spotify_vol_seen = spotify
        if ours != self._spotify_vol_pushed:
            await self.spotify.set_volume(ours, steps)
            self._spotify_push_at = time.monotonic()
            self._spotify_vol_seen = self._spotify_vol_pushed = ours

    async def airplay_volume(self, airplay_db: float) -> dict[str, Any]:
        """The AirPlay sender moved its volume slider (shairport-sync hook).

        AirPlay reports 0 .. -30 dB (-144 = mute). shairport-sync runs with
        ignore_volume_control, so the group volume is the only attenuation.
        The first report of a session is the sender's stored level and is not
        applied (connecting must not jump the room); later moves are.
        """

        now = time.monotonic()
        first = now - self._airplay_vol_at > 30 or self.source != "airplay"
        self._airplay_vol_at = now
        if not self.is_leader or self.source != "airplay" or first:
            return {"applied": False}
        if airplay_db <= -100:
            await self.set_group_volume(self.id, None, True)
            return {"applied": True, "muted": True}
        percent = round((max(-30.0, min(0.0, airplay_db)) + 30.0) / 30.0 * 100)
        await self.set_group_volume(self.id, percent, False)
        return {"applied": True, "volume": percent}

    async def _update_sources(self) -> None:
        read_at = time.monotonic()
        self._spotify_status = await self.spotify.status()
        try:
            self._server_status = await self.snap_local.status()
        except SnapcastError:
            self._server_status = {}
        await self.radio.watchdog()
        await self.media.refresh()
        airplay = stream_status(self._server_status, "AirPlay") or {}
        media = self.media.source if self.media.playing else None
        playing = {
            "spotify": is_playing(self._spotify_status),
            "airplay": airplay.get("status") == "playing",
            "radio": self.radio.running,
            "library": media == "library",
            "podcast": media == "podcast",
            "upnp": media == "upnp",
        }
        started = [s for s, on in playing.items() if on and not self._prev_playing[s]]
        self._prev_playing = playing
        if started:
            new = next((s for s in started if s != self.source), started[0])
            await self._switch_source(new)
        if self.source == "airplay":
            if playing["airplay"]:
                self._airplay_paused_since = None
            else:
                self._airplay_paused_since = self._airplay_paused_since or time.monotonic()
                if time.monotonic() - self._airplay_paused_since > AIRPLAY_PAUSE_GRACE:
                    self.source = "none"
                    self._airplay_paused_since = None
        elif self.source == "spotify" and not (
            playing["spotify"] or is_paused(self._spotify_status)
        ):
            self.source = "none"
        elif self.source == "radio" and not (
            self.radio.station
            and (self.radio.running or self.radio.paused or self.radio.restarts < 50)
        ):
            self.source = "none"
        elif self.source in MEDIA_SOURCES and self.media.source != self.source:
            # Finished (MPD stopped), or a UPnP controller replaced our queue.
            self._track_podcast(final=True)
            self.source = self.media.source or "none"
        self._track_podcast()
        if self.source == "none":
            others = [s for s, on in playing.items() if on]
            if others:
                await self._switch_source(others[0])
        await self._refresh_upnp_meta()
        np = self._now_playing(airplay)
        np = self._hold_while_paused(np)
        if np:
            song = tuple(np.get(k) for k in ("title", "artist", "album", "image_url"))
            if song != self._np_song:
                self._np_song = song
                self._np_changed_at = time.time()
            np["meta_updated_at"] = self._np_changed_at
            np["meta_from"] = self.room_name()
        self.np = np
        self._settle_cmd_state(read_at)
        await self._tail_mute_rules()

    def _settle_cmd_state(self, read_at: float) -> None:
        """A source read taken after the last play/pause agrees: it is done."""

        cmd = self._cmd_state
        if cmd is None:
            return
        state, at, source = cmd
        expired = time.monotonic() - at >= CMD_STATE_HOLD
        if expired or source != self.source or (read_at >= at and self._source_state() == state):
            self._cmd_state = None

    async def _tail_mute_rules(self) -> None:
        tm = self._tail_mute
        if tm is None:
            # Not muted by a pause: no group stays muted (e.g. after a restart).
            stuck = [g["id"] for g in self._server_status.get("groups", []) if g.get("muted")]
            if stuck and not self._mute_lock.locked():
                async with self._mute_lock:
                    if self._tail_mute is None:
                        for gid in stuck:
                            with contextlib.suppress(SnapcastError):
                                await self.snap_local.set_group_mute(gid, False)
            return
        actual = self._source_state()
        if self.source != tm["source"]:
            await self._unmute_tail()
            return
        if actual != "playing":
            tm["seen_paused"] = True
        age = time.monotonic() - tm["at"]
        if age < PAUSE_TAIL_MUTE:
            return
        if actual != "playing" or tm["seen_paused"]:
            await self._unmute_tail()  # drained and silent, or resumed from another app
        elif age > PAUSE_MUTE_MAX:
            logger.warning("%s did not confirm the pause; unmuting the room", self.source)
            await self._unmute_tail()

    async def _mute_tail(self, seq: int) -> None:
        async with self._mute_lock:
            if seq != self._cmd_seq:
                return  # a play came in first: never mute after it
            stream = SOURCE_STREAM.get(self.source)
            groups = self._server_status.get("groups", [])
            gids = [g["id"] for g in groups if stream and g.get("stream_id") == stream]
            if not gids:
                return
            self._tail_mute = {
                "gids": gids,
                "at": time.monotonic(),
                "source": self.source,
                "seen_paused": False,
                "seq": seq,
            }
            results = await asyncio.gather(
                *(self.snap_local.set_group_mute(g, True) for g in gids), return_exceptions=True
            )
            if any(isinstance(r, BaseException) for r in results):
                logger.info("muting the paused room failed: %s", results)

    async def _unmute_tail(self) -> None:
        """The paused room's tail has drained: lift the pause mute."""

        async with self._mute_lock:
            tm, self._tail_mute = self._tail_mute, None
            if tm is None:
                return
            await asyncio.gather(
                *(self.snap_local.set_group_mute(g, False) for g in tm["gids"]),
                return_exceptions=True,
            )

    async def _unmute_all(self) -> None:
        """Play: no pause mute may survive, on any group of this server."""

        async with self._mute_lock:
            tm, self._tail_mute = self._tail_mute, None
            gids = set(tm["gids"]) if tm else set()
            try:
                server = await self.snap_local.status()
            except SnapcastError:
                server = self._server_status
            gids |= {g["id"] for g in server.get("groups", []) if g.get("muted")}
            if not gids:
                return
            results = await asyncio.gather(
                *(self.snap_local.set_group_mute(g, False) for g in gids), return_exceptions=True
            )
            if any(isinstance(r, BaseException) for r in results):
                logger.warning("unmuting the room failed: %s", results)

    async def _switch_source(self, new: str) -> None:
        if new == self.source:
            return
        logger.info("Source %s -> %s", self.source, new)
        if new != "radio" and (self.radio.running or self.radio.paused):
            await self.radio.stop()
        if new != "spotify" and is_playing(self._spotify_status):
            await self.spotify.command("pause")
        if new != "airplay" and self.source == "airplay":
            with contextlib.suppress(SnapcastError):
                await self.snap_local.stream_control("AirPlay", "pause")
        if new not in MEDIA_SOURCES and self.media.state != "stop":
            self._track_podcast(final=True)
            await self.media.release()
        self.source = new
        self._airplay_paused_since = None

    # What a paused room keeps showing (0.6.18): while nothing plays, the song
    # and cover stay as they were; a radio station's next song title, a
    # Spotify status read without its track for a moment or a new AirPlay art
    # tag no longer swap the cover under a paused room.
    _HELD_KEYS = (
        "title",
        "artist",
        "album",
        "image_url",
        "station_image_url",
        "stream_title",
        "context_name",
    )

    def _hold_while_paused(self, np: dict[str, Any]) -> dict[str, Any]:
        source = self.source
        held = self._np_held
        if not np or self.play_state() == "playing" or source == "none":
            self._np_held = (source, np) if np and source != "none" else None
            return np
        key = np.get("uri") or np.get("station_name") or np.get("title")
        if held is not None and held[0] == source:
            old = held[1]
            old_key = old.get("uri") or old.get("station_name") or old.get("title")
            # Same track (or same station), or the source briefly lost it.
            if key is None or key == old_key:
                np = {**np, **{k: old[k] for k in self._HELD_KEYS if k in old}}
                return np
        self._np_held = (source, np)
        return np

    def _now_playing(self, airplay: dict[str, Any]) -> dict[str, Any]:
        if self.source == "spotify" and self._spotify_status:
            return now_playing_fields(self._spotify_status)
        if self.source == "radio" and self.radio.station:
            st = self.radio.station
            logo = st.get("favicon") or None
            artist, song = split_stream_title(self.radio.stream_title)
            art = self.art.cached(artist, song)
            return {
                "title": song or st.get("name"),
                "artist": artist or (st.get("name") if song else None),
                "album": None,
                "image_url": art or logo,
                "station_name": st.get("name"),
                "station_image_url": logo,
                "stream_title": self.radio.stream_title,
                # When the leader last read the stream's song title (ICY), for
                # the app's developer line and diagnostics.
                "title_checked_at": self.radio.title_checked_at,
                "title_error": self.radio.title_error,
                "title_source": self.radio.title_source,
            }
        if self.source == "airplay":
            props = airplay.get("properties") or {}
            meta = props.get("metadata") or {}
            artist = meta.get("artist")
            if isinstance(artist, list):
                artist = ", ".join(artist)
            tag = self.airplay_art.update(meta)
            position = props.get("position")
            return {
                "title": meta.get("title"),
                "artist": artist,
                "album": meta.get("album"),
                "image_url": f"{self.base_url()}/api/v2/art/airplay?t={tag}" if tag else None,
                "duration": meta.get("duration"),
                "position": position if isinstance(position, int | float) else None,
            }
        if self.source in MEDIA_SOURCES:
            return self._media_now_playing()
        return {}

    def _media_now_playing(self) -> dict[str, Any]:
        media = self.media
        base: dict[str, Any] = {
            "position": media.elapsed,
            "duration": media.duration,
        }
        track = media.current()
        if track is not None:
            base.update(
                {
                    "title": track.get("title"),
                    "artist": track.get("artist"),
                    "album": track.get("album"),
                    "image_url": track.get("image_url"),
                    "duration": track.get("duration") or media.duration,
                    "uri": track.get("uri"),
                    "context_name": track.get("context_name"),
                }
            )
            return base
        song = media.song
        meta = self._upnp_meta
        base.update(
            {
                "title": meta.get("title") or song.get("Title") or song.get("Name"),
                "artist": meta.get("artist") or song.get("Artist"),
                "album": meta.get("album") or song.get("Album"),
                "image_url": meta.get("image_url"),
            }
        )
        if not base["title"] and song.get("file"):
            base["title"] = song["file"].rsplit("/", 1)[-1].split("?", 1)[0] or None
        return base

    async def _refresh_upnp_meta(self) -> None:
        """Track details from our own renderer (the controller's DIDL-Lite)."""

        if self.source != "upnp":
            self._upnp_meta, self._upnp_meta_key = {}, None
            return
        key = (self.media.song.get("file", ""), time.monotonic() // 15)
        if key == self._upnp_meta_key:
            return
        self._upnp_meta_key = key
        try:
            self._upnp_meta = await self.renderer.track()
        except Exception:  # noqa: BLE001 - metadata is best effort
            logger.debug("renderer metadata failed", exc_info=True)

    def _track_podcast(self, final: bool = False) -> None:
        """Remember where each podcast episode got to (shared by the household)."""

        if final:
            last = self._pod_last
            if last is not None:
                key, pos, dur = last
                self.podcasts.set_position(key, pos, dur)
                self._pod_saved = (key, time.monotonic())
            self._pod_last = None
            return
        if self.source != "podcast":
            return
        track = self.media.current()
        pos = self.media.elapsed
        if not track or not track.get("episode_key") or pos is None:
            return
        key = str(track["episode_key"])
        dur = track.get("duration") or self.media.duration
        self._pod_last = (key, pos, dur)
        saved_key, saved_at = self._pod_saved
        if key != saved_key or time.monotonic() - saved_at > 15 or not self.media.playing:
            self.podcasts.set_position(key, pos, dur)
            self._pod_saved = (key, time.monotonic())

    async def _sync_media_volume(self) -> None:
        """UPnP controllers' volume slider <-> group volume (MPD's null mixer).

        Works like Spotify Connect: the controller's first value of a session
        is not applied (connecting must not jump the room).
        """

        vol = self.media.volume
        if self.source != "upnp" or vol is None:
            self._media_vol_seen = self._media_vol_pushed = None
            return
        group = self.group_for_room(self.id)
        ours = int(group["volume"]) if group else int(self.group.get("volume", 40))
        try:
            if self._media_vol_seen is None:
                if vol != ours:
                    await self.media.set_volume(ours)
                self._media_vol_seen = self._media_vol_pushed = ours
                return
            if vol != self._media_vol_seen and vol != self._media_vol_pushed:
                self._media_vol_seen = self._media_vol_pushed = vol
                if vol != ours:
                    self._pending_group_volume = vol
                return
            self._media_vol_seen = vol
            if ours != self._media_vol_pushed:
                await self.media.set_volume(ours)
                self._media_vol_seen = self._media_vol_pushed = ours
        except MpdError:
            logger.debug("MPD volume sync failed", exc_info=True)

    def base_url(self) -> str:
        return f"http://{self.settings.advertise_ip or local_ip()}:{self.settings.port}"

    async def airplay_cover(self) -> tuple[bytes, str] | None:
        art = self.airplay_art
        if art.data:
            return art.data, art.mime
        if art.source_path:
            rpc = httpx.URL(self.settings.snapserver_rpc)
            url = f"{rpc.scheme}://{rpc.host}:{rpc.port}{art.source_path}"
            try:
                res = await self.http.get(url, timeout=5)
            except httpx.HTTPError:
                return None
            if res.status_code == 200 and res.content:
                art.data = res.content
                art.mime = res.headers.get("content-type", "image/jpeg")
                return art.data, art.mime
        return None

    async def _reconcile_snapcast(self) -> None:
        server = self._server_status
        if not server:
            return
        recs = self.records()
        member_ids = {m["id"] for m in self._members(recs, self.id)}
        pairs = clients_of(server)
        connected = {c["id"]: gid for gid, c in pairs if c.get("connected")}
        wanted = [cid for cid in connected if cid in member_ids]
        stream = SOURCE_STREAM.get(self.source, "Idle")
        try:
            if wanted:
                main_gid = connected[wanted[0]]
                group = next((g for g in server.get("groups", []) if g["id"] == main_gid), {})
                current = sorted(c["id"] for c in group.get("clients", []) if c.get("connected"))
                if current != sorted(wanted):
                    await self.snap_local.set_group_clients(main_gid, wanted)
                    server = await self.snap_local.status()
                    connected = {
                        c["id"]: gid for gid, c in clients_of(server) if c.get("connected")
                    }
                    main_gid = connected.get(wanted[0], main_gid)
            else:
                main_gid = ""
            for group in server.get("groups", []):
                desired = stream if group["id"] == main_gid else "Idle"
                if group.get("stream_id") != desired:
                    await self.snap_local.set_group_stream(group["id"], desired)
        except SnapcastError:
            logger.info("snapserver not ready", exc_info=True)

    async def _relay_rules(self) -> None:
        # 0.6.6 and older let a leader that left a playing group keep feeding
        # it ("relay"), so an ungrouped room was still driven by its old
        # leader. Ungrouped now means independent: finish any such relay.
        self._idle_since = None
        if self.group.get("relay"):
            logger.info("Ending relay from an older version: handing the group over")
            await self._leave_as_leader()

    async def _leave_as_leader(self, prefer: str | None = None) -> dict[str, Any]:
        """I lead a group and leave it: the music stays with the others.

        Taking a room out of a group turns *that* room off; the rooms that
        remain keep playing (0.6.18; before, Spotify, AirPlay and the library
        stayed on the leader and the rooms that remained went silent).

        - Radio: the new leader starts the same station.
        - Library and podcasts: the new leader gets the queue and position.
        - Spotify: the new leader takes the group; the app moves Spotify
          playback to it (Web API transfer) using the returned hint.
        - AirPlay and UPnP stream to this player's own device and cannot move:
          the others become independent rooms and this one keeps playing.

        [prefer] picks the new leader (the room the user was on), if it stays.
        Afterwards nobody follows me, so my room is fully on its own.
        """

        recs = self.records()
        members = [
            r
            for r in recs.values()
            if r.get("follow") == self.id and r["id"] != self.id and r.get("available", True)
        ]
        state = self.play_state()
        active = state in ("playing", "paused")
        if self.group.get("relay"):
            self._set_group(relay=False)
        if not members:
            return {}
        if active and self.source in ("airplay", "upnp"):
            for member in members:
                await self._peer_command(member["id"], {"op": "solo"})
            self.kick()
            return {"kept": self.source}
        station = self.radio.station if active and self.source == "radio" else None
        media = None
        if active and self.source in ("library", "podcast") and self.media.tracks:
            pos = self.media.status.get("song")
            media = {
                "tracks": self.media.tracks,
                "start": int(pos) if str(pos or "").isdigit() else 0,
                "seek": float(self.media.elapsed or 0),
                "owner": self.source,
                "paused": state == "paused",
            }
        spotify = active and self.source == "spotify"
        best = await self._handover(members, station, prefer=prefer, media=media)
        result: dict[str, Any] = {"leader": best}
        if station:
            await self.radio.stop()
        if media:
            self._track_podcast(final=True)
            await self.media.release()
        if spotify:
            # The app transfers Spotify playback to the new leader's Connect
            # device; stop here so this room goes quiet straight away.
            with contextlib.suppress(Exception):
                await self.spotify.command("pause")
            result["transfer"] = {"source": "spotify", "to": best, "playing": state == "playing"}
        if station or media or spotify:
            self.source = "none"
            self.np = {}
        return result

    async def _handover(
        self,
        members: list[dict[str, Any]] | None = None,
        station: dict[str, Any] | None = None,
        *,
        prefer: str | None = None,
        media: dict[str, Any] | None = None,
    ) -> str | None:
        """Hand my members to [prefer] if it is one of them, else the best of
        them, with my radio station or media queue, if any. Returns the new
        leader's id."""

        if members is None:
            recs = self.records()
            members = [
                r
                for r in recs.values()
                if r.get("follow") == self.id and r["id"] != self.id and r.get("available", True)
            ]
        self.group["relay"] = False
        self._idle_since = None
        if not members:
            self.store.save_group(self.group)
            return None
        chosen = [m for m in members if prefer and m["id"] == prefer]
        best = chosen[0] if chosen else min(members, key=score)
        term = int(self.group.get("term", 0)) + 1
        command: dict[str, Any] = {"op": "lead", "term": term}
        if station:
            command["station"] = station
        if media:
            command["media"] = media
        await self._peer_command(best["id"], command)
        for member in members:
            if member["id"] != best["id"]:
                await self._peer_command(member["id"], {"op": "follow", "leader": best["id"]})
        self.store.save_group(self.group)
        self.kick()
        return str(best["id"])

    # ------------------------------------------------------------------ member
    async def _follow(self) -> None:
        leader = self.peers.get(self.group["follow"])
        if self.radio.running or self.radio.paused:
            await self.radio.stop()
        if self.source in MEDIA_SOURCES or self.media.playing:
            await self.media.release()
        self.source = "none"
        self.np = {}
        if self.settings.manage_system:
            await self.system.ensure_stopped(SPOTIFY_UNIT)
            await self.system.ensure_stopped(UPNP_UNIT)
            await self.system.ensure_stopped(SNAPSERVER_UNIT)
        if leader and leader.get("ip"):
            await self._target_snapclient(leader["ip"])

    async def _target_snapclient(self, host: str) -> None:
        env = f'FS_PLAYER_ID={self.id}\nSNAPCLIENT_URL="tcp://{host}:1704"\n'
        if self._tone_active:
            # A test tone owns the output; snapclient comes back afterwards.
            return
        if not self.settings.manage_system:
            if self._snap_target != host:
                self._snap_target = host
                self._applied_volume = None
                self.system.calls.append(("snapclient", host))
            return
        changed = self.system.write_if_changed(self.settings.snapclient_env, env)
        if changed and self._snap_target is not None:
            await self.system.systemctl("restart", SNAPCLIENT_UNIT)
        elif changed:
            await self.system.systemctl("restart", SNAPCLIENT_UNIT)
        else:
            await self.system.ensure_active(SNAPCLIENT_UNIT)
        if self._snap_target != host:
            self._applied_volume = None
        self._snap_target = host

    def _server_rpc(self) -> SnapcastRpc:
        if self.is_leader or not self._snap_target or self._snap_target == "127.0.0.1":
            return self.snap_local
        return SnapcastRpc(f"http://{self._snap_target}:1780/jsonrpc", self.http)

    async def _apply_volume(self) -> None:
        limit = self.store.load_settings().volume_limit
        percent = round(int(self.group.get("volume", 40)) * limit / 100)
        muted = bool(self.group.get("muted", False))
        if self.group.get("relay"):
            muted = True
        rpc = self._server_rpc()
        try:
            server = self._server_status if rpc is self.snap_local else await rpc.status()
            if not server:
                return
            for _gid, client in clients_of(server):
                if client.get("id") != self.id:
                    continue
                vol = (client.get("config") or {}).get("volume") or {}
                if vol.get("percent") != percent or bool(vol.get("muted")) != muted:
                    await rpc.set_client_volume(self.id, percent, muted)
                self._applied_volume = (percent, muted)
                latency = self.output_latency_ms()
                if int((client.get("config") or {}).get("latency") or 0) != latency:
                    await rpc.set_client_latency(self.id, latency)
        except SnapcastError:
            return

    # ------------------------------------------------------------------ status file
    def _write_status(self) -> None:
        if not self.settings.manage_system:
            return
        hh = self.store.household()
        recs = self.records()
        leader = recs.get(self.leader_id, {})
        group = self.group_for_room(self.id)
        status = {
            "version": __version__,
            "id": self.id,
            "room": self.room_name(),
            "state": "ready" if hh else "unclaimed",
            "household": hh.name if hh else None,
            "household_id": hh.id[:8] if hh else None,
            "role": "leader" if self.is_leader else "member",
            "leader": leader.get("room") if not self.is_leader else None,
            "relay": bool(self.group.get("relay")),
            "group": group["name"] if group else self.room_name(),
            "members": len(group["member_ids"]) if group else 1,
            "source": (group or {}).get("source", self.source),
            "play_state": (group or {}).get("state", "idle"),
            "peers": sum(1 for p in self.peers if self.peer_alive(p)),
            "snapclient_target": self._snap_target,
            "airplay": self.airplay_version,
            "hat": detected_hat(),
            "net": self._net,
            "audio": self._audio_status(),
            "updated": time.time(),
        }
        with contextlib.suppress(OSError):
            self.system.write_if_changed(
                self.settings.run_dir / "status.json", json.dumps(status, indent=2) + "\n"
            )

    # ------------------------------------------------------------------ operations
    async def _peer_command(self, pid: str, body: dict[str, Any]) -> dict[str, Any]:
        if pid == self.id:
            return await self.handle_peer_command(body)
        return await self.forward(pid, "POST", "/api/v2/peer/command", body)

    async def forward(
        self,
        pid: str,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        wait: float = 10,
    ) -> Any:
        rec = self.peers.get(pid)
        hh = self.store.household()
        if (rec is None or not rec.get("ip")) and hh is not None:
            # A member this player remembers but has not heard from lately
            # (listed as a room, e.g. after our restart or a missed beacon):
            # try its last address instead of answering 404, which the app
            # read as "this player has no such feature".
            known = self.store.known_peers().get(pid)
            if known is not None:
                name = self.store.known_peer_names().get(pid) or pid
                rec = {"id": pid, "ip": known[0], "port": known[1], "room": name}
        if rec is None or not rec.get("ip") or hh is None:
            raise EngineError(404, f"Unknown player {pid}")
        raw = json.dumps(body).encode() if body is not None else b""
        url = f"http://{rec['ip']}:{rec.get('port', 8080)}{path}"
        try:
            res = await self._signed(pid, method, url, path, raw, wait)
        except httpx.HTTPError as exc:
            raise EngineError(502, f"{rec.get('room', pid)} did not answer: {exc}") from exc
        try:
            data = res.json()
        except ValueError:
            data = None
        if res.status_code >= 400:
            detail = data.get("detail") if isinstance(data, dict) else res.text[:200]
            raise EngineError(res.status_code, str(detail))
        return data

    async def _signed(
        self, pid: str, method: str, url: str, path: str, raw: bytes, wait: float
    ) -> httpx.Response:
        """A household-signed request to a peer, tolerant of clock skew.

        If the peer rejects the signature and reports its own time, sign
        again on the peer's clock (remembered per peer).
        """
        hh = self.store.household()
        assert hh is not None

        async def send() -> httpx.Response:
            now = self.clock() + self.clock_offset.get(pid, 0.0)
            headers = peers.sign(hh.key, hh.id, method, path, raw, now=now)
            headers["Content-Type"] = "application/json"
            headers["X-FS-Forwarded"] = "1"
            return await self.http.request(method, url, content=raw, headers=headers, timeout=wait)

        res = await send()
        theirs = res.headers.get(peers.HDR_NOW) if res.status_code == 403 else None
        if theirs is not None:
            try:
                offset = float(theirs) - self.clock()
            except ValueError:
                return res
            if abs(offset - self.clock_offset.get(pid, 0.0)) > peers.MAX_SKEW / 2:
                self.clock_offset[pid] = offset
                logger.warning("clock of %s is %.0f s off ours; signing on its clock", pid, offset)
                res = await send()
        return res

    async def handle_peer_command(self, body: dict[str, Any]) -> dict[str, Any]:
        op = body.get("op")
        media = body.get("media") if op == "lead" else None
        async with self._lock:
            if op == "lead":
                self._set_group(
                    follow=None,
                    term=max(int(body.get("term", 0)), self.group.get("term", 0)),
                    relay=False,
                )
                station = body.get("station")
                if isinstance(station, dict) and station.get("url"):
                    await self._start_radio(station)
            elif op == "solo":
                # My leader left the group: I become an independent room.
                if self.group.get("follow"):
                    self._set_group(follow=None, relay=False)
            elif op == "follow" and body.get("leader"):
                await self._local_join(str(body["leader"]))
            else:
                raise EngineError(400, f"Unknown op {op}")
        if isinstance(media, dict) and media.get("tracks"):
            await self._take_media(media)
        self.kick()
        return {"ok": True}

    async def _take_media(self, media: dict[str, Any]) -> None:
        """Carry on with a queue handed over by the leader that left."""

        tracks = [t for t in media.get("tracks") or [] if isinstance(t, dict) and t.get("url")]
        owner = str(media.get("owner") or "library")
        if not tracks or owner not in ("library", "podcast"):
            return
        try:
            await self._start_media(
                tracks[:500], int(media.get("start") or 0), owner, float(media.get("seek") or 0)
            )
            if media.get("paused"):
                await self.media.pause()
        except EngineError as exc:
            logger.warning("Could not take over the group's %s queue: %s", owner, exc)

    def resolve_leader(self, group_or_room: str) -> str:
        gid = group_or_room.removesuffix(SOLO_SUFFIX)
        recs = self.records()
        rec = recs.get(gid)
        if rec is None:
            raise EngineError(404, f"Unknown group or room {group_or_room}")
        return rec.get("follow") or rec["id"]

    async def join(self, room_id: str, target: str) -> None:
        leader = self.resolve_leader(target)
        if room_id == self.id:
            async with self._lock:
                await self._local_join(leader)
            self.kick()
        else:
            await self.forward(room_id, "POST", f"/api/v2/rooms/{room_id}/join", {"group": leader})

    async def _local_join(self, leader: str) -> None:
        if leader == self.id:
            if self.group.get("relay"):
                self._set_group(relay=False)
            elif self.group.get("follow"):
                self._set_group(follow=None)
            return
        if self.radio.running or self.radio.paused:
            await self.radio.stop()
        if self.is_leader and is_playing(self._spotify_status):
            await self.spotify.command("pause")
        self.source = "none"
        self._set_group(follow=leader, relay=False)

    async def leave(self, room_id: str, keep: str | None = None) -> dict[str, Any]:
        """Take a room out of its group. The room turns off; the others keep
        the music. [keep] is the room that should lead what remains."""

        if room_id != self.id:
            res = await self.forward(
                room_id, "POST", f"/api/v2/rooms/{room_id}/leave", {"keep": keep}
            )
            return res if isinstance(res, dict) else {}
        async with self._lock:
            if self.group.get("follow"):
                self._set_group(follow=None, relay=False)
                return {}
            result = await self._leave_as_leader(prefer=keep)
        self.kick()
        return result

    async def set_members(self, gid: str, members: list[str]) -> dict[str, Any]:
        """Make the group exactly [members]. Rooms taken out turn off; if the
        leader itself is taken out, the first listed room that stays takes the
        group and its music (see _leave_as_leader)."""

        old_leader = self.resolve_leader(gid)
        group = next((g for g in self.groups() if g["id"] == old_leader), None)
        current = set(group["member_ids"]) if group else {old_leader}
        wanted = set(members)
        staying = [m for m in members if m in current and m != old_leader]
        leader = old_leader
        result: dict[str, Any] = {}
        if old_leader not in wanted and staying:
            result = await self.leave(old_leader, keep=staying[0])
            leader = result.get("leader") or staying[0]
            current.discard(old_leader)
        for rid in sorted(wanted - current):
            await self.join(rid, leader)
        for rid in sorted(current - wanted):
            await self.leave(rid)
        return result

    async def set_room_volume(self, room_id: str, volume: int | None, muted: bool | None) -> None:
        if room_id != self.id:
            await self.forward(
                room_id,
                "POST",
                f"/api/v2/rooms/{room_id}/volume",
                {"volume": volume, "muted": muted},
                wait=4,
            )
            # Show it right away instead of after the next peer poll (which
            # made sliders in the app jump back for a moment).
            rec = self.peers.get(room_id)
            if rec is not None:
                if volume is not None:
                    rec["volume"] = max(0, min(100, int(volume)))
                if muted is not None:
                    rec["muted"] = bool(muted)
                self.broadcast_if_changed()
            return
        updates: dict[str, Any] = {}
        if volume is not None:
            updates["volume"] = max(0, min(100, int(volume)))
        if muted is not None:
            updates["muted"] = bool(muted)
        if not updates or all(self.group.get(k) == v for k, v in updates.items()):
            return
        # Fast path: no engine lock (a tick may hold it for a while), no
        # snapserver status round trip, and the file is saved a moment later
        # rather than on every step of a slider drag.
        self.group.update(updates)
        self._save_group_soon()
        await self._push_volume()
        self.broadcast_if_changed()

    def _save_group_soon(self, delay: float = 2.0) -> None:
        if self._group_save is not None and not self._group_save.done():
            return

        async def later() -> None:
            await asyncio.sleep(delay)
            with contextlib.suppress(OSError):
                self.store.save_group(self.group)

        self._group_save = asyncio.ensure_future(later())

    async def _push_volume(self) -> None:
        """Send my volume to the snapserver I play from; drags coalesce.

        While one Client.SetVolume is on its way, newer values just replace
        the pending one, so a fast slider never builds a queue.
        """

        if self._volume_task is not None and not self._volume_task.done():
            self._volume_dirty = True
            return

        async def run() -> None:
            while True:
                self._volume_dirty = False
                limit = self.store.load_settings().volume_limit
                percent = round(int(self.group.get("volume", 40)) * limit / 100)
                muted = bool(self.group.get("muted", False))
                try:
                    await self._server_rpc().set_client_volume(self.id, percent, muted)
                    self._applied_volume = (percent, muted)
                except Exception:  # noqa: BLE001 - the next tick's _apply_volume reconciles
                    logger.debug("volume push failed", exc_info=True)
                if not self._volume_dirty:
                    return

        self._volume_task = asyncio.ensure_future(run())
        await asyncio.shield(self._volume_task)

    async def set_group_volume(self, gid: str, volume: int | None, muted: bool | None) -> None:
        leader = self.resolve_leader(gid)
        group = next((g for g in self.groups() if g["id"] == leader), None)
        if group is None:
            raise EngineError(404, "Unknown group")
        recs = self.records()
        delta = 0 if volume is None else int(volume) - int(group["volume"])
        jobs = []
        for rid in group["member_ids"]:
            current = int(recs.get(rid, {}).get("volume", 0))
            new = None if volume is None else max(0, min(100, current + delta))
            if volume is not None and group["volume"] == 0:
                new = int(volume)
            jobs.append(self.set_room_volume(rid, new, muted))
        # All rooms at once, not one after another.
        results = await asyncio.gather(*jobs, return_exceptions=True)
        errors = [r for r in results if isinstance(r, BaseException)]
        if errors and len(errors) == len(results):
            raise errors[0]

    async def control(self, gid: str, action: str) -> None:
        leader = self.resolve_leader(gid)
        if gid.endswith(SOLO_SUFFIX) and action == "play":
            raise EngineError(409, "This room is still playing for its old group.")
        if leader != self.id:
            await self.forward(leader, "POST", f"/api/v2/groups/{leader}/{action}", {})
            rec = self.peers.get(leader)
            if action in ("play", "pause") and rec and rec.get("source", "none") != "none":
                # Show it at once here too, not after the next peer poll.
                rec["state"] = "playing" if action == "play" else "paused"
                self.broadcast_if_changed()
            return
        if action in ("play", "pause", "toggle") and self.source != "none":
            await self._play_pause(action)
            return
        async with self._lock:
            await self._local_control(action)
        self.kick()

    async def _play_pause(self, action: str) -> None:
        """Play/pause without waiting for a tick; pause silences the room at once.

        Play always unmutes every muted group and clears any pending pause,
        whatever state an earlier command left behind.
        """

        if action == "toggle":
            action = "pause" if self.play_state() == "playing" else "play"
        pause = action == "pause"
        source = self.source
        self._cmd_seq += 1
        seq = self._cmd_seq
        self._cmd_state = ("paused" if pause else "playing", time.monotonic(), source)
        if pause:
            await self._mute_tail(seq)
        else:
            await self._unmute_all()
        self.broadcast_if_changed()
        try:
            try:
                await asyncio.wait_for(self._control_lock.acquire(), timeout=CONTROL_WAIT)
            except TimeoutError as exc:
                raise EngineError(503, "The player is busy; try again.") from exc
            try:
                if seq != self._cmd_seq:
                    return  # a newer play/pause came in meanwhile: it decides
                if source == "radio":
                    # ffmpeg start/stop must not race the tick's radio watchdog.
                    try:
                        await asyncio.wait_for(self._lock.acquire(), timeout=CONTROL_WAIT)
                    except TimeoutError as exc:
                        raise EngineError(503, "The player is busy; try again.") from exc
                    try:
                        if seq == self._cmd_seq:
                            await self._local_control(action)
                    finally:
                        self._lock.release()
                else:
                    await self._local_control(action)
            finally:
                self._control_lock.release()
            if not pause and seq == self._cmd_seq:
                await self._unmute_all()  # self-heal: nothing stays muted after a play
        except Exception:
            if seq == self._cmd_seq:
                self._cmd_state = None
                await self._unmute_all()
                self.broadcast_if_changed()
            raise
        finally:
            self.kick()

    async def _local_control(self, action: str) -> None:
        src = self.source
        if src == "spotify":
            cmd = {
                "play": "resume",
                "pause": "pause",
                "stop": "pause",
                "next": "next",
                "previous": "prev",
                "toggle": "playpause",
            }.get(action)
            if cmd:
                await self.spotify.command(cmd)
            if action == "stop":
                self.source = "none"
        elif src == "radio":
            if action in {"pause"}:
                await self.radio.pause()
            elif action == "stop":
                await self.radio.stop()
                self.source = "none"
            elif action in {"play", "toggle"}:
                if self.radio.paused or not self.radio.running:
                    await self.radio.resume()
                elif action == "toggle":
                    await self.radio.pause()
        elif src == "airplay":
            cmd = {
                "play": "play",
                "pause": "pause",
                "stop": "pause",
                "next": "next",
                "previous": "previous",
                "toggle": "playPause",
            }.get(action)
            if cmd:
                with contextlib.suppress(SnapcastError):
                    await self.snap_local.stream_control("AirPlay", cmd)
        elif src in MEDIA_SOURCES:
            try:
                if action == "pause" or (action == "toggle" and self.media.playing):
                    await self.media.pause()
                    self._track_podcast()
                elif action in {"play", "toggle"}:
                    await self.media.resume()
                elif action == "stop":
                    self._track_podcast(final=True)
                    await self.media.stop()
                    self.source = "none"
                elif action == "next":
                    await self.media.next()
                elif action == "previous":
                    await self.media.previous()
                await self.media.refresh()
            except MpdError as exc:
                raise EngineError(502, f"The music player did not answer: {exc}") from exc
        elif action == "play":
            last = self.store.radio_favourites()
            station = self.radio.station or (last[0] if last else None)
            if station:
                await self._start_radio(station)
            else:
                raise EngineError(409, "Nothing to play yet. Pick a radio station first.")

    # ------------------------------------------------------------------ spotify
    def _spotify_leader(self, gid: str) -> str:
        if gid.endswith(SOLO_SUFFIX):
            raise EngineError(409, "This room is still playing for its old group.")
        return self.resolve_leader(gid)

    async def spotify_info(self, gid: str) -> dict[str, Any]:
        leader = self._spotify_leader(gid)
        if leader != self.id:
            return await self.forward(leader, "POST", f"/api/v2/groups/{leader}/spotify", {})
        return await self._spotify_info_local()

    async def _spotify_info_local(self) -> dict[str, Any]:
        status = await self.spotify.status()
        state = read_state(self.settings.spotify_dir)
        return {
            "leader_id": self.id,
            "group_id": self.id,
            "device_name": self.my_group_name(),
            "device_id": (status or {}).get("device_id") or state.get("device_id"),
            "logged_in": status is not None,
            "username": (status or {}).get("username")
            or stored_username(self.settings.spotify_dir),
            "signing_in": self._spotify_login_active,
            "pairing": self._spotify_pair,
        }

    async def spotify_login(self, gid: str, body: dict[str, Any]) -> dict[str, Any]:
        leader = self._spotify_leader(gid)
        if leader != self.id:
            return await self.forward(
                leader, "POST", f"/api/v2/groups/{leader}/spotify/login", body, wait=88
            )
        username = str(body.get("username") or "").strip()
        token = str(body.get("access_token") or "").strip()
        if not username or not token:
            raise EngineError(400, "username and access_token are required")
        if not self.settings.manage_system:
            return await self._spotify_info_local()
        async with self._spotify_login_lock:
            current = await self.spotify.status()
            if current and current.get("username") == username:
                return await self._spotify_info_local()
            self._spotify_login_active = True
            spot_path = self.settings.spotify_dir / "config.yml"
            name = self.my_group_name()
            fifo = str(self.settings.run_dir / "spotify.fifo")
            started = time.time()
            ok = False
            try:
                # 1. This speaker already has a stored login for this account
                #    (its session was only down, e.g. after a regroup): start
                #    it again with that login instead of a new sign-in.
                if stored_username(self.settings.spotify_dir) == username:
                    self.system.write_if_changed(
                        spot_path, render_config(device_name=name, fifo=fifo), 0o600
                    )
                    await self.system.systemctl("restart", SPOTIFY_UNIT)
                    ok = await self._wait_spotify_user(username, 12)
                # 2. A new sign-in with the app's token, twice if need be. The
                #    old login is kept aside and put back if this fails, so a
                #    failed attempt never leaves the speaker signed out.
                backup = saved_credentials(self.settings.spotify_dir)
                for _attempt in range(2):
                    if ok:
                        break
                    await self.system.systemctl("stop", SPOTIFY_UNIT)
                    clear_credentials(self.settings.spotify_dir)
                    self.system.write_if_changed(
                        spot_path,
                        render_config(device_name=name, fifo=fifo, token_login=(username, token)),
                        0o600,
                    )
                    await self.system.systemctl("start", SPOTIFY_UNIT)
                    ok = await self._wait_spotify_user(username, 22)
                if not ok and backup and not saved_credentials(self.settings.spotify_dir):
                    restore_credentials(self.settings.spotify_dir, backup)
            finally:
                # Never leave the token on disk: back to the normal config,
                # which reuses the credentials go-librespot just stored.
                self.system.write_if_changed(
                    spot_path, render_config(device_name=name, fifo=fifo), 0o600
                )
                await self.system.systemctl("restart", SPOTIFY_UNIT)
                self._spotify_login_active = False
            if ok:
                ok = await self._wait_spotify_user(username, 20)
        self.kick()
        info = await self._spotify_info_local()
        if not ok:
            log = await self._unit_log(SPOTIFY_UNIT, since=started)
            logger.warning("Spotify sign-in failed; go-librespot said: %s", log[-600:])
            raise EngineError(502, login_failure_reason(log, self.room_name()))
        return info

    async def _unit_log(self, unit: str, *, since: float) -> str:
        """A unit's journal since [since] (epoch seconds), for error reasons."""

        try:
            proc = await asyncio.create_subprocess_exec(
                "journalctl",
                "-u",
                unit,
                f"--since=@{int(since)}",
                "-o",
                "cat",
                "--no-pager",
                "-q",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        except (OSError, TimeoutError):
            return ""
        return out.decode(errors="replace")

    # How long a pairing code is waited for (Spotify's codes last ~10 min).
    SPOTIFY_PAIR_WAIT = 600.0

    async def spotify_pair(self, gid: str) -> dict[str, Any]:
        """Sign this group's speaker in to Spotify with a code approved at
        spotify.com/pair (go-librespot device_auth, Spotify's own sign-in).

        0.6.19: Spotify no longer accepts the app's own token for a speaker
        (login5 INVALID_CREDENTIALS). Returns the Spotify info with
        "pairing": {code, url, expires_at} while it waits; the speaker keeps
        the login afterwards (state.json, reused by the normal config).
        """

        leader = self._spotify_leader(gid)
        if leader != self.id:
            return await self.forward(
                leader, "POST", f"/api/v2/groups/{leader}/spotify/pair", {}, wait=30
            )
        if not self.settings.manage_system:
            return await self._spotify_info_local()
        async with self._spotify_login_lock:
            status = await self.spotify.status()
            if status and status.get("username"):
                return await self._spotify_info_local()
            if self._spotify_pair_task is not None and not self._spotify_pair_task.done():
                return await self._spotify_info_local()
            self._spotify_login_active = True
            spot_path = self.settings.spotify_dir / "config.yml"
            name = self.my_group_name()
            fifo = str(self.settings.run_dir / "spotify.fifo")
            # go-librespot signs in with a stored login if it has one, so the
            # old one is set aside (and put back if pairing does not finish).
            backup = saved_credentials(self.settings.spotify_dir)
            await self.system.systemctl("stop", SPOTIFY_UNIT)
            clear_credentials(self.settings.spotify_dir)
            self.system.write_if_changed(
                spot_path, render_config(device_name=name, fifo=fifo, device_auth=True), 0o600
            )
            await self.system.systemctl("start", SPOTIFY_UNIT)
            code = None
            for _ in range(30):
                code = await self.spotify.auth_code()
                if code:
                    break
                await asyncio.sleep(0.5)
            if not code:
                await self._end_spotify_pair(backup, name, fifo)
                raise EngineError(
                    502,
                    f"{self.room_name()} could not get a pairing code from Spotify. "
                    "Check that it is online, then try again.",
                )
            self._spotify_pair = code
            self._spotify_pair_ctx = (backup, name, fifo)
            self._spotify_pair_task = asyncio.create_task(
                self._finish_spotify_pair(backup, name, fifo)
            )
        return await self._spotify_info_local()

    async def spotify_pair_cancel(self, gid: str) -> dict[str, Any]:
        leader = self._spotify_leader(gid)
        if leader != self.id:
            return await self.forward(
                leader, "POST", f"/api/v2/groups/{leader}/spotify/pair/cancel", {}
            )
        task = self._spotify_pair_task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if self._spotify_pair is not None and self._spotify_pair_ctx is not None:
            # Cancelled before the waiting task ever ran: tidy up here.
            await self._end_spotify_pair(*self._spotify_pair_ctx)
        return await self._spotify_info_local()

    async def _finish_spotify_pair(
        self, backup: dict[str, Any] | None, name: str, fifo: str
    ) -> None:
        """Wait for the approval, then back to the normal config (which
        reuses the stored login). On timeout, decline or cancel: put the
        old login back."""

        ok = False
        try:
            deadline = time.monotonic() + self.SPOTIFY_PAIR_WAIT
            while time.monotonic() < deadline:
                status = await self.spotify.status()
                if status and status.get("username"):
                    ok = True
                    break
                code = await self.spotify.auth_code()
                if code:
                    self._spotify_pair = code
                await asyncio.sleep(2)
        finally:
            if ok:
                logger.info("Spotify pairing approved on %s", self.room_name())
                await self._end_spotify_pair(None, name, fifo)
            else:
                logger.info("Spotify pairing ended without approval on %s", self.room_name())
                await self._end_spotify_pair(backup, name, fifo)

    async def _end_spotify_pair(
        self, restore: dict[str, Any] | None, name: str, fifo: str
    ) -> None:
        if restore and not saved_credentials(self.settings.spotify_dir):
            restore_credentials(self.settings.spotify_dir, restore)
        self.system.write_if_changed(
            self.settings.spotify_dir / "config.yml",
            render_config(device_name=name, fifo=fifo),
            0o600,
        )
        await self.system.systemctl("restart", SPOTIFY_UNIT)
        self._spotify_pair = None
        self._spotify_pair_ctx = None
        self._spotify_login_active = False
        self.kick()

    async def _wait_spotify_user(self, username: str, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            status = await self.spotify.status()
            if status and status.get("username"):
                return True
            await asyncio.sleep(1)
        return False

    async def spotify_play(self, gid: str, body: dict[str, Any]) -> dict[str, Any]:
        leader = self._spotify_leader(gid)
        if leader != self.id:
            return await self.forward(
                leader, "POST", f"/api/v2/groups/{leader}/spotify/play", body, wait=20
            )
        uri = str(body.get("uri") or "")
        if not uri.startswith("spotify:"):
            raise EngineError(400, "uri must be a spotify: URI")
        async with self._lock:
            await self._switch_source("spotify")
            ok, message = await self.spotify.play(
                uri,
                skip_to_uri=body.get("skip_to_uri") or None,
                position_ms=body.get("position_ms"),
            )
            if not ok:
                raise EngineError(409 if "signed in" in message else 502, message)
            self._prev_playing["spotify"] = True
            self.source = "spotify"
        self.kick()
        return {"ok": True, **(await self._spotify_info_local())}

    async def spotify_context_tracks(self, gid: str, uri: str) -> dict[str, Any]:
        leader = self._spotify_leader(gid)
        if leader != self.id:
            return await self.forward(
                leader, "POST", f"/api/v2/groups/{leader}/spotify/context-tracks", {"uri": uri}
            )
        data = await self.spotify.context_tracks(uri)
        if data is None:
            raise EngineError(409, "Spotify is not signed in on this player yet.")
        return data

    async def seek(self, gid: str, position_ms: int) -> None:
        leader = self.resolve_leader(gid)
        if leader != self.id:
            await self.forward(
                leader, "POST", f"/api/v2/groups/{leader}/seek", {"position_ms": position_ms}
            )
            return
        if self.source in MEDIA_SOURCES:
            try:
                await self.media.seek(position_ms / 1000)
                await self.media.refresh()
            except MpdError as exc:
                raise EngineError(502, f"Could not seek: {exc}") from exc
            self._track_podcast()
            self.kick()
            return
        if self.source != "spotify":
            raise EngineError(409, "Seeking works for Spotify, the library and podcasts.")
        if not await self.spotify.seek(position_ms):
            raise EngineError(502, "Spotify player did not seek")
        self.kick()

    async def play_radio(self, gid: str, body: dict[str, Any]) -> dict[str, Any]:
        leader = self.resolve_leader(gid)
        if gid.endswith(SOLO_SUFFIX):
            async with self._lock:
                await self._handover()
            leader = self.id
        if leader != self.id:
            return await self.forward(leader, "POST", f"/api/v2/groups/{leader}/radio", body)
        station = dict(body)
        if station.get("stationuuid") and not station.get("url"):
            try:
                resolved = await self.radio_browser.resolve(station["stationuuid"])
            except RuntimeError as exc:
                raise EngineError(502, str(exc)) from exc
            station["url"] = resolved.get("url")
            station["name"] = station.get("name") or resolved.get("name")
        if not station.get("url"):
            raise EngineError(400, "No stream URL for this station")
        async with self._lock:
            await self._start_radio(station)
        self.kick()
        return {"ok": True, "station": station}

    # ------------------------------------------------------------------ library
    def library_host(self) -> str:
        """The player holding the music library (shares), else this player."""

        if self.library.shares():
            return self.id
        hosts = [
            r
            for r in self.peers.values()
            if int(r.get("library") or 0) > 0 and self.peer_alive(r["id"])
        ]
        return min(hosts, key=score)["id"] if hosts else self.id

    def _addr(self, pid: str) -> tuple[str, int]:
        if pid == self.id:
            return (self.settings.advertise_ip or local_ip(), self.settings.port)
        rec = self.peers.get(pid)
        if rec is None or not rec.get("ip"):
            raise EngineError(404, "The library player is offline.")
        return (str(rec["ip"]), int(rec.get("port", 8080)))

    def library_urls(self, host: str, uri: str) -> tuple[str, str]:
        """Stream and cover URLs for one library file on its library player."""

        hh = self.store.household()
        if hh is None:
            raise EngineError(409, "Set up this player first.")
        from urllib.parse import quote

        ip, port = self._addr(host)
        sig = peers.token(hh.key, "libfile", uri)
        q = quote(uri, safe="")
        return (
            f"http://{ip}:{port}/api/v2/library/file?uri={q}&sig={sig}",
            f"http://{ip}:{port}/api/v2/library/art?uri={q}",
        )

    async def play_library(self, gid: str, body: dict[str, Any]) -> dict[str, Any]:
        leader = self._media_leader(gid)
        if leader != self.id:
            return await self.forward(leader, "POST", f"/api/v2/groups/{leader}/library/play", body)
        host = str(body.get("host") or self.library_host())
        items = [t for t in body.get("tracks") or [] if isinstance(t, dict) and t.get("uri")]
        if not items:
            raise EngineError(400, "Nothing to play.")
        items = items[:500]
        tracks = []
        for item in items:
            url, art = self.library_urls(host, str(item["uri"]))
            tracks.append(
                {
                    "url": url,
                    "uri": item["uri"],
                    "title": item.get("title"),
                    "artist": item.get("artist"),
                    "album": item.get("album"),
                    "duration": item.get("duration"),
                    "image_url": art,
                    "context_name": body.get("context_name"),
                }
            )
        start = int(body.get("start") or 0)
        await self._start_media(tracks, start, "library")
        return {"ok": True, "tracks": len(tracks)}

    async def play_podcast(self, gid: str, body: dict[str, Any]) -> dict[str, Any]:
        leader = self._media_leader(gid)
        if leader != self.id:
            return await self.forward(leader, "POST", f"/api/v2/groups/{leader}/podcast/play", body)
        ep = body.get("episode") or {}
        url = str(ep.get("url") or "")
        if not url.startswith(("http://", "https://")) or not ep.get("id"):
            raise EngineError(400, "That episode has no audio address.")
        pod = body.get("podcast") or {}
        track = {
            "url": url,
            "title": ep.get("title"),
            "artist": pod.get("title"),
            "album": None,
            "duration": ep.get("duration"),
            "image_url": ep.get("image") or pod.get("image"),
            "episode_key": str(ep["id"]),
            "context_name": pod.get("title"),
        }
        start_at = body.get("position")
        seek = float(start_at) if start_at is not None else self.podcasts.resume_at(str(ep["id"]))
        await self._start_media([track], 0, "podcast", seek=seek)
        return {"ok": True, "position": seek}

    def _media_leader(self, gid: str) -> str:
        if gid.endswith(SOLO_SUFFIX):
            raise EngineError(409, "This room is still playing for its old group.")
        return self.resolve_leader(gid)

    async def _start_media(
        self, tracks: list[dict[str, Any]], start: int, owner: str, seek: float = 0
    ) -> None:
        async with self._lock:
            if self.source != owner:
                await self._switch_source(owner)
            self._track_podcast(final=True)
            try:
                await self.media.play(tracks, start=start, owner=owner, seek=seek)
            except MpdError as exc:
                raise EngineError(502, f"The music player did not start: {exc}") from exc
            self.source = owner
            self._prev_playing[owner] = True
        self.kick()

    async def _start_radio(self, station: dict[str, Any]) -> None:
        if is_playing(self._spotify_status):
            await self.spotify.command("pause")
        if self.media.state != "stop":
            self._track_podcast(final=True)
            await self.media.release()
        await self.radio.play(station)
        self.radio.restarts = 0
        self._prev_playing["radio"] = True
        self.source = "radio"
        favs = [s for s in self.store.radio_favourites() if s.get("url") != station.get("url")]
        recents = [station, *favs][:20]
        with contextlib.suppress(OSError):
            self.store.save_radio_favourites(recents)

    def _audio_status(self) -> dict[str, Any]:
        if not self.settings.manage_system:
            return {}
        try:
            state = audio_state()
        except Exception:  # noqa: BLE001 - status must never break the loop
            logger.debug("audio state failed", exc_info=True)
            return {}
        state["tone"] = self._tone_active
        server = self._server_status or {}
        for gid, client in clients_of(server) if server else []:
            if client.get("id") == self.id:
                vol = (client.get("config") or {}).get("volume") or {}
                group = next((g for g in server.get("groups", []) if g.get("id") == gid), {})
                state["snapclient"] = {
                    "connected": bool(client.get("connected")),
                    "volume": vol.get("percent"),
                    "muted": bool(vol.get("muted")) or bool(group.get("muted")),
                    "stream": group.get("stream_id"),
                }
                stream = next(
                    (x for x in server.get("streams", []) if x.get("id") == group.get("stream_id")),
                    None,
                )
                if stream is not None:
                    state["stream"] = {"id": stream.get("id"), "status": stream.get("status")}
        if self.is_leader and self.source == "radio":
            state["radio"] = {"running": self.radio.running, "restarts": self.radio.restarts}
        return state

    async def test_tone(self, seconds: float = 3.0) -> dict[str, Any]:
        """Stop snapclient, play a tone through the full ALSA chain, start it again."""

        if self._tone_lock.locked():
            raise EngineError(409, "A test tone is already playing")
        async with self._tone_lock:
            self._tone_active = True
            try:
                if not self.settings.manage_system:
                    self.system.calls.append(("test-tone", str(seconds)))
                    return {"ok": True, "detail": "played", "audio": {}}
                await self.system.systemctl("stop", SNAPCLIENT_UNIT)
                # select-audio-output also re-checks HDMI and self-tests the chain.
                proc = await asyncio.create_subprocess_exec(
                    "/usr/lib/freesounder/select-audio-output",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(proc.wait(), timeout=30)
                wav = self.settings.run_dir / "test-tone.wav"
                ok, detail = await play_tone(wav, seconds=seconds)
            finally:
                self._tone_active = False
                await self.system.systemctl("start", SNAPCLIENT_UNIT)
                self.kick()
        audio = self._audio_status()
        output = audio.get("output") or {}
        self._notice(
            f"Test tone {'played' if ok else 'failed'} on {output.get('pcm', '?')}"
            + ("" if ok else f": {detail}")
        )
        return {"ok": ok, "detail": detail, "audio": audio}

    async def doctor(self) -> dict[str, Any]:
        units = {}
        for unit in (
            SNAPSERVER_UNIT,
            SNAPCLIENT_UNIT,
            SPOTIFY_UNIT,
            "nqptp.service",
            "avahi-daemon.service",
        ):
            units[unit] = await self.system.is_active(unit)
        return {
            "version": __version__,
            "id": self.id,
            "room": self.room_name(),
            "ready": self.ready,
            "leader": self.is_leader,
            "group": dict(self.group),
            "source": self.source,
            "snapclient_target": self._snap_target,
            "peers": {
                pid: {
                    "room": r.get("room"),
                    "ip": r.get("ip"),
                    "alive": self.peer_alive(pid),
                    "follow": r.get("follow"),
                }
                for pid, r in self.peers.items()
            },
            "units": units,
            "airplay": self.airplay_version,
            "hat": detected_hat(),
            "net": self._net,
            "notices": self.notices,
            "audio": self._audio_status(),
        }

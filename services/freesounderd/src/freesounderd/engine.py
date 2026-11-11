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
from freesounderd.config import DaemonSettings
from freesounderd.discovery import Discovery, local_ip
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
    is_paused,
    is_playing,
    now_playing_fields,
    render_config,
)
from freesounderd.store import Store, board_model, detected_hat, hostname
from freesounderd.system import System

logger = logging.getLogger(__name__)

SNAPSERVER_UNIT = "snapserver.service"
SNAPCLIENT_UNIT = "snapclient.service"
SOLO_SUFFIX = ":solo"
RELAY_IDLE_END = 30.0
AIRPLAY_PAUSE_GRACE = 60.0
BOOT_GRACE = 15.0
PEER_FORGET = 600.0


class EngineError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def net_kind() -> str:
    """eth, wifi5, wifi24 or unknown, from the default route's interface."""

    iface = ""
    try:
        with open("/proc/net/route", encoding="utf-8") as fh:
            for line in fh.readlines()[1:]:
                parts = line.split()
                if len(parts) > 2 and parts[1] == "00000000":
                    iface = parts[0]
                    break
    except OSError:
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
        self.source = "none"
        self.np: dict[str, Any] = {}
        self._prev_playing = {"spotify": False, "airplay": False, "radio": False}
        self._airplay_paused_since: float | None = None
        self._idle_since: float | None = None
        self._lost_since: float | None = None
        self._started = time.monotonic()
        self._lock = asyncio.Lock()
        self._kick = asyncio.Event()
        self._listeners: set[asyncio.Queue[str]] = set()
        self._last_broadcast = ""
        self._snap_target: str | None = None
        self._applied_volume: tuple[int, bool] | None = None
        self._last_name: str | None = None
        self._server_status: dict[str, Any] = {}
        self._spotify_status: dict[str, Any] | None = None
        self._net = "unknown"
        self._model = board_model()
        self._task: asyncio.Task[None] | None = None
        self.notices: list[dict[str, Any]] = []
        self.snap_local = SnapcastRpc(settings.snapserver_rpc, self.http)
        self.spotify = LibrespotClient(settings.librespot_api, self.http)
        self.radio = RadioPlayer(
            settings.ffmpeg_bin, str(settings.run_dir / "radio.fifo"), settings.manage_system
        )
        self.radio_browser = RadioBrowser(self.http, settings.radio_browser_base)
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
            "volume": int(self.group.get("volume", 40)),
            "muted": bool(self.group.get("muted", False)),
            "available": True,
        }
        if self.is_leader:
            rec["source"] = self.source
            rec["state"] = self.play_state()
            rec["now_playing"] = self.np
            rec["station"] = self.radio.station
        return rec

    def play_state(self) -> str:
        if self.source == "none":
            return "idle"
        if self.source == "spotify":
            return "playing" if is_playing(self._spotify_status) else "paused"
        if self.source == "radio":
            return "paused" if self.radio.paused else "playing"
        if self.source == "airplay":
            return "paused" if self._airplay_paused_since else "playing"
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
                    "is_group": len(group["member_ids"]) > 1,
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
            for event in events:
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(event)

    def kick(self) -> None:
        self._kick.set()

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
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

    async def tick(self) -> None:
        self._net = await asyncio.to_thread(net_kind) if self.settings.manage_system else "unknown"
        await self.poll_peers()
        async with self._lock:
            if self.ready:
                await self._membership_rules()
            if self.is_leader:
                await self._lead()
            else:
                await self._follow()
            await self._apply_volume()
        self._write_status()
        if self.discovery is not None:
            await self.discovery.refresh()
        self.broadcast_if_changed()

    # ------------------------------------------------------------------ peers
    def _candidates(self) -> dict[str, tuple[str, int]]:
        hh = self.store.household()
        out: dict[str, tuple[str, int]] = {}
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
                res = await self.http.get(
                    f"http://{ip}:{port}{path}",
                    headers=peers.sign(hh.key, hh.id, "GET", path),
                    timeout=2.5,
                )
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

        await asyncio.gather(*(fetch(pid, ip, port) for pid, (ip, port) in candidates.items()))
        now = time.monotonic()
        for pid in [p for p, r in self.peers.items() if now - r.get("_seen", 0) > PEER_FORGET]:
            self.peers.pop(pid, None)

    def peer_alive(self, pid: str) -> bool:
        rec = self.peers.get(pid)
        return bool(rec) and time.monotonic() - rec.get("_seen", 0) <= self.settings.leader_timeout

    async def _membership_rules(self) -> None:
        follow = self.group.get("follow")
        if not follow:
            self._lost_since = None
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
            self._set_group(follow=None, term=term, relay=False)
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
            spot_path = self.settings.spotify_dir / "config.yml"
            spot_changed = self.system.write_if_changed(spot_path, spotify_conf)
            if spot_changed and await self.system.is_active(SPOTIFY_UNIT):
                if not await self.spotify.set_device_name(name):
                    await self.system.systemctl("restart", SPOTIFY_UNIT)
            await self.system.ensure_active(SPOTIFY_UNIT)
        await self._target_snapclient("127.0.0.1")
        await self._update_sources()
        await self._reconcile_snapcast()
        await self._relay_rules()

    async def _update_sources(self) -> None:
        self._spotify_status = await self.spotify.status()
        try:
            self._server_status = await self.snap_local.status()
        except SnapcastError:
            self._server_status = {}
        await self.radio.watchdog()
        airplay = stream_status(self._server_status, "AirPlay") or {}
        playing = {
            "spotify": is_playing(self._spotify_status),
            "airplay": airplay.get("status") == "playing",
            "radio": self.radio.running,
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
        if self.source == "none":
            others = [s for s, on in playing.items() if on]
            if others:
                await self._switch_source(others[0])
        self.np = self._now_playing(airplay)

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
        self.source = new
        self._airplay_paused_since = None

    def _now_playing(self, airplay: dict[str, Any]) -> dict[str, Any]:
        if self.source == "spotify" and self._spotify_status:
            return now_playing_fields(self._spotify_status)
        if self.source == "radio" and self.radio.station:
            st = self.radio.station
            title = self.radio.stream_title
            return {
                "title": title or st.get("name"),
                "artist": st.get("name") if title else None,
                "album": None,
                "image_url": st.get("favicon") or None,
            }
        if self.source == "airplay":
            meta = (airplay.get("properties") or {}).get("metadata") or {}
            artist = meta.get("artist")
            if isinstance(artist, list):
                artist = ", ".join(artist)
            return {
                "title": meta.get("title"),
                "artist": artist,
                "album": meta.get("album"),
                "image_url": meta.get("artUrl"),
                "duration": meta.get("duration"),
            }
        return {}

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
        if not self.group.get("relay"):
            self._idle_since = None
            return
        if self.play_state() == "playing":
            self._idle_since = None
            return
        now = time.monotonic()
        self._idle_since = self._idle_since or now
        if now - self._idle_since >= RELAY_IDLE_END:
            await self._handover()

    async def _handover(self) -> None:
        """Hand my members to the best of them and go solo."""

        recs = self.records()
        members = [
            r for r in recs.values() if r.get("follow") == self.id and r.get("available", True)
        ]
        self.group["relay"] = False
        self._idle_since = None
        if not members:
            self.store.save_group(self.group)
            return
        best = min(members, key=score)
        term = int(self.group.get("term", 0)) + 1
        await self._peer_command(best["id"], {"op": "lead", "term": term})
        for member in members:
            if member["id"] != best["id"]:
                await self._peer_command(member["id"], {"op": "follow", "leader": best["id"]})
        self.store.save_group(self.group)
        self.kick()

    # ------------------------------------------------------------------ member
    async def _follow(self) -> None:
        leader = self.peers.get(self.group["follow"])
        if self.radio.running or self.radio.paused:
            await self.radio.stop()
        self.source = "none"
        self.np = {}
        if self.settings.manage_system:
            await self.system.ensure_stopped(SPOTIFY_UNIT)
            await self.system.ensure_stopped(SNAPSERVER_UNIT)
        if leader and leader.get("ip"):
            await self._target_snapclient(leader["ip"])

    async def _target_snapclient(self, host: str) -> None:
        env = f'FS_PLAYER_ID={self.id}\nSNAPCLIENT_URL="tcp://{host}:1704"\n'
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
        self, pid: str, method: str, path: str, body: dict[str, Any] | None = None
    ) -> Any:
        rec = self.peers.get(pid)
        hh = self.store.household()
        if rec is None or not rec.get("ip") or hh is None:
            raise EngineError(404, f"Unknown player {pid}")
        raw = json.dumps(body).encode() if body is not None else b""
        headers = peers.sign(hh.key, hh.id, method, path, raw)
        headers["Content-Type"] = "application/json"
        headers["X-FS-Forwarded"] = "1"
        url = f"http://{rec['ip']}:{rec.get('port', 8080)}{path}"
        try:
            res = await self.http.request(method, url, content=raw, headers=headers, timeout=10)
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

    async def handle_peer_command(self, body: dict[str, Any]) -> dict[str, Any]:
        op = body.get("op")
        async with self._lock:
            if op == "lead":
                self._set_group(
                    follow=None,
                    term=max(int(body.get("term", 0)), self.group.get("term", 0)),
                    relay=False,
                )
            elif op == "follow" and body.get("leader"):
                await self._local_join(str(body["leader"]))
            else:
                raise EngineError(400, f"Unknown op {op}")
        self.kick()
        return {"ok": True}

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

    async def leave(self, room_id: str) -> None:
        if room_id != self.id:
            await self.forward(room_id, "POST", f"/api/v2/rooms/{room_id}/leave", {})
            return
        async with self._lock:
            if self.group.get("follow"):
                self._set_group(follow=None, relay=False)
                return
            members = [p for p in self.peers.values() if p.get("follow") == self.id]
            if not members:
                return
            if self.play_state() == "playing":
                self._set_group(relay=True)
            else:
                await self._handover()
        self.kick()

    async def set_members(self, gid: str, members: list[str]) -> None:
        leader = self.resolve_leader(gid)
        group = next((g for g in self.groups() if g["id"] == leader), None)
        current = set(group["member_ids"]) if group else {leader}
        wanted = set(members)
        for rid in sorted(wanted - current):
            await self.join(rid, leader)
        for rid in sorted(current - wanted):
            await self.leave(rid)

    async def set_room_volume(self, room_id: str, volume: int | None, muted: bool | None) -> None:
        if room_id != self.id:
            await self.forward(
                room_id,
                "POST",
                f"/api/v2/rooms/{room_id}/volume",
                {"volume": volume, "muted": muted},
            )
            return
        updates: dict[str, Any] = {}
        if volume is not None:
            updates["volume"] = max(0, min(100, int(volume)))
        if muted is not None:
            updates["muted"] = bool(muted)
        async with self._lock:
            self._set_group(**updates)
            await self._apply_volume()
        self.broadcast_if_changed()

    async def set_group_volume(self, gid: str, volume: int | None, muted: bool | None) -> None:
        leader = self.resolve_leader(gid)
        group = next((g for g in self.groups() if g["id"] == leader), None)
        if group is None:
            raise EngineError(404, "Unknown group")
        recs = self.records()
        delta = 0 if volume is None else int(volume) - int(group["volume"])
        for rid in group["member_ids"]:
            current = int(recs.get(rid, {}).get("volume", 0))
            new = None if volume is None else max(0, min(100, current + delta))
            if volume is not None and group["volume"] == 0:
                new = int(volume)
            await self.set_room_volume(rid, new, muted)

    async def control(self, gid: str, action: str) -> None:
        leader = self.resolve_leader(gid)
        if gid.endswith(SOLO_SUFFIX) and action == "play":
            raise EngineError(409, "This room is still playing for its old group.")
        if leader != self.id:
            await self.forward(leader, "POST", f"/api/v2/groups/{leader}/{action}", {})
            return
        async with self._lock:
            await self._local_control(action)
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
        elif action == "play":
            last = self.store.radio_favourites()
            station = self.radio.station or (last[0] if last else None)
            if station:
                await self._start_radio(station)
            else:
                raise EngineError(409, "Nothing to play yet. Pick a radio station first.")

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

    async def _start_radio(self, station: dict[str, Any]) -> None:
        if is_playing(self._spotify_status):
            await self.spotify.command("pause")
        await self.radio.play(station)
        self.radio.restarts = 0
        self._prev_playing["radio"] = True
        self.source = "radio"
        favs = [s for s in self.store.radio_favourites() if s.get("url") != station.get("url")]
        recents = [station, *favs][:20]
        with contextlib.suppress(OSError):
            self.store.save_radio_favourites(recents)

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
        }

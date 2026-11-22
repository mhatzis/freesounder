"""Sonos speakers as FreeSounder rooms (0.6.20).

FreeSounder finds Sonos players on the LAN (SSDP, then the household's
ZoneGroupTopology from any one of them) and controls them over their local
UPnP/SOAP API on port 1400: volume and mute, play/pause/next/previous/seek,
now playing, Sonos-with-Sonos grouping, and playing FreeSounder sources on
them by URL (radio, the music library, podcasts).

Sonos rooms are not snapclients: they play on their own clock, so they group
only with other Sonos rooms. Room ids are ``sonos-<RINCON uid>``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import socket
import time
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from html import escape
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

PREFIX = "sonos-"
PORT = 1400
SSDP_ADDR = ("239.255.255.250", 1900)
SSDP_ST = "urn:schemas-upnp-org:device:ZonePlayer:1"
DISCOVER_EVERY = 60.0  # s between SSDP searches
POLL_ACTIVE = 3.0  # s between state reads while an app is watching
POLL_IDLE = 60.0

SERVICES = {
    "AVTransport": "/MediaRenderer/AVTransport/Control",
    "RenderingControl": "/MediaRenderer/RenderingControl/Control",
    "ZoneGroupTopology": "/ZoneGroupTopology/Control",
}
NS = {
    "dc": "http://purl.org/dc/elements/1.1/",
    "upnp": "urn:schemas-upnp-org:metadata-1-0/upnp/",
    "r": "urn:schemas-rinconnetworks-com:metadata-1-0/",
    "didl": "urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/",
}
STATES = {"PLAYING": "playing", "TRANSITIONING": "playing", "PAUSED_PLAYBACK": "paused"}


class SonosError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def is_sonos(room_or_group: str | None) -> bool:
    return bool(room_or_group) and str(room_or_group).startswith(PREFIX)


def uid_of(room_id: str) -> str:
    return room_id.removeprefix(PREFIX)


# ------------------------------------------------------------------ discovery
async def ssdp_discover(wait: float = 2.0) -> set[str]:
    """IPs of Sonos players answering an SSDP M-SEARCH."""

    msg = (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {SSDP_ADDR[0]}:{SSDP_ADDR[1]}\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 1\r\n"
        f"ST: {SSDP_ST}\r\n\r\n"
    ).encode()
    found: set[str] = set()

    class Proto(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
            text = data.decode(errors="replace")
            if "sonos" in text.lower() or SSDP_ST.lower() in text.lower():
                found.add(addr[0])

    loop = asyncio.get_running_loop()
    try:
        transport, _ = await loop.create_datagram_endpoint(
            Proto, local_addr=("0.0.0.0", 0), family=socket.AF_INET
        )
    except OSError as exc:
        logger.debug("SSDP socket failed: %s", exc)
        return found
    try:
        for _ in range(2):
            transport.sendto(msg, SSDP_ADDR)
            await asyncio.sleep(wait / 2)
    finally:
        transport.close()
    return found


# ------------------------------------------------------------------ SOAP
def _envelope(service: str, action: str, args: list[tuple[str, Any]]) -> str:
    urn = f"urn:schemas-upnp-org:service:{service}:1"
    inner = "".join(f"<{k}>{escape(str(v), quote=False)}</{k}>" for k, v in args)
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
        f'<u:{action} xmlns:u="{urn}">{inner}</u:{action}>'
        "</s:Body></s:Envelope>"
    )


def _parse_response(text: str, action: str) -> dict[str, str]:
    root = ET.fromstring(text)
    for el in root.iter():
        if el.tag.endswith(f"}}{action}Response") or el.tag == f"{action}Response":
            return {child.tag.split("}")[-1]: child.text or "" for child in el}
    return {}


def didl_item(
    title: str,
    *,
    uri: str = "",
    upnp_class: str = "object.item.audioItem.musicTrack",
    artist: str | None = None,
    album: str | None = None,
    art: str | None = None,
    mime: str = "audio/mpeg",
) -> str:
    parts = [f"<dc:title>{escape(title)}</dc:title>", f"<upnp:class>{upnp_class}</upnp:class>"]
    if artist:
        parts.append(f"<dc:creator>{escape(artist)}</dc:creator>")
    if album:
        parts.append(f"<upnp:album>{escape(album)}</upnp:album>")
    if art:
        parts.append(f"<upnp:albumArtURI>{escape(art)}</upnp:albumArtURI>")
    if uri:
        parts.append(f'<res protocolInfo="http-get:*:{mime}:*">{escape(uri)}</res>')
    return (
        '<DIDL-Lite xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" '
        'xmlns:r="urn:schemas-rinconnetworks-com:metadata-1-0/" '
        'xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/">'
        '<item id="-1" parentID="-1" restricted="true">' + "".join(parts) + "</item></DIDL-Lite>"
    )


def parse_didl(meta: str | None, base: str) -> dict[str, Any]:
    """Title, artist, album and art from Sonos track metadata (DIDL-Lite)."""

    if not meta or meta == "NOT_IMPLEMENTED":
        return {}
    try:
        root = ET.fromstring(meta)
    except ET.ParseError:
        return {}
    item = root.find("didl:item", NS)
    if item is None:
        return {}

    def text(path: str) -> str | None:
        el = item.find(path, NS)
        return el.text.strip() if el is not None and el.text and el.text.strip() else None

    title, artist = text("dc:title"), text("dc:creator")
    stream = text("r:streamContent")
    if stream and " - " in stream:
        artist, title = (s.strip() for s in stream.split(" - ", 1))
    elif stream:
        title = stream
    art = text("upnp:albumArtURI")
    if art and art.startswith("/"):
        art = base + art
    return {"title": title, "artist": artist, "album": text("upnp:album"), "image_url": art}


def _mime_for(url: str) -> str:
    path = urlparse(url).path.lower()
    for ext, mime in (
        (".flac", "audio/flac"),
        (".m4a", "audio/mp4"),
        (".aac", "audio/aac"),
        (".ogg", "audio/ogg"),
        (".wav", "audio/wav"),
    ):
        if path.endswith(ext):
            return mime
    return "audio/mpeg"


def radio_uri(url: str) -> str:
    """Sonos treats x-rincon-mp3radio:// as a live (ICY) stream."""

    return "x-rincon-mp3radio://" + url[len("http://") :] if url.startswith("http://") else url


def _hms(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"


def _secs(hms: str | None) -> float | None:
    """Seconds from Sonos' H:MM:SS (None for NOT_IMPLEMENTED and the like)."""

    if not hms:
        return None
    m = re.fullmatch(r"(\d+):(\d{2}):(\d{2})(?:\.\d+)?", hms.strip())
    if not m:
        return None
    h, mi, s = (int(x) for x in m.groups())
    return float(h * 3600 + mi * 60 + s)


# ------------------------------------------------------------------ manager
class SonosManager:
    """Sonos rooms for the rooms list, and their controls."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        discover: Callable[[], Awaitable[set[str]]] = ssdp_discover,
        clock: Callable[[], float] = time.monotonic,
        watching: Callable[[], bool] = lambda: True,
    ) -> None:
        self.http = http
        self.discover = discover
        self.clock = clock
        self.watching = watching
        self.known_ips: set[str] = set()
        self.speakers: dict[str, dict[str, Any]] = {}  # uid -> speaker
        self.groups: dict[str, list[str]] = {}  # coordinator uid -> member uids
        self._discovered_at = -1e9
        self._polled_at = -1e9
        self._task: asyncio.Task[None] | None = None
        self.on_change: Callable[[], None] | None = None

    # -------------------------------------------------------------- soap
    async def soap(
        self, ip: str, service: str, action: str, args: list[tuple[str, Any]] | None = None
    ) -> dict[str, str]:
        urn = f"urn:schemas-upnp-org:service:{service}:1"
        body = _envelope(service, action, args or [])
        try:
            res = await self.http.post(
                f"http://{ip}:{PORT}{SERVICES[service]}",
                content=body.encode(),
                headers={
                    "Content-Type": 'text/xml; charset="utf-8"',
                    "SOAPACTION": f'"{urn}#{action}"',
                },
                timeout=5,
            )
        except httpx.HTTPError as exc:
            raise SonosError(502, f"The Sonos speaker at {ip} did not answer.") from exc
        if res.status_code != 200:
            raise SonosError(502, f"The Sonos speaker refused {action} ({res.status_code}).")
        return _parse_response(res.text, action)

    # -------------------------------------------------------------- state
    async def refresh(self, *, force: bool = False) -> None:
        now = self.clock()
        if force or now - self._discovered_at >= DISCOVER_EVERY:
            self._discovered_at = now
            with contextlib.suppress(Exception):
                self.known_ips |= await self.discover()
        if not self.known_ips and not self.speakers:
            return
        await self._read_topology()
        for coord in list(self.groups):
            with contextlib.suppress(SonosError):
                await self._read_group_state(coord)
        for uid in list(self.speakers):
            with contextlib.suppress(SonosError):
                await self._read_volume(uid)
        self._polled_at = self.clock()

    async def _read_topology(self) -> None:
        ips = [s["ip"] for s in self.speakers.values() if s.get("available")] or sorted(
            self.known_ips
        )
        for ip in ips:
            try:
                res = await self.soap(ip, "ZoneGroupTopology", "GetZoneGroupState")
            except SonosError:
                continue
            self._apply_topology(res.get("ZoneGroupState") or "")
            return
        for s in self.speakers.values():
            s["available"] = False

    def _apply_topology(self, xml: str) -> None:
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return
        seen: set[str] = set()
        groups: dict[str, list[str]] = {}
        for group in root.iter("ZoneGroup"):
            coord = group.get("Coordinator") or ""
            members: list[str] = []
            for m in group.iter("ZoneGroupMember"):
                if m.get("Invisible") == "1":  # stereo pair / sub satellites
                    continue
                uid = m.get("UUID") or ""
                loc = urlparse(m.get("Location") or "")
                if not uid or not loc.hostname:
                    continue
                speaker = self.speakers.setdefault(uid, {"uid": uid, "volume": 0, "muted": False})
                speaker.update(
                    ip=loc.hostname, name=m.get("ZoneName") or "Sonos", available=True, coord=coord
                )
                self.known_ips.add(loc.hostname)
                members.append(uid)
                seen.add(uid)
            if coord and coord in members:
                groups[coord] = [coord, *[u for u in members if u != coord]]
        for uid, s in self.speakers.items():
            if uid not in seen:
                s["available"] = False
        self.groups = groups

    async def _read_group_state(self, coord: str) -> None:
        sp = self.speakers[coord]
        ip = sp["ip"]
        info = await self.soap(ip, "AVTransport", "GetTransportInfo", [("InstanceID", 0)])
        pos = await self.soap(ip, "AVTransport", "GetPositionInfo", [("InstanceID", 0)])
        np = parse_didl(pos.get("TrackMetaData"), f"http://{ip}:{PORT}")
        uri = pos.get("TrackURI") or ""
        if uri.startswith(("x-rincon-mp3radio:", "x-sonosapi-stream:", "aac:")) or not np.get(
            "title"
        ):
            with contextlib.suppress(SonosError):
                media = await self.soap(ip, "AVTransport", "GetMediaInfo", [("InstanceID", 0)])
                station = parse_didl(media.get("CurrentURIMetaData"), f"http://{ip}:{PORT}")
                if station.get("title"):
                    np.setdefault("station_name", station["title"])
                    if not np.get("title"):
                        np["title"] = station["title"]
        np["position"] = _secs(pos.get("RelTime"))
        np["duration"] = _secs(pos.get("TrackDuration"))
        sp["state"] = STATES.get(info.get("CurrentTransportState", ""), "idle")
        sp["np"] = np

    async def _read_volume(self, uid: str) -> None:
        sp = self.speakers[uid]
        if not sp.get("available"):
            return
        vol = await self.soap(
            sp["ip"], "RenderingControl", "GetVolume", [("InstanceID", 0), ("Channel", "Master")]
        )
        mute = await self.soap(
            sp["ip"], "RenderingControl", "GetMute", [("InstanceID", 0), ("Channel", "Master")]
        )
        with contextlib.suppress(ValueError):
            sp["volume"] = int(vol.get("CurrentVolume", sp.get("volume", 0)))
        sp["muted"] = mute.get("CurrentMute") == "1"

    async def run(self) -> None:
        while True:
            try:
                await self.refresh()
                if self.on_change is not None:
                    self.on_change()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.debug("Sonos refresh failed", exc_info=True)
            await asyncio.sleep(POLL_ACTIVE if self.watching() else POLL_IDLE)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    # -------------------------------------------------------------- views
    def _coord_of(self, uid: str) -> str:
        for coord, members in self.groups.items():
            if uid in members:
                return coord
        return uid

    def _group_name(self, coord: str) -> str:
        members = self.groups.get(coord, [coord])
        name = self.speakers.get(coord, {}).get("name", "Sonos")
        return name if len(members) <= 1 else f"{name} + {len(members) - 1}"

    def rooms(self) -> list[dict[str, Any]]:
        out = []
        for uid, sp in self.speakers.items():
            coord = self._coord_of(uid)
            lead = self.speakers.get(coord, sp)
            members = self.groups.get(coord, [uid])
            np = lead.get("np") or {}
            out.append(
                {
                    "id": PREFIX + uid,
                    "name": sp.get("name", "Sonos"),
                    "available": bool(sp.get("available")),
                    "volume": int(sp.get("volume", 0)),
                    "muted": bool(sp.get("muted")),
                    "group_id": PREFIX + coord,
                    "group_name": self._group_name(coord),
                    "is_group": False,
                    "member_ids": [PREFIX + m for m in members] if len(members) > 1 else [],
                    "state": lead.get("state", "idle"),
                    "leader_id": PREFIX + coord,
                    "relaying_for": None,
                    "model": "Sonos",
                    "kind": "sonos",
                    "ip": sp.get("ip"),
                    "port": PORT,
                    "url": None,  # not a FreeSounder player: never connect the app to it
                    "hostname": None,
                    "version": None,
                    "power_warning": None,
                    "source": "sonos" if lead.get("state", "idle") != "idle" else "none",
                    "now_playing": {
                        k: np.get(k) for k in ("title", "artist", "album", "image_url")
                    },
                }
            )
        return out

    def now_playing(self, room_id: str) -> dict[str, Any]:
        uid = uid_of(room_id)
        coord = self._coord_of(uid)
        lead = self.speakers.get(coord) or {}
        np = lead.get("np") or {}
        state = lead.get("state", "idle")
        return {
            "player_id": room_id,
            "group_id": PREFIX + coord,
            "group_name": self._group_name(coord),
            "source": "sonos" if state != "idle" else "none",
            "state": state,
            "title": np.get("title"),
            "artist": np.get("artist"),
            "album": np.get("album"),
            "image_url": np.get("image_url"),
            "station_name": np.get("station_name"),
            "position": np.get("position"),
            "duration": np.get("duration"),
            "meta_from": self.speakers.get(coord, {}).get("name"),
        }

    # -------------------------------------------------------------- controls
    def _speaker(self, room_id: str) -> dict[str, Any]:
        sp = self.speakers.get(uid_of(room_id))
        if sp is None:
            raise SonosError(404, "Unknown Sonos speaker")
        if not sp.get("available"):
            raise SonosError(503, f"{sp.get('name', 'The Sonos speaker')} is not reachable.")
        return sp

    def _coordinator(self, room_or_group: str) -> dict[str, Any]:
        return self._speaker(PREFIX + self._coord_of(uid_of(room_or_group)))

    async def control(self, gid: str, action: str) -> None:
        sp = self._coordinator(gid)
        ip = sp["ip"]
        if action == "toggle":
            action = "pause" if sp.get("state") == "playing" else "play"
        calls = {
            "play": ("Play", [("InstanceID", 0), ("Speed", 1)]),
            "pause": ("Pause", [("InstanceID", 0)]),
            "stop": ("Stop", [("InstanceID", 0)]),
            "next": ("Next", [("InstanceID", 0)]),
            "previous": ("Previous", [("InstanceID", 0)]),
        }
        if action not in calls:
            raise SonosError(400, f"Unknown action {action}")
        name, args = calls[action]
        await self.soap(ip, "AVTransport", name, args)
        if action in ("play", "pause", "stop"):
            sp["state"] = {"play": "playing", "pause": "paused", "stop": "idle"}[action]

    async def seek(self, gid: str, position_ms: int) -> None:
        sp = self._coordinator(gid)
        await self.soap(
            sp["ip"],
            "AVTransport",
            "Seek",
            [("InstanceID", 0), ("Unit", "REL_TIME"), ("Target", _hms(position_ms / 1000))],
        )

    async def set_volume(self, room_id: str, volume: int | None, muted: bool | None) -> None:
        sp = self._speaker(room_id)
        if volume is not None:
            level = max(0, min(100, int(volume)))
            await self.soap(
                sp["ip"],
                "RenderingControl",
                "SetVolume",
                [("InstanceID", 0), ("Channel", "Master"), ("DesiredVolume", level)],
            )
            sp["volume"] = level
        if muted is not None:
            await self.soap(
                sp["ip"],
                "RenderingControl",
                "SetMute",
                [("InstanceID", 0), ("Channel", "Master"), ("DesiredMute", 1 if muted else 0)],
            )
            sp["muted"] = bool(muted)

    async def set_group_volume(self, gid: str, volume: int | None, muted: bool | None) -> None:
        coord = self._coord_of(uid_of(gid))
        members = self.groups.get(coord, [coord])
        levels = [int(self.speakers[m].get("volume", 0)) for m in members if m in self.speakers]
        avg = round(sum(levels) / len(levels)) if levels else 0
        delta = 0 if volume is None else int(volume) - avg
        await asyncio.gather(
            *[
                self.set_volume(
                    PREFIX + m,
                    None
                    if volume is None
                    else max(0, min(100, int(self.speakers[m].get("volume", 0)) + delta)),
                    muted,
                )
                for m in members
                if m in self.speakers
            ]
        )

    async def play_stream(self, gid: str, url: str, title: str, art: str | None = None) -> None:
        """A live stream (radio) on the group."""

        sp = self._coordinator(gid)
        meta = didl_item(title, upnp_class="object.item.audioItem.audioBroadcast", art=art)
        await self.soap(
            sp["ip"],
            "AVTransport",
            "SetAVTransportURI",
            [("InstanceID", 0), ("CurrentURI", radio_uri(url)), ("CurrentURIMetaData", meta)],
        )
        await self.soap(sp["ip"], "AVTransport", "Play", [("InstanceID", 0), ("Speed", 1)])
        sp["state"] = "playing"

    async def play_tracks(
        self, gid: str, tracks: list[dict[str, Any]], start: int = 0, seek: float = 0
    ) -> None:
        """Files (library songs, podcast episodes) through the group's queue."""

        if not tracks:
            raise SonosError(400, "Nothing to play.")
        sp = self._coordinator(gid)
        ip = sp["ip"]
        await self.soap(ip, "AVTransport", "RemoveAllTracksFromQueue", [("InstanceID", 0)])
        for t in tracks[:200]:
            url = str(t["url"])
            meta = didl_item(
                str(t.get("title") or url.rsplit("/", 1)[-1]),
                uri=url,
                artist=t.get("artist"),
                album=t.get("album"),
                art=t.get("image_url"),
                mime=_mime_for(str(t.get("uri") or url)),
            )
            await self.soap(
                ip,
                "AVTransport",
                "AddURIToQueue",
                [
                    ("InstanceID", 0),
                    ("EnqueuedURI", url),
                    ("EnqueuedURIMetaData", meta),
                    ("DesiredFirstTrackNumberEnqueued", 0),
                    ("EnqueueAsNext", 0),
                ],
            )
        await self.soap(
            ip,
            "AVTransport",
            "SetAVTransportURI",
            [
                ("InstanceID", 0),
                ("CurrentURI", f"x-rincon-queue:{sp['uid']}#0"),
                ("CurrentURIMetaData", ""),
            ],
        )
        first = max(0, min(int(start), len(tracks) - 1)) + 1
        await self.soap(
            ip, "AVTransport", "Seek", [("InstanceID", 0), ("Unit", "TRACK_NR"), ("Target", first)]
        )
        if seek and seek > 1:
            with contextlib.suppress(SonosError):
                await self.soap(
                    ip,
                    "AVTransport",
                    "Seek",
                    [("InstanceID", 0), ("Unit", "REL_TIME"), ("Target", _hms(seek))],
                )
        await self.soap(ip, "AVTransport", "Play", [("InstanceID", 0), ("Speed", 1)])
        sp["state"] = "playing"

    # -------------------------------------------------------------- grouping
    async def join(self, room_id: str, target: str) -> None:
        coord = self._coordinator(target)
        await self._join_uid(room_id, coord["uid"])

    async def _join_uid(self, room_id: str, coord_uid: str) -> None:
        sp = self._speaker(room_id)
        if sp["uid"] == coord_uid:
            return
        await self.soap(
            sp["ip"],
            "AVTransport",
            "SetAVTransportURI",
            [
                ("InstanceID", 0),
                ("CurrentURI", f"x-rincon:{coord_uid}"),
                ("CurrentURIMetaData", ""),
            ],
        )

    async def leave(self, room_id: str) -> None:
        sp = self._speaker(room_id)
        await self.soap(
            sp["ip"], "AVTransport", "BecomeCoordinatorOfStandaloneGroup", [("InstanceID", 0)]
        )

    async def set_members(self, gid: str, members: list[str]) -> None:
        """Make the Sonos group exactly [members] (Sonos speakers only)."""

        if any(not is_sonos(m) for m in members):
            raise SonosError(
                409,
                "Sonos speakers group only with other Sonos speakers; they keep their own "
                "timing, so they cannot play in step with FreeSounder players.",
            )
        if not members:
            return
        coord = PREFIX + self._coord_of(uid_of(gid))
        current = {PREFIX + u for u in self.groups.get(uid_of(coord), [uid_of(coord)])}
        wanted = set(members)
        lead = coord if coord in wanted else members[0]
        if lead != coord:
            await self.leave(lead)  # it leads a group of its own now
        for rid in members:
            if rid != lead and (rid not in current or lead != coord):
                await self._join_uid(rid, uid_of(lead))
        for rid in sorted(current - wanted):
            if rid != lead:
                await self.leave(rid)
        await self.refresh()

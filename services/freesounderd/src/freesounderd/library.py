"""Music library from network shares (SMB/NFS) and USB drives, indexed by MPD.

A share is added on one player (its "library player"); that player mounts it
read-only under ``music_dir/<id>`` and MPD indexes it. Browsing and search go
to that player. Any group leader plays tracks by URL from the library player
(``/api/v2/library/file``, a per-file household token), so every room can play
the library and only one board scans the NAS.

Passwords are written only to a root-only credentials file for mount.cifs;
they are never returned by the API and never logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import mimetypes
import os
import re
import secrets
from collections import OrderedDict
from pathlib import Path
from typing import Any

from freesounderd.mpd import MpdError, pairs_to_dicts

logger = logging.getLogger(__name__)

KINDS = ("smb", "nfs", "usb")
_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-:]{0,252}$")
_SHARE_RE = re.compile(r"^[^\x00-\x1f,\"'\\]{1,255}$")
_UUID_RE = re.compile(r"^[A-Za-z0-9\-]{4,64}$")
COVER_NAMES = ("cover", "folder", "front", "album", "albumart", "albumartsmall", "thumb")
COVER_EXT = (".jpg", ".jpeg", ".png", ".webp")
ART_CACHE = 24


class LibraryError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _int(value: Any) -> int:
    m = re.match(r"\d+", str(value or ""))
    return int(m.group(0)) if m else 0


def track_of(rec: dict[str, str]) -> dict[str, Any]:
    uri = rec.get("file", "")
    duration = rec.get("duration") or rec.get("Time")
    try:
        dur = round(float(duration), 1) if duration else None
    except ValueError:
        dur = None
    return {
        "uri": uri,
        "title": rec.get("Title") or Path(uri).stem,
        "artist": rec.get("Artist") or rec.get("AlbumArtist"),
        "album": rec.get("Album"),
        "albumartist": rec.get("AlbumArtist") or rec.get("Artist"),
        "track": _int(rec.get("Track")),
        "disc": _int(rec.get("Disc")),
        "duration": dur,
    }


def _check(value: str, pattern: re.Pattern[str], what: str) -> str:
    value = (value or "").strip()
    if not pattern.match(value) or ".." in value.split("/"):
        raise LibraryError(400, f"That {what} does not look right.")
    return value


def mount_argv(share: dict[str, Any], target: Path, cred: Path) -> list[str]:
    kind = share["kind"]
    if kind == "smb":
        auth = f"credentials={cred}" if share.get("username") else "guest"
        opts = (
            f"ro,{auth},iocharset=utf8,soft,noperm,file_mode=0444,dir_mode=0555,"
            "nobrl,actimeo=30,echo_interval=15"
        )
        source = f"//{share['host']}/{share['share'].strip('/')}"
        return ["mount", "-t", "cifs", source, str(target), "-o", opts]
    if kind == "nfs":
        export = "/" + share["share"].strip("/")
        opts = "ro,soft,timeo=100,retrans=2,nolock,noatime"
        return ["mount", "-t", "nfs", f"{share['host']}:{export}", str(target), "-o", opts]
    return ["mount", "-o", "ro,noatime", f"UUID={share['uuid']}", str(target)]


def friendly_mount_error(text: str) -> str:
    low = text.lower()
    if "permission denied" in low or "error(13)" in low:
        return "The share refused the user name or password."
    if "no such file" in low or "error(2)" in low or "does not exist" in low:
        return "The share name was not found on that computer."
    if "host is down" in low or "error(112)" in low or "no route" in low or "timed out" in low:
        return "Could not reach that computer."
    if "could not resolve" in low or "resolve address" in low:
        return "Could not find a computer with that name. Try its IP address."
    if "unknown filesystem" in low or "wrong fs type" in low:
        return "This player cannot read that kind of share or drive."
    line = text.strip().splitlines()[-1] if text.strip() else "mount failed"
    return line[:200]


class Library:
    def __init__(
        self,
        *,
        state_dir: Path,
        music_dir: Path,
        mpd: Any,
        managed: bool,
    ) -> None:
        self.state_dir = state_dir
        self.music_dir = music_dir
        self.mpd = mpd
        self.managed = managed
        self.status: dict[str, dict[str, Any]] = {}  # id -> {"mounted": bool, "error": str}
        self._art: OrderedDict[str, tuple[bytes, str]] = OrderedDict()
        self._mount_lock = asyncio.Lock()

    # ------------------------------------------------------------ config
    @property
    def _path(self) -> Path:
        return self.state_dir / "library.json"

    def _cred(self, sid: str) -> Path:
        return self.state_dir / "library" / f"{sid}.cred"

    def shares(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [s for s in data.get("shares", []) if isinstance(s, dict) and s.get("id")]

    def _save(self, shares: list[dict[str, Any]]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"shares": shares}, fh, indent=2)
        tmp.replace(self._path)

    def public(self) -> list[dict[str, Any]]:
        out = []
        for share in self.shares():
            item = {k: share.get(k) for k in ("id", "kind", "name", "host", "share", "username")}
            item["has_password"] = self._cred(share["id"]).exists()
            item.update(self.status.get(share["id"], {"mounted": False, "error": None}))
            out.append(item)
        return out

    def _write_cred(self, sid: str, username: str, password: str, domain: str) -> None:
        path = self._cred(sid)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lines = [f"username={username}", f"password={password}"]
        if domain:
            lines.append(f"domain={domain}")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")

    def validate(self, body: dict[str, Any]) -> dict[str, Any]:
        kind = str(body.get("kind") or "")
        if kind not in KINDS:
            raise LibraryError(400, "Pick a network share (SMB or NFS) or a USB drive.")
        share: dict[str, Any] = {"kind": kind}
        if kind == "usb":
            share["uuid"] = _check(str(body.get("uuid") or ""), _UUID_RE, "drive")
            share["host"] = "usb"
            share["share"] = share["uuid"]
            share["name"] = str(body.get("name") or "USB drive")[:60]
            return share
        share["host"] = _check(
            str(body.get("host") or "").removeprefix("\\\\"), _HOST_RE, "address"
        )
        share["share"] = _check(
            str(body.get("share") or "").replace("\\", "/").strip("/"), _SHARE_RE, "share name"
        )
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "")
        for value in (username, password, str(body.get("domain") or "")):
            if "\n" in value or "\r" in value or "\x00" in value:
                raise LibraryError(400, "User names and passwords cannot contain line breaks.")
        if kind == "nfs" and (username or password):
            raise LibraryError(400, "NFS shares do not use a user name or password.")
        share["username"] = username or None
        share["_password"] = password
        share["_domain"] = str(body.get("domain") or "").strip()
        default = share["share"].rsplit("/", 1)[-1] or share["host"]
        share["name"] = (str(body.get("name") or "").strip() or default)[:60]
        return share

    # ------------------------------------------------------------ add / remove
    async def add(self, body: dict[str, Any]) -> dict[str, Any]:
        share = self.validate(body)
        shares = self.shares()
        if any(
            s.get("kind") == share["kind"]
            and s.get("host") == share["host"]
            and s.get("share") == share["share"]
            for s in shares
        ):
            raise LibraryError(409, "That share is already in the library.")
        if len(shares) >= 8:
            raise LibraryError(409, "A player can hold up to 8 shares.")
        sid = f"{share['kind']}-{secrets.token_hex(3)}"
        share["id"] = sid
        password = share.pop("_password", "")
        domain = share.pop("_domain", "")
        if share.get("username"):
            self._write_cred(sid, share["username"], password, domain)
        ok, error = await self._mount(share)
        if not ok:
            with contextlib.suppress(OSError):
                self._cred(sid).unlink()
            raise LibraryError(502, error or "Could not open that share.")
        self._save([*shares, share])
        await self.rescan(sid)
        return {k: v for k, v in share.items() if not k.startswith("_")}

    async def remove(self, sid: str) -> None:
        shares = self.shares()
        share = next((s for s in shares if s["id"] == sid), None)
        if share is None:
            raise LibraryError(404, "No such share.")
        await self._umount(sid)
        self._save([s for s in shares if s["id"] != sid])
        with contextlib.suppress(OSError):
            self._cred(sid).unlink()
        self.status.pop(sid, None)
        self._art.clear()
        with contextlib.suppress(MpdError):
            await self.mpd.command("update")

    # ------------------------------------------------------------ mounting
    def _target(self, sid: str) -> Path:
        return self.music_dir / sid

    async def _run(self, argv: list[str], limit: float = 30) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            "timeout",
            str(int(limit)),
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        return proc.returncode or 0, out.decode("utf-8", "replace")

    async def _is_mounted(self, target: Path) -> bool:
        if not self.managed:
            return self.status.get(target.name, {}).get("mounted", False)
        try:
            text = await asyncio.to_thread(Path("/proc/mounts").read_text, encoding="utf-8")
        except OSError:
            return False
        return any(line.split()[1] == str(target) for line in text.splitlines() if line.strip())

    async def _mount(self, share: dict[str, Any]) -> tuple[bool, str | None]:
        sid = share["id"]
        target = self._target(sid)
        async with self._mount_lock:
            if not self.managed:
                target.mkdir(parents=True, exist_ok=True)
                self.status[sid] = {"mounted": True, "error": None}
                return True, None
            if await self._is_mounted(target):
                self.status[sid] = {"mounted": True, "error": None}
                return True, None
            await asyncio.to_thread(target.mkdir, parents=True, exist_ok=True)
            rc, out = await self._run(mount_argv(share, target, self._cred(sid)))
            if rc == 0:
                self.status[sid] = {"mounted": True, "error": None}
                logger.info("Mounted library share %s (%s)", share.get("name"), share["kind"])
                return True, None
            error = "Could not reach that computer." if rc == 124 else friendly_mount_error(out)
            # The mount output never contains the password (it is in a file).
            logger.warning("Mounting %s failed: %s", share.get("name"), error)
            self.status[sid] = {"mounted": False, "error": error}
            return False, error

    async def _umount(self, sid: str) -> None:
        target = self._target(sid)
        if self.managed and await self._is_mounted(target):
            await self._run(["umount", "-l", str(target)], 20)
        with contextlib.suppress(OSError):
            target.rmdir()

    async def mount_all(self) -> None:
        for share in self.shares():
            await self._mount(share)

    async def rescan(self, sid: str | None = None) -> None:
        try:
            if sid:
                await self.mpd.command("update", sid)
            else:
                await self.mpd.command("update")
        except MpdError as exc:
            logger.info("MPD update failed: %s", exc)
        self._art.clear()

    async def usb_drives(self) -> list[dict[str, Any]]:
        if not self.managed:
            return []
        rc, out = await self._run(
            ["lsblk", "-J", "-o", "NAME,UUID,LABEL,FSTYPE,TRAN,SIZE,MOUNTPOINT"], 10
        )
        if rc != 0:
            return []
        try:
            devices = json.loads(out).get("blockdevices", [])
        except ValueError:
            return []
        found: list[dict[str, Any]] = []

        def walk(nodes: list[dict[str, Any]], usb: bool) -> None:
            for node in nodes:
                is_usb = usb or node.get("tran") == "usb"
                if is_usb and node.get("uuid") and node.get("fstype"):
                    found.append(
                        {
                            "uuid": node["uuid"],
                            "name": node.get("label") or f"USB drive ({node.get('size')})",
                            "fstype": node["fstype"],
                            "size": node.get("size"),
                        }
                    )
                walk(node.get("children") or [], is_usb)

        walk(devices, False)
        return found

    # ------------------------------------------------------------ queries
    async def summary(self) -> dict[str, Any]:
        stats: dict[str, Any] = {}
        updating = False
        try:
            raw = dict(await self.mpd.command("stats"))
            stats = {k: _int(raw.get(k)) for k in ("artists", "albums", "songs")}
            updating = "updating_db" in await self.mpd.status()
        except MpdError as exc:
            stats = {"error": str(exc)}
        return {"shares": self.public(), "stats": stats, "scanning": updating}

    def _name_of(self, sid: str) -> str:
        for share in self.shares():
            if share["id"] == sid:
                return str(share.get("name") or sid)
        return sid

    async def artists(self) -> list[str]:
        pairs = await self.mpd.command("list", "albumartist")
        names = {v for k, v in pairs if k.lower() == "albumartist" and v.strip()}
        return sorted(names, key=str.casefold)

    async def albums(self, artist: str | None = None) -> list[dict[str, Any]]:
        if artist:
            pairs = await self.mpd.command("list", "album", "albumartist", artist)
            return sorted(
                ({"album": v, "artist": artist} for k, v in pairs if k == "Album" and v),
                key=lambda a: a["album"].casefold(),
            )
        pairs = await self.mpd.command("list", "album", "group", "albumartist")
        out: list[dict[str, Any]] = []
        current = ""
        for key, value in pairs:
            if key == "AlbumArtist":
                current = value
            elif key == "Album" and value:
                out.append({"album": value, "artist": current or None})
        out.sort(key=lambda a: (a["album"].casefold(), (a["artist"] or "").casefold()))
        return out

    async def album_tracks(self, album: str, artist: str | None = None) -> list[dict[str, Any]]:
        args: list[str] = ["album", album]
        if artist:
            args += ["albumartist", artist]
        recs = pairs_to_dicts(await self.mpd.command("find", *args), "file")
        tracks = [track_of(r) for r in recs if r.get("file")]
        tracks.sort(key=lambda t: (t["disc"], t["track"], t["uri"]))
        return tracks

    async def folder(self, path: str = "") -> dict[str, Any]:
        path = path.strip("/")
        if ".." in path.split("/"):
            raise LibraryError(400, "Bad folder")
        pairs = await self.mpd.command("lsinfo", path) if path else await self.mpd.command("lsinfo")
        folders: list[dict[str, Any]] = []
        tracks: list[dict[str, Any]] = []
        rec: dict[str, str] = {}

        def flush() -> None:
            if "directory" in rec:
                d = rec["directory"]
                name = self._name_of(d) if "/" not in d else d.rsplit("/", 1)[-1]
                folders.append({"path": d, "name": name})
            elif "file" in rec:
                tracks.append(track_of(rec))

        for key, value in pairs:
            if key in ("directory", "file", "playlist"):
                flush()
                rec = {}
            rec.setdefault(key, value)
        flush()
        folders.sort(key=lambda f: f["name"].casefold())
        tracks.sort(key=lambda t: (t["disc"], t["track"], t["uri"]))
        return {"path": path, "folders": folders, "tracks": tracks}

    async def search(self, query: str, limit: int = 150) -> dict[str, Any]:
        query = query.strip()
        if not query:
            return {"artists": [], "albums": [], "tracks": []}
        recs = pairs_to_dicts(
            await self.mpd.command("search", "any", query, "window", f"0:{limit}"), "file"
        )
        tracks = [track_of(r) for r in recs if r.get("file")]
        low = query.casefold()
        artists = sorted(
            {
                t["albumartist"]
                for t in tracks
                if t["albumartist"] and low in t["albumartist"].casefold()
            },
            key=str.casefold,
        )
        albums: dict[tuple[str, str], dict[str, Any]] = {}
        for t in tracks:
            if t["album"] and (low in t["album"].casefold() or t["albumartist"] in artists):
                albums.setdefault(
                    (t["album"], t["albumartist"] or ""),
                    {"album": t["album"], "artist": t["albumartist"]},
                )
        return {"artists": artists[:30], "albums": list(albums.values())[:60], "tracks": tracks}

    # ------------------------------------------------------------ files and art
    def file_path(self, uri: str) -> Path:
        root = self.music_dir.resolve()
        path = (self.music_dir / uri.lstrip("/")).resolve()
        if root not in path.parents or not path.is_file():
            raise LibraryError(404, "Not in the library")
        return path

    async def art(
        self, uri: str | None, album: str | None, artist: str | None
    ) -> tuple[bytes, str]:
        if not uri and album:
            tracks = await self.album_tracks(album, artist)
            uri = tracks[0]["uri"] if tracks else None
        if not uri:
            raise LibraryError(404, "No cover")
        folder = str(Path(uri).parent)
        cached = self._art.get(folder)
        if cached:
            self._art.move_to_end(folder)
            return cached
        found: tuple[bytes, str] | None = None
        for command in ("readpicture", "albumart"):
            try:
                data, mime = await self.mpd.binary(command, uri)
            except MpdError:
                continue
            if data:
                found = (data, mime or "image/jpeg")
                break
        if found is None:
            found = await asyncio.to_thread(self._folder_image, folder)
        if found is None:
            raise LibraryError(404, "No cover")
        self._art[folder] = found
        while len(self._art) > ART_CACHE:
            self._art.popitem(last=False)
        return found

    def _folder_image(self, folder: str) -> tuple[bytes, str] | None:
        root = self.music_dir.resolve()
        directory = (self.music_dir / folder).resolve()
        if directory != root and root not in directory.parents:
            return None
        try:
            names = os.listdir(directory)
        except OSError:
            return None
        images = [n for n in names if n.lower().endswith(COVER_EXT)]

        def rank(name: str) -> tuple[int, str]:
            stem = Path(name).stem.lower()
            for i, want in enumerate(COVER_NAMES):
                if stem == want or stem.startswith(want):
                    return (i, name)
            return (len(COVER_NAMES), name)

        for name in sorted(images, key=rank)[:1]:
            path = directory / name
            try:
                if path.stat().st_size > 6_000_000:
                    return None
                data = path.read_bytes()
            except OSError:
                return None
            return data, mimetypes.guess_type(name)[0] or "image/jpeg"
        return None

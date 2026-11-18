"""Now-playing artwork helpers: radio album art lookup and AirPlay cover art."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
import time
from collections import OrderedDict
from typing import Any

import httpx

logger = logging.getLogger(__name__)

ITUNES_SEARCH = "https://itunes.apple.com/search"
_JUNK = re.compile(r"(?i)\b(advert|commercial|station id|jingle|news|traffic|weather)\b")


def split_stream_title(title: str | None) -> tuple[str | None, str | None]:
    """ICY "Artist - Title" -> (artist, title). (None, title) when not split."""

    if not title:
        return None, None
    text = title.strip().strip("'\"")
    for sep in (" - ", " – ", " — ", " ~ "):
        if sep in text:
            artist, song = text.split(sep, 1)
            artist, song = artist.strip(), song.strip()
            if artist and song:
                return artist, song
    return None, text or None


class ArtLookup:
    """iTunes Search album art for radio tracks, cached in memory.

    ``cached`` never blocks: an unknown key starts a background lookup and
    returns None; the result shows up on a later tick.
    """

    MISSING = object()
    RETRY_AFTER = 60.0  # seconds before a failed (network) lookup is tried again

    def __init__(self, client: httpx.AsyncClient, *, enabled: bool = True, size: int = 300) -> None:
        self._client = client
        self.enabled = enabled
        self._cache: OrderedDict[str, str | None] = OrderedDict()
        self._inflight: set[str] = set()
        self._failed: dict[str, float] = {}  # key -> monotonic time of a network error
        self._size = size
        self.on_found: Any = None  # callback(), e.g. engine.kick

    @staticmethod
    def key(artist: str, song: str) -> str:
        return f"{artist.lower()}\x00{song.lower()}"

    def cached(self, artist: str | None, song: str | None) -> str | None:
        if not self.enabled or not artist or not song or _JUNK.search(f"{artist} {song}"):
            return None
        key = self.key(artist, song)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        failed = self._failed.get(key)
        if failed is not None and time.monotonic() - failed < self.RETRY_AFTER:
            return None
        if key not in self._inflight:
            self._inflight.add(key)
            with _suppress_no_loop():
                asyncio.get_running_loop().create_task(self._fill(key, artist, song))
        return None

    async def _fill(self, key: str, artist: str, song: str) -> None:
        try:
            url = await self.lookup(artist, song)
        except _LookupError:
            # Network trouble: no art for now, try again later (not cached).
            self._failed[key] = time.monotonic()
            if len(self._failed) > self._size:
                self._failed.clear()
            return
        finally:
            self._inflight.discard(key)
        self._failed.pop(key, None)
        self._cache[key] = url
        while len(self._cache) > self._size:
            self._cache.popitem(last=False)
        if url and self.on_found:
            self.on_found()

    async def lookup(self, artist: str, song: str) -> str | None:
        try:
            res = await self._client.get(
                ITUNES_SEARCH,
                params={"term": f"{artist} {song}", "media": "music", "entity": "song", "limit": 5},
                timeout=6,
            )
            if res.status_code != 200:
                raise _LookupError(res.status_code)
            data = res.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise _LookupError(str(exc)) from exc
        results = data.get("results") or []
        wanted = artist.lower()
        best = next(
            (r for r in results if wanted in str(r.get("artistName", "")).lower()),
            results[0] if results else None,
        )
        art = (best or {}).get("artworkUrl100")
        if not art:
            return None
        return re.sub(r"/\d+x\d+bb\.", "/600x600bb.", art)


class _LookupError(Exception):
    """iTunes did not answer (network, rate limit): retry later."""


class _suppress_no_loop:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return exc_type is RuntimeError


class AirplayArt:
    """Cover art for the AirPlay stream, from snapserver's metadata."""

    def __init__(self) -> None:
        self.data: bytes | None = None
        self.mime = "image/jpeg"
        self.tag: str | None = None
        self.track: str | None = None  # the song the current picture belongs to
        self.album = ""
        self.source_path: str | None = None

    def update(self, meta: dict[str, Any]) -> str | None:
        """Remember the cover in ``meta``; returns a short tag for the URL.

        Never hands the previous song's cover to a new song: AirPlay senders
        send the title first and the picture a moment later, and snapserver
        keeps the old picture until then. A new track with no picture yet, or
        with the very same picture but a different album, gets no art (the
        app shows the fallback) until its own cover arrives.
        """

        track = _track_key(meta)
        album = str(meta.get("album") or "").strip().lower()
        tag: str | None = None
        art = meta.get("artData")
        url = meta.get("artUrl")
        if isinstance(art, dict) and art.get("data"):
            raw = str(art["data"])
            tag = hashlib.sha1(raw.encode()).hexdigest()[:12]
            if tag != self.tag:
                try:
                    data = base64.b64decode(raw)
                except ValueError:
                    return None
                ext = str(art.get("extension") or "jpg").lower().lstrip(".")
                self.data, self.source_path = data, None
                self.mime = "image/png" if ext == "png" else "image/jpeg"
        elif isinstance(url, str) and url:
            path = httpx.URL(url).raw_path.decode() if "://" in url else url
            if not path.startswith("/__image_cache"):
                return None
            tag = hashlib.sha1(path.encode()).hexdigest()[:12]
            if tag != self.tag:
                self.source_path, self.data = path, None
        if tag is None:
            return self.tag if track == self.track else None
        if tag == self.tag and track != self.track and album != self.album:
            return None  # same picture as the last song, different album: stale
        self.tag, self.track, self.album = tag, track, album
        return self.tag


def _track_key(meta: dict[str, Any]) -> str:
    artist = meta.get("artist")
    if isinstance(artist, list):
        artist = ", ".join(str(a) for a in artist)
    return "\x00".join(str(meta.get(k) or "").strip().lower() for k in ("title", "album")) + (
        "\x00" + str(artist or "").strip().lower()
    )

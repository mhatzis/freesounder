"""Now-playing artwork helpers: radio album art lookup and AirPlay cover art."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
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

    def __init__(self, client: httpx.AsyncClient, *, enabled: bool = True, size: int = 300) -> None:
        self._client = client
        self.enabled = enabled
        self._cache: OrderedDict[str, str | None] = OrderedDict()
        self._inflight: set[str] = set()
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
        if key not in self._inflight:
            self._inflight.add(key)
            with _suppress_no_loop():
                asyncio.get_running_loop().create_task(self._fill(key, artist, song))
        return None

    async def _fill(self, key: str, artist: str, song: str) -> None:
        try:
            url = await self.lookup(artist, song)
        finally:
            self._inflight.discard(key)
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
            data = res.json() if res.status_code == 200 else {}
        except (httpx.HTTPError, ValueError):
            return None
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
        self.source_path: str | None = None

    def update(self, meta: dict[str, Any]) -> str | None:
        """Remember the cover in ``meta``; returns a short tag for the URL."""

        art = meta.get("artData")
        if isinstance(art, dict) and art.get("data"):
            raw = str(art["data"])
            tag = hashlib.sha1(raw.encode()).hexdigest()[:12]
            if tag != self.tag:
                try:
                    self.data = base64.b64decode(raw)
                except ValueError:
                    return None
                ext = str(art.get("extension") or "jpg").lower().lstrip(".")
                self.mime = "image/png" if ext == "png" else "image/jpeg"
                self.tag = tag
                self.source_path = None
            return self.tag
        url = meta.get("artUrl")
        if isinstance(url, str) and url:
            path = httpx.URL(url).raw_path.decode() if "://" in url else url
            if not path.startswith("/__image_cache"):
                return None
            tag = hashlib.sha1(path.encode()).hexdigest()[:12]
            if tag != self.tag:
                self.tag, self.source_path, self.data = tag, path, None
            return self.tag
        return None

"""Podcasts: search (iTunes Search API, no key), RSS feeds, subscriptions.

Subscriptions and listening positions are one small household document,
copied between players like the rest of the home state: every player holds
it, and the newer half (subscriptions as a whole, each episode's position on
its own) wins when two players compare.
"""

from __future__ import annotations

import contextlib
import email.utils
import hashlib
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import httpx

from freesounderd import __version__

logger = logging.getLogger(__name__)

ITUNES_SEARCH = "https://itunes.apple.com/search"
FEED_TTL = 20 * 60
FEED_CACHE = 16
MAX_FEED_BYTES = 12_000_000
MAX_EPISODES = 300
MAX_POSITIONS = 400
MAX_SUBS = 200
ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
USER_AGENT = f"FreeSounder/{__version__} (podcasts)"


class PodcastError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def episode_key(feed: str, guid: str) -> str:
    return hashlib.sha1(f"{feed}\n{guid}".encode()).hexdigest()[:16]


def parse_duration(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    try:
        if ":" in value:
            secs = 0.0
            for part in value.split(":"):
                secs = secs * 60 + float(part)
            return secs
        return float(value)
    except ValueError:
        return None


def _strip_html(text: str | None, limit: int = 400) -> str | None:
    if not text:
        return None
    plain = re.sub(r"<[^>]+>", " ", text)
    plain = re.sub(r"\s+", " ", plain).strip()
    return plain[:limit] or None


def parse_feed(feed_url: str, raw: bytes) -> dict[str, Any]:
    head = raw[:4096].decode("utf-8", "replace")
    if "<!DOCTYPE" in head or "<!ENTITY" in raw.decode("utf-8", "replace"):
        raise PodcastError(502, "That feed is not a plain podcast feed.")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise PodcastError(502, "That podcast feed could not be read.") from exc
    channel = root.find("channel")
    if channel is None:
        raise PodcastError(502, "That is not a podcast feed.")
    it = f"{{{ITUNES_NS}}}"

    def image_of(node: ET.Element) -> str | None:
        img = node.find(f"{it}image")
        if img is not None and img.get("href"):
            return img.get("href")
        url = node.find("image/url")
        return url.text.strip() if url is not None and url.text else None

    podcast_image = image_of(channel)
    episodes: list[dict[str, Any]] = []
    for item in channel.findall("item"):
        enclosure = item.find("enclosure")
        url = enclosure.get("url") if enclosure is not None else None
        if not url:
            continue
        guid = (item.findtext("guid") or url).strip()
        date = item.findtext("pubDate")
        ts = None
        if date:
            with contextlib.suppress(TypeError, ValueError, IndexError):
                ts = email.utils.parsedate_to_datetime(date.strip()).timestamp()
        episodes.append(
            {
                "id": episode_key(feed_url, guid),
                "title": (item.findtext("title") or "Episode").strip(),
                "date": ts,
                "duration": parse_duration(item.findtext(f"{it}duration")),
                "url": url.strip(),
                "image": image_of(item) or podcast_image,
                "description": _strip_html(
                    item.findtext(f"{it}summary") or item.findtext("description")
                ),
            }
        )
        if len(episodes) >= MAX_EPISODES:
            break
    return {
        "feed": feed_url,
        "title": (channel.findtext("title") or "Podcast").strip(),
        "author": (channel.findtext(f"{it}author") or "").strip() or None,
        "image": podcast_image,
        "description": _strip_html(channel.findtext("description"), 600),
        "episodes": episodes,
    }


def merge_docs(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Subscriptions: newer list wins. Positions: newer per episode wins."""

    subs_from = a if float(a.get("subs_rev", 0)) >= float(b.get("subs_rev", 0)) else b
    pos: dict[str, Any] = dict(a.get("pos") or {})
    for key, value in (b.get("pos") or {}).items():
        if not isinstance(value, dict):
            continue
        if key not in pos or float(value.get("t", 0)) > float(pos[key].get("t", 0)):
            pos[key] = value
    if len(pos) > MAX_POSITIONS:
        keep = sorted(pos.items(), key=lambda kv: float(kv[1].get("t", 0)), reverse=True)
        pos = dict(keep[:MAX_POSITIONS])
    return {
        "subs_rev": float(subs_from.get("subs_rev", 0)),
        "subs": list(subs_from.get("subs") or [])[:MAX_SUBS],
        "pos": pos,
    }


def doc_rev(doc: dict[str, Any]) -> float:
    times = [float(v.get("t", 0)) for v in (doc.get("pos") or {}).values() if isinstance(v, dict)]
    return max([float(doc.get("subs_rev", 0)), *times])


class Podcasts:
    def __init__(self, state_dir: Path, http: httpx.AsyncClient) -> None:
        self.path = state_dir / "podcasts.json"
        self.http = http
        self._feeds: dict[str, tuple[float, dict[str, Any]]] = {}
        self._doc: dict[str, Any] | None = None

    # ------------------------------------------------------------ document
    def doc(self) -> dict[str, Any]:
        if self._doc is None:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            self._doc = merge_docs({"subs_rev": 0, "subs": [], "pos": {}}, data)
        return self._doc

    def rev(self) -> float:
        return doc_rev(self.doc())

    def _save(self, doc: dict[str, Any]) -> None:
        self._doc = doc
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc), encoding="utf-8")
        tmp.replace(self.path)

    def adopt(self, other: dict[str, Any]) -> bool:
        """A peer's copy: merge it in. True if ours changed."""

        mine = self.doc()
        merged = merge_docs(mine, other)
        if merged == mine:
            return False
        self._save(merged)
        return True

    def subscriptions(self) -> list[dict[str, Any]]:
        return list(self.doc()["subs"])

    def subscribe(self, item: dict[str, Any]) -> list[dict[str, Any]]:
        feed = str(item.get("feed") or "").strip()
        if not feed.startswith(("http://", "https://")):
            raise PodcastError(400, "That podcast has no feed address.")
        doc = dict(self.doc())
        subs = [s for s in doc["subs"] if s.get("feed") != feed]
        sub = {
            "feed": feed,
            "title": str(item.get("title") or "Podcast")[:200],
            "author": (str(item["author"])[:200] if item.get("author") else None),
            "image": item.get("image") or None,
        }
        doc["subs"] = [sub, *subs][:MAX_SUBS]
        doc["subs_rev"] = max(time.time(), float(doc.get("subs_rev", 0)) + 0.001)
        self._save(doc)
        return doc["subs"]

    def unsubscribe(self, feed: str) -> list[dict[str, Any]]:
        doc = dict(self.doc())
        doc["subs"] = [s for s in doc["subs"] if s.get("feed") != feed]
        doc["subs_rev"] = max(time.time(), float(doc.get("subs_rev", 0)) + 0.001)
        self._save(doc)
        return doc["subs"]

    def position(self, key: str) -> dict[str, Any] | None:
        return (self.doc().get("pos") or {}).get(key)

    def set_position(self, key: str, position: float, duration: float | None) -> None:
        doc = dict(self.doc())
        pos = dict(doc.get("pos") or {})
        done = bool(duration and position >= duration - 30)
        old = pos.get(key) or {}
        if abs(float(old.get("p", -99)) - position) < 1 and bool(old.get("done")) == done:
            return
        pos[key] = {
            "p": round(0.0 if done else position, 1),
            "d": round(duration, 1) if duration else None,
            "done": done,
            "t": time.time(),
        }
        doc["pos"] = pos
        self._save(merge_docs(doc, {}))

    def resume_at(self, key: str) -> float:
        pos = self.position(key) or {}
        if pos.get("done"):
            return 0.0
        p = float(pos.get("p") or 0)
        return p if p > 10 else 0.0

    # ------------------------------------------------------------ network
    async def search(self, term: str, limit: int = 30) -> list[dict[str, Any]]:
        term = term.strip()
        if not term:
            return []
        try:
            res = await self.http.get(
                ITUNES_SEARCH,
                params={"media": "podcast", "entity": "podcast", "term": term, "limit": limit},
                timeout=10,
            )
            data = res.json() if res.status_code == 200 else {}
        except (httpx.HTTPError, ValueError) as exc:
            raise PodcastError(502, "Podcast search is not answering right now.") from exc
        out = []
        for item in data.get("results") or []:
            feed = item.get("feedUrl")
            if not feed:
                continue
            out.append(
                {
                    "feed": feed,
                    "title": item.get("collectionName") or item.get("trackName") or "Podcast",
                    "author": item.get("artistName"),
                    "image": item.get("artworkUrl600") or item.get("artworkUrl100"),
                    "genre": item.get("primaryGenreName"),
                }
            )
        return out

    async def feed(self, url: str, *, fresh: bool = False) -> dict[str, Any]:
        if not url.startswith(("http://", "https://")):
            raise PodcastError(400, "Bad feed address")
        now = time.monotonic()
        cached = self._feeds.get(url)
        if cached and not fresh and now - cached[0] < FEED_TTL:
            return self._with_progress(cached[1])
        try:
            async with self.http.stream(
                "GET",
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=20,
                follow_redirects=True,
            ) as res:
                if res.status_code != 200:
                    raise PodcastError(502, f"The podcast feed answered {res.status_code}.")
                raw = bytearray()
                async for chunk in res.aiter_bytes():
                    raw += chunk
                    if len(raw) > MAX_FEED_BYTES:
                        raise PodcastError(502, "That podcast feed is too big.")
        except httpx.HTTPError as exc:
            if cached:
                return self._with_progress(cached[1])
            raise PodcastError(502, "Could not load that podcast feed.") from exc
        parsed = parse_feed(url, bytes(raw))
        self._feeds[url] = (now, parsed)
        while len(self._feeds) > FEED_CACHE:
            oldest = min(self._feeds, key=lambda k: self._feeds[k][0])
            self._feeds.pop(oldest)
        return self._with_progress(parsed)

    def _with_progress(self, feed: dict[str, Any]) -> dict[str, Any]:
        pos = self.doc().get("pos") or {}
        episodes = []
        for ep in feed["episodes"]:
            item = dict(ep)
            p = pos.get(ep["id"]) or {}
            item["position"] = p.get("p")
            item["done"] = bool(p.get("done"))
            episodes.append(item)
        subscribed = any(s.get("feed") == feed["feed"] for s in self.doc()["subs"])
        return {**feed, "episodes": episodes, "subscribed": subscribed}

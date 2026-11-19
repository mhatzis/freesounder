"""The MPD-backed "Media" source: music library, podcasts and UPnP/DLNA.

FreeSounder loads MPD's queue itself for the library and podcasts and keeps
the tracks' metadata (title, artist, cover URL) next to it. When something
else changes the queue (upmpdcli, i.e. a UPnP/DLNA or OpenHome controller such
as BubbleUPnP), the queue is no longer ours and the source is "upnp".
"""

from __future__ import annotations

import logging
from typing import Any

from freesounderd.mpd import MpdClient, MpdError

logger = logging.getLogger(__name__)

MEDIA_SOURCES = ("library", "podcast", "upnp")


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class MediaPlayer:
    def __init__(self, mpd: Any | None = None) -> None:
        self.mpd: Any = mpd or MpdClient()
        self.owner: str | None = None  # "library" / "podcast" while the queue is ours
        self.tracks: list[dict[str, Any]] = []
        self._plver: str | None = None
        self.status: dict[str, str] = {}
        self.song: dict[str, str] = {}
        self.available = False
        self.error: str | None = None

    # ------------------------------------------------------------- state
    @property
    def state(self) -> str:
        """play / pause / stop ("stop" also when MPD is not there)."""

        return self.status.get("state", "stop") if self.available else "stop"

    @property
    def playing(self) -> bool:
        return self.state == "play"

    @property
    def elapsed(self) -> float | None:
        return _num(self.status.get("elapsed"))

    @property
    def duration(self) -> float | None:
        return _num(self.status.get("duration")) or _num(self.song.get("duration"))

    @property
    def volume(self) -> int | None:
        vol = _num(self.status.get("volume"))
        return None if vol is None or vol < 0 else int(vol)

    @property
    def source(self) -> str | None:
        """Who drives MPD right now, or None when it is idle."""

        if self.state == "stop":
            return None
        return self.owner or "upnp"

    async def refresh(self) -> None:
        try:
            self.status = await self.mpd.status()
            self.song = await self.mpd.currentsong() if self.state != "stop" else {}
            self.available = True
            self.error = None
        except MpdError as exc:
            self.available = False
            self.status, self.song = {}, {}
            self.error = str(exc)
            return
        if self.owner and self.status.get("playlist") != self._plver:
            logger.info("MPD queue changed by another client: UPnP/DLNA took over")
            self.owner = None
            self.tracks = []

    def current(self) -> dict[str, Any] | None:
        """Our metadata for the playing track (None for UPnP: see upnp.py)."""

        if not self.owner:
            return None
        pos = _num(self.status.get("song"))
        if pos is None or not (0 <= int(pos) < len(self.tracks)):
            return None
        return self.tracks[int(pos)]

    # ------------------------------------------------------------- commands
    async def play(
        self, tracks: list[dict[str, Any]], *, start: int = 0, owner: str, seek: float = 0
    ) -> None:
        if not tracks:
            raise ValueError("nothing to play")
        start = max(0, min(start, len(tracks) - 1))
        cmds: list[list[Any]] = [["stop"], ["clear"]]
        cmds += [["add", t["url"]] for t in tracks]
        if seek and seek > 1:
            # seek starts playback at that point (a podcast's resume position).
            try:
                await self.mpd.command_list([*cmds, ["seek", start, f"{seek:.1f}"]])
            except MpdError:
                logger.info("Resume position not seekable; starting from the top")
                await self.mpd.command_list([*cmds, ["play", start]])
        else:
            await self.mpd.command_list([*cmds, ["play", start]])
        self.owner = owner
        self.tracks = [dict(t) for t in tracks]
        self.status = await self.mpd.status()
        self.available = True
        self._plver = self.status.get("playlist")

    async def pause(self) -> None:
        await self.mpd.command("pause", 1)

    async def resume(self) -> None:
        if self.state == "stop":
            await self.mpd.command("play")
        else:
            await self.mpd.command("pause", 0)

    async def stop(self) -> None:
        await self.mpd.command("stop")

    async def next(self) -> None:
        await self.mpd.command("next")

    async def previous(self) -> None:
        await self.mpd.command("previous")

    async def seek(self, seconds: float) -> None:
        await self.mpd.command("seekcur", f"{max(0.0, seconds):.1f}")

    async def set_volume(self, percent: int) -> None:
        await self.mpd.command("setvol", max(0, min(100, int(percent))))

    async def release(self) -> None:
        """Another source took over: stop, and forget our queue."""

        if self.state != "stop":
            try:
                await self.stop()
            except MpdError:
                logger.debug("MPD stop failed", exc_info=True)
        self.status["state"] = "stop"

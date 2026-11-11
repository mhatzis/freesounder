"""Radio Browser search and the ffmpeg radio player (leader only)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import re
import socket
import time
from typing import Any

import httpx

from freesounderd import __version__

logger = logging.getLogger(__name__)

USER_AGENT = f"FreeSounder/{__version__}"
FALLBACK_SERVERS = (
    "de1.api.radio-browser.info",
    "de2.api.radio-browser.info",
    "fi1.api.radio-browser.info",
)
_STREAM_TITLE = re.compile(r"StreamTitle\s*:\s*(.*)$")


def _discover_servers() -> list[str]:
    """Radio Browser asks clients to resolve all.api.radio-browser.info."""

    try:
        infos = socket.getaddrinfo("all.api.radio-browser.info", 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return list(FALLBACK_SERVERS)
    names: set[str] = set()
    for info in infos:
        try:
            names.add(socket.gethostbyaddr(info[4][0])[0])
        except OSError:
            continue
    servers = [n for n in names if n.endswith("radio-browser.info")]
    random.shuffle(servers)
    return servers or list(FALLBACK_SERVERS)


class RadioBrowser:
    def __init__(self, client: httpx.AsyncClient, base: str | None = None) -> None:
        self._client = client
        self._base = base
        self._servers: list[str] | None = None

    async def _bases(self) -> list[str]:
        if self._base:
            return [self._base.rstrip("/")]
        if self._servers is None:
            self._servers = await asyncio.to_thread(_discover_servers)
        return [f"https://{s}" for s in self._servers]

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        last: Exception | None = None
        for base in await self._bases():
            try:
                res = await self._client.get(
                    f"{base}{path}",
                    params=params,
                    headers={"User-Agent": USER_AGENT},
                    timeout=8,
                )
                res.raise_for_status()
                return res.json()
            except (httpx.HTTPError, ValueError) as exc:
                last = exc
                continue
        raise RuntimeError(f"Radio Browser unavailable: {last}")

    async def search(
        self,
        *,
        name: str | None = None,
        countrycode: str | None = None,
        tag: str | None = None,
        limit: int = 40,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "limit": limit,
            "hidebroken": "true",
            "order": "clickcount",
            "reverse": "true",
        }
        if name:
            params["name"] = name
        if countrycode:
            params["countrycode"] = countrycode
        if tag:
            params["tag"] = tag
        data = await self._get("/json/stations/search", params)
        return [s for s in data if isinstance(s, dict) and s.get("stationuuid")]

    async def resolve(self, stationuuid: str) -> dict[str, Any]:
        """Registers a click (as Radio Browser asks) and returns the stream URL."""

        data = await self._get(f"/json/url/{stationuuid}")
        if isinstance(data, list):
            data = data[0] if data else {}
        return data if isinstance(data, dict) else {}


class RadioPlayer:
    """Runs ffmpeg <url> -> s16le 44.1 kHz stereo into the leader's radio FIFO."""

    def __init__(self, ffmpeg: str, fifo: str, managed: bool = True) -> None:
        self.ffmpeg = ffmpeg
        self.fifo = fifo
        self.managed = managed
        self.station: dict[str, Any] | None = None
        self.stream_title: str | None = None
        self.paused = False
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._fake_running = False
        self.restarts = 0
        self._next_try = 0.0

    @property
    def running(self) -> bool:
        if not self.managed:
            return self._fake_running
        return self._proc is not None and self._proc.returncode is None

    def command(self, url: str) -> list[str]:
        return [
            self.ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "info",
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_delay_max",
            "10",
            "-user_agent",
            USER_AGENT,
            "-i",
            url,
            "-vn",
            "-f",
            "s16le",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-y",
            self.fifo,
        ]

    async def play(self, station: dict[str, Any]) -> None:
        await self.stop(keep_station=False)
        self.station = station
        self.paused = False
        await self._start()

    async def resume(self) -> None:
        if self.station and not self.running:
            self.paused = False
            await self._start()

    async def _start(self) -> None:
        assert self.station is not None
        url = self.station.get("url")
        if not url:
            return
        self.stream_title = None
        if not self.managed:
            self._fake_running = True
            return
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self.command(url),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError:
            logger.warning("Could not start ffmpeg", exc_info=True)
            self._proc = None
            return
        self._reader = asyncio.create_task(self._read_stderr(self._proc))

    async def _read_stderr(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stderr is not None
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            text = line.decode(errors="replace").strip()
            match = _STREAM_TITLE.search(text)
            if match:
                self.stream_title = match.group(1).strip() or None

    async def pause(self) -> None:
        await self.stop(keep_station=True)
        self.paused = self.station is not None

    async def stop(self, *, keep_station: bool = False) -> None:
        if not keep_station:
            self.station = None
            self.paused = False
        self._fake_running = False
        proc, self._proc = self._proc, None
        if proc is not None and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except TimeoutError:
                proc.kill()
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader
            self._reader = None

    async def watchdog(self) -> None:
        """Restart ffmpeg if it exited while it should be playing."""

        if not (self.station and not self.paused and not self.running and self.managed):
            return
        now = time.monotonic()
        if now < self._next_try:
            return
        self.restarts += 1
        self._next_try = now + min(30, 2 * self.restarts)
        await self._start()

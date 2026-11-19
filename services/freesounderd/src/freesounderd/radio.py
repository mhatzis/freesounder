"""Radio Browser search and the ffmpeg radio player (leader only)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import re
import socket
import ssl
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin, urlsplit

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
_STDERR_LINE_MAX = 8192


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


class NoIcy(Exception):
    """The stream has no ICY metadata (HLS, plain HTTP audio, ...)."""


class IcyUnavailable(Exception):
    """The server refused the side connection for now (listener limit, 5xx, ...)."""


def describe_error(exc: BaseException) -> str:
    """Never an empty message: "TimeoutError: no answer in 20 s" style."""

    text = str(exc).strip()
    if not text and isinstance(exc, TimeoutError):
        text = "no answer in time"
    return f"{type(exc).__name__}: {text or 'no detail'}"[:160]


CLOSE_WAIT = 1.0  # s for a station connection to close before it is cut
TASK_WAIT = 2.0  # s for a cancelled stream task to finish


async def _settle(task: asyncio.Task[Any]) -> None:
    """Wait a little for a cancelled task; never block a pause on it."""

    done, _ = await asyncio.wait({task}, timeout=TASK_WAIT)
    if not done:
        logger.warning("radio task did not stop within %.0f s; leaving it", TASK_WAIT)
        return
    with contextlib.suppress(asyncio.CancelledError, Exception):
        task.result()


class IcyStream:
    """An open HTTP/ICY connection positioned at the start of the body."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        status: str,
        headers: dict[str, str],
        url: str,
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.status = status
        self.headers = headers
        self.url = url
        try:
            self.metaint = int(headers.get("icy-metaint", "0") or 0)
        except ValueError:
            self.metaint = 0

    async def close(self) -> None:
        # Never wait long: an HTTPS station that does not answer the TLS
        # close kept wait_closed() (and so a radio pause, under the engine
        # lock) busy for up to 30 s.
        self.writer.close()
        try:
            await asyncio.wait_for(self.writer.wait_closed(), timeout=CLOSE_WAIT)
        except asyncio.CancelledError:
            self.writer.transport.abort()
            raise
        except Exception:  # noqa: BLE001 - TimeoutError, SSL errors, reset
            self.writer.transport.abort()


async def open_icy(url: str, user_agent: str = "FreeSounder", wait: float = 10.0) -> IcyStream:
    """GET ``url`` with ``Icy-MetaData: 1``; follows up to three redirects.

    Handles "ICY 200 OK" status lines (Shoutcast v1), which HTTP libraries
    reject. Raises NoIcy for unusable URLs/404, IcyUnavailable for other
    refusals (listener caps, 5xx).
    """

    async def once(target: str) -> IcyStream | str:
        parts = urlsplit(target)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise NoIcy(target)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        ssl_ctx = ssl.create_default_context() if parts.scheme == "https" else None
        reader, writer = await asyncio.open_connection(
            parts.hostname, port, ssl=ssl_ctx, server_hostname=parts.hostname if ssl_ctx else None
        )
        try:
            path = parts.path or "/"
            if parts.query:
                path += "?" + parts.query
            host = parts.hostname if parts.port is None else f"{parts.hostname}:{parts.port}"
            writer.write(
                (
                    f"GET {path} HTTP/1.0\r\nHost: {host}\r\nUser-Agent: {user_agent}\r\n"
                    "Icy-MetaData: 1\r\nAccept: */*\r\nConnection: close\r\n\r\n"
                ).encode()
            )
            await writer.drain()
            status = (await reader.readline()).decode("latin-1").strip()
            headers: dict[str, str] = {}
            while True:
                line = (await reader.readline()).decode("latin-1").strip()
                if not line:
                    break
                key, _, value = line.partition(":")
                headers[key.strip().lower()] = value.strip()
        except BaseException:
            writer.close()
            raise
        code = status.split(" ")[1] if " " in status else ""
        if code in ("301", "302", "303", "307", "308") and headers.get("location"):
            writer.close()
            return urljoin(target, headers["location"])
        if code != "200":
            writer.close()
            if code in ("404", "410"):
                raise NoIcy(status)
            # Shoutcast/Icecast listener caps, 401/403/429/5xx: try again later.
            raise IcyUnavailable(status or "empty answer")
        return IcyStream(reader, writer, status, headers, target)

    async def run() -> IcyStream:
        target = url
        for _ in range(4):
            got = await once(target)
            if isinstance(got, IcyStream):
                return got
            target = got
        raise NoIcy("too many redirects")

    return await asyncio.wait_for(run(), timeout=wait)


def parse_stream_title(raw: bytes) -> str | None:
    """StreamTitle from an ICY metadata block (UTF-8, else Latin-1)."""

    data = raw.rstrip(b"\0")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    match = re.search(r"StreamTitle='(.*?)';", text, re.S)
    if match is None:
        return None
    return match.group(1).strip()


class IcyDemuxer:
    """Splits an ICY body into audio bytes and metadata blocks.

    Every ``metaint`` audio bytes the server inserts one length byte (x16)
    and that many bytes of ``StreamTitle='...';`` metadata.
    """

    def __init__(self, metaint: int) -> None:
        self.metaint = metaint
        self._left = metaint  # audio bytes until the next length byte
        self._meta_len: int | None = None
        self._meta = bytearray()

    def feed(self, chunk: bytes) -> tuple[bytes, list[str | None]]:
        audio = bytearray()
        titles: list[str | None] = []
        i = 0
        n = len(chunk)
        while i < n:
            if self._meta_len is None and self._left > 0:
                take = min(self._left, n - i)
                audio += chunk[i : i + take]
                i += take
                self._left -= take
            elif self._meta_len is None:
                self._meta_len = chunk[i] * 16
                i += 1
                self._meta.clear()
                if self._meta_len == 0:
                    self._meta_len = None
                    self._left = self.metaint
                    titles.append(None)  # a block with no change: still a heartbeat
            else:
                take = min(self._meta_len - len(self._meta), n - i)
                self._meta += chunk[i : i + take]
                i += take
                if len(self._meta) == self._meta_len:
                    titles.append(parse_stream_title(bytes(self._meta)) or "")
                    self._meta_len = None
                    self._left = self.metaint
        return bytes(audio), titles


async def fetch_icy_title(
    url: str, user_agent: str = "FreeSounder", wait: float = 20.0
) -> str | None:
    """Current ICY StreamTitle via a short side connection (fallback only).

    The player reads titles from the stream it is already playing (see
    RadioPlayer._pump); this is only used when that is not possible.
    """

    async def run() -> str | None:
        stream = await open_icy(url, user_agent, wait)
        try:
            if stream.metaint <= 0:
                raise NoIcy("no icy-metaint")
            await stream.reader.readexactly(stream.metaint)
            length = (await stream.reader.readexactly(1))[0] * 16
            if length == 0:
                return None
            return parse_stream_title(await stream.reader.readexactly(length)) or None
        finally:
            await stream.close()

    return await asyncio.wait_for(run(), timeout=wait)


TITLE_POLL = 30.0  # fallback side-connection poll (stream metadata is live)


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
        # Song-title refresh (see fetch_icy_title). Overridable in tests.
        self.title_fetcher = fetch_icy_title
        self.poll_titles = managed
        self.title_poll = TITLE_POLL
        self.title_backoff_max = 120.0
        self.no_icy_recheck = 300.0
        self.on_title: Callable[[], None] | None = None
        self._titles: asyncio.Task[None] | None = None
        # For diagnostics / the app's developer line (wall-clock seconds).
        self.title_changed_at: float | None = None
        self.title_checked_at: float | None = None
        self.title_error: str | None = None
        self.title_source: str | None = None  # "stream" (live ICY) or "poll"
        self.opener = open_icy  # overridable in tests
        self._pump: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        if not self.managed:
            return self._fake_running
        return self._proc is not None and self._proc.returncode is None

    def command(self, url: str) -> list[str]:
        """ffmpeg reading the station itself (streams without ICY metadata)."""

        return [
            self.ffmpeg,
            "-nostdin",
            "-hide_banner",
            # No progress line: it is noise, and ffmpeg rewrites it with
            # "\r" about once a second for as long as the station plays.
            "-nostats",
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
            *self._output(),
        ]

    def pipe_command(self) -> list[str]:
        """ffmpeg decoding audio we feed it (we read the ICY stream ourselves)."""

        return [
            self.ffmpeg,
            "-hide_banner",
            "-nostats",
            # Titles come from our own ICY demuxer here, so only warnings.
            "-loglevel",
            "warning",
            "-i",
            "pipe:0",
            *self._output(),
        ]

    def _output(self) -> list[str]:
        return ["-vn", "-f", "s16le", "-ar", "44100", "-ac", "2", "-y", self.fifo]

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
        self.title_error = None
        if self._pump is not None:
            self._pump.cancel()
            self._pump = None
        if not self.managed:
            self._fake_running = True
            self.title_source = "poll"
            self._start_titles(url)
            return
        # Read the stream ourselves when it carries ICY metadata: the song
        # title then comes with the audio, every few seconds, on the one
        # connection that is already playing (no second connection to time out).
        stream: IcyStream | None = None
        try:
            stream = await self.opener(url, USER_AGENT, 10.0)
            if stream.metaint <= 0:
                await stream.close()
                stream = None
        except Exception as exc:  # noqa: BLE001 - fall back to ffmpeg's own HTTP
            logger.info(
                "Reading %s directly failed (%s); ffmpeg opens it", url, describe_error(exc)
            )
            stream = None
        cmd = self.pipe_command() if stream is not None else self.command(url)
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE if stream is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError:
            logger.warning("Could not start ffmpeg", exc_info=True)
            self._proc = None
            if stream is not None:
                await stream.close()
            return
        self._reader = asyncio.create_task(self._read_stderr(self._proc))
        if stream is not None:
            self.title_source = "stream"
            self._pump = asyncio.create_task(self._pump_stream(self._proc, stream, url))
        else:
            self.title_source = "poll"
            self._start_titles(url)

    async def _pump_stream(
        self, proc: asyncio.subprocess.Process, stream: IcyStream | None, url: str
    ) -> None:
        """Stream -> ffmpeg stdin, taking the ICY metadata out on the way.

        Reconnects (1 s .. 15 s) if the station drops us, as ffmpeg's own
        -reconnect would.
        """

        delay = 1.0
        try:
            while self.station is not None and self.station.get("url") == url:
                if proc.returncode is not None or proc.stdin is None:
                    return
                if stream is None:
                    try:
                        stream = await self.opener(url, USER_AGENT, 10.0)
                    except Exception as exc:  # noqa: BLE001
                        self.title_error = f"stream: {describe_error(exc)}"
                        await asyncio.sleep(delay)
                        delay = min(15.0, delay * 2)
                        continue
                    if stream.metaint <= 0:
                        # The server stopped sending metadata: ffmpeg still plays
                        # it; titles come from the fallback poll.
                        self.title_error = "stream: no ICY metadata after reconnect"
                demux = IcyDemuxer(stream.metaint) if stream.metaint > 0 else None
                try:
                    while True:
                        chunk = await asyncio.wait_for(stream.reader.read(16384), timeout=30)
                        if not chunk:
                            raise ConnectionResetError("station closed the stream")
                        if demux is not None:
                            audio, titles = demux.feed(chunk)
                            for title in titles:
                                self.title_checked_at = time.time()
                                self.title_error = None
                                if title:
                                    self._set_title(title)
                        else:
                            audio = chunk
                        if audio:
                            proc.stdin.write(audio)
                            await proc.stdin.drain()
                        delay = 1.0
                except (BrokenPipeError, ConnectionResetError) as exc:
                    if proc.returncode is not None:
                        return
                    self.title_error = f"stream: {describe_error(exc)}"
                except Exception as exc:  # noqa: BLE001 - reconnect on anything else
                    self.title_error = f"stream: {describe_error(exc)}"
                finally:
                    await stream.close()
                    stream = None
                logger.info("Radio stream %s dropped (%s); reconnecting", url, self.title_error)
                await asyncio.sleep(delay)
                delay = min(15.0, delay * 2)
        finally:
            if stream is not None:
                await stream.close()

    async def _read_stderr(self, proc: asyncio.subprocess.Process) -> None:
        """Drain ffmpeg's stderr for as long as it runs.

        Reads chunks and splits on both "\r" and "\n": a ``readline()``
        loop dies with ValueError once 64 KiB arrive without a newline
        (ffmpeg's progress line only uses "\r"), and then the full pipe
        blocks ffmpeg. Overlong lines are cut, never fatal.
        """

        assert proc.stderr is not None
        pending = b""
        while True:
            chunk = await proc.stderr.read(4096)
            if not chunk:
                break
            pending += chunk
            *lines, pending = re.split(rb"[\r\n]", pending)
            if len(pending) > _STDERR_LINE_MAX:
                lines.append(pending)
                pending = b""
            for line in lines:
                self._stderr_line(line)
        if pending:
            self._stderr_line(pending)

    def _stderr_line(self, line: bytes) -> None:
        text = line[:_STDERR_LINE_MAX].decode(errors="replace").strip()
        if not text:
            return
        match = _STREAM_TITLE.search(text)
        if match:
            self._set_title(match.group(1).strip() or None)

    def _set_title(self, title: str | None) -> None:
        if title != self.stream_title:
            self.stream_title = title
            self.title_changed_at = time.time()
            if self.on_title is not None:
                self.on_title()

    def _start_titles(self, url: str) -> None:
        if self._titles is not None:
            self._titles.cancel()
            self._titles = None
        self.title_error = None
        if self.poll_titles:
            self._titles = asyncio.create_task(self._poll_titles(url))

    def ensure_title_poll(self) -> None:
        """Restart the title poll if it ever ended while the station plays."""

        url = (self.station or {}).get("url")
        if not (self.poll_titles and url and not self.paused and self.title_source == "poll"):
            return
        if self._titles is None or self._titles.done():
            logger.warning("Song-title poll was not running for %s; restarting it", url)
            self._titles = asyncio.create_task(self._poll_titles(url))

    async def _poll_titles(self, url: str) -> None:
        """Keep stream_title current for as long as this station plays.

        Never gives up while the station plays: errors back off (up to 2 min),
        and a stream without ICY metadata is re-checked every 5 minutes (some
        servers redirect to a different mount now and then).
        """

        delay = self.title_poll
        while self.station is not None and self.station.get("url") == url:
            await asyncio.sleep(delay)
            if self.paused or self.station is None or self.station.get("url") != url:
                return
            try:
                title = await self.title_fetcher(url, USER_AGENT)
            except asyncio.CancelledError:
                raise
            except NoIcy as exc:
                if self.title_error != f"poll: no ICY metadata ({exc})":
                    logger.info("No ICY metadata on %s (%s); re-checking every 5 min", url, exc)
                self.title_error = f"poll: no ICY metadata ({exc})"
                delay = self.no_icy_recheck
                continue
            except Exception as exc:  # noqa: BLE001 - the poll must survive anything
                self.title_error = f"poll: {describe_error(exc)}"
                delay = min(self.title_backoff_max, max(self.title_poll, delay * 2))
                logger.info("Song-title poll failed for %s: %s", url, self.title_error)
                continue
            delay = self.title_poll
            self.title_error = None
            self.title_checked_at = time.time()
            if title is not None:
                self._set_title(title)

    async def pause(self) -> None:
        # Paused first, so nothing restarts ffmpeg while it is being stopped.
        self.paused = self.station is not None
        await self.stop(keep_station=True)
        self.paused = self.station is not None

    async def stop(self, *, keep_station: bool = False) -> None:
        if not keep_station:
            self.station = None
            self.paused = False
        self._fake_running = False
        if self._titles is not None:
            self._titles.cancel()
            self._titles = None
        if self._pump is not None:
            self._pump.cancel()
            await _settle(self._pump)
            self._pump = None
        proc, self._proc = self._proc, None
        if proc is not None and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except TimeoutError:
                proc.kill()
        if self._reader is not None:
            self._reader.cancel()
            await _settle(self._reader)
            self._reader = None

    async def watchdog(self) -> None:
        """Restart ffmpeg if it exited while it should be playing."""

        self.ensure_title_poll()
        if not (self.station and not self.paused and not self.running and self.managed):
            return
        now = time.monotonic()
        if now < self._next_try:
            return
        self.restarts += 1
        self._next_try = now + min(30, 2 * self.restarts)
        await self._start()

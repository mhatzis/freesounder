"""A small asyncio client for MPD's text protocol (no extra dependency).

MPD runs on every player (127.0.0.1:6600 only). It plays the music library,
podcasts and whatever a UPnP/DLNA controller sends through upmpdcli, into the
leader's snapserver "Media" pipe. Its mixer is the "null" mixer: volume is
only reported (the group volume is the one real attenuation).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class MpdError(RuntimeError):
    """MPD answered ACK, or could not be reached."""


class _Gone(Exception):
    """The connection broke (retry on a new one)."""


def _gone_types() -> tuple[type[BaseException], ...]:
    return (_Gone, OSError, TimeoutError, asyncio.IncompleteReadError, ValueError)


def quote(arg: Any) -> str:
    text = str(arg)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def pairs_to_dicts(pairs: list[tuple[str, str]], start: str) -> list[dict[str, str]]:
    """Split a flat key/value answer into records, a new one at each `start` key."""

    out: list[dict[str, str]] = []
    for key, value in pairs:
        if key == start or not out:
            out.append({})
        out[-1].setdefault(key, value)
    return out


class MpdClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 6600, timeout: float = 5.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()
        self.version: str | None = None

    async def _connect(self) -> None:
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), self.timeout
            )
            hello = await asyncio.wait_for(self._reader.readline(), self.timeout)
        except (OSError, TimeoutError) as exc:
            self._drop()
            raise MpdError(f"MPD not reachable: {exc}") from exc
        if not hello.startswith(b"OK MPD "):
            self._drop()
            raise MpdError("Not an MPD server")
        self.version = hello[7:].decode().strip()

    def _drop(self) -> None:
        if self._writer is not None:
            self._writer.close()
        self._reader = self._writer = None

    async def close(self) -> None:
        async with self._lock:
            self._drop()

    async def _read_answer(self, binary: bool) -> tuple[list[tuple[str, str]], bytes]:
        assert self._reader is not None
        pairs: list[tuple[str, str]] = []
        data = b""
        while True:
            line = await asyncio.wait_for(self._reader.readline(), self.timeout)
            if not line:
                raise _Gone("MPD closed the connection")
            text = line.decode("utf-8", "replace").rstrip("\n")
            if text == "OK" or text == "list_OK":
                if text == "OK":
                    return pairs, data
                continue
            if text.startswith("ACK "):
                raise MpdError(text[4:])
            key, _, value = text.partition(": ")
            if binary and key == "binary":
                size = int(value)
                data = await asyncio.wait_for(self._reader.readexactly(size + 1), self.timeout)
                data = data[:size]
                continue
            pairs.append((key, value))

    async def command(self, name: str, *args: Any, binary: bool = False) -> list[tuple[str, str]]:
        pairs, _ = await self._run([name, *args], binary)
        return pairs

    async def command_list(self, commands: list[list[Any]]) -> None:
        """Run several commands at once (all or nothing for the caller)."""

        if not commands:
            return
        lines = ["command_list_begin"]
        lines += [" ".join([str(c[0]), *(quote(a) for a in c[1:])]) for c in commands]
        lines.append("command_list_end")
        await self._send("\n".join(lines) + "\n", False)

    async def _run(self, cmd: list[Any], binary: bool) -> tuple[list[tuple[str, str]], bytes]:
        line = " ".join([str(cmd[0]), *(quote(a) for a in cmd[1:])]) + "\n"
        return await self._send(line, binary)

    async def _send(self, text: str, binary: bool) -> tuple[list[tuple[str, str]], bytes]:
        async with self._lock:
            for attempt in (0, 1):
                if self._writer is None:
                    await self._connect()
                assert self._writer is not None
                try:
                    self._writer.write(text.encode("utf-8"))
                    await self._writer.drain()
                    return await self._read_answer(binary)
                except _gone_types() as exc:
                    # MPD drops idle connections: reconnect once.
                    self._drop()
                    if attempt:
                        raise MpdError(f"MPD: {exc}") from exc
            raise MpdError("MPD: no answer")

    async def binary(self, name: str, uri: str) -> tuple[bytes, str | None]:
        """albumart / readpicture: the whole picture (fetched in chunks)."""

        out = bytearray()
        mime: str | None = None
        while True:
            pairs, chunk = await self._run([name, uri, len(out)], True)
            info = dict(pairs)
            mime = mime or info.get("type")
            if not chunk:
                break
            out += chunk
            total = int(info.get("size", len(out)))
            if len(out) >= total or len(out) > 8_000_000:
                break
        return bytes(out), mime

    # ------------------------------------------------------------ helpers
    async def status(self) -> dict[str, str]:
        return dict(await self.command("status"))

    async def currentsong(self) -> dict[str, str]:
        return dict(await self.command("currentsong"))

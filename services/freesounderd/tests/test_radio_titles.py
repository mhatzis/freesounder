"""Radio song titles stay current (ffmpeg only reports the first one)."""

from __future__ import annotations

import asyncio

import pytest

from freesounderd.radio import NoIcy, RadioPlayer, fetch_icy_title


async def serve(handler):
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, f"http://127.0.0.1:{port}"


def icy_handler(title: str, status: str = "ICY 200 OK", metaint: int = 64):
    async def handle(reader, writer):
        request = b""
        while not request.endswith(b"\r\n\r\n"):
            request += await reader.read(1024)
        assert b"Icy-MetaData: 1" in request
        writer.write(
            f"{status}\r\nicy-metaint: {metaint}\r\ncontent-type: audio/mpeg\r\n\r\n".encode()
        )
        meta = f"StreamTitle='{title}';StreamUrl='';".encode()
        blocks = (len(meta) + 15) // 16
        writer.write(b"\xff" * metaint + bytes([blocks]) + meta.ljust(blocks * 16, b"\0"))
        writer.write(b"\xff" * metaint)
        await writer.drain()
        writer.close()

    return handle


async def test_reads_title_from_shoutcast_icy_status_line() -> None:
    server, url = await serve(icy_handler("Artist - Song"))
    async with server:
        assert await fetch_icy_title(url + "/stream") == "Artist - Song"


async def test_follows_redirects_and_http_status() -> None:
    target, turl = await serve(icy_handler("Other - Tune", status="HTTP/1.0 200 OK"))

    async def redirect(reader, writer):
        await reader.read(1024)
        writer.write(f"HTTP/1.1 302 Found\r\nLocation: {turl}/live\r\n\r\n".encode())
        await writer.drain()
        writer.close()

    first, url = await serve(redirect)
    async with target, first:
        assert await fetch_icy_title(url) == "Other - Tune"


async def test_stream_without_icy_metadata() -> None:
    async def plain(reader, writer):
        await reader.read(1024)
        writer.write(b"HTTP/1.1 200 OK\r\ncontent-type: audio/aac\r\n\r\n" + b"\0" * 100)
        await writer.drain()
        writer.close()

    server, url = await serve(plain)
    async with server:
        with pytest.raises(NoIcy):
            await fetch_icy_title(url)


async def test_player_keeps_the_song_title_current() -> None:
    titles = iter(["A - One", "A - One", "B - Two", "C - Three"])
    calls = []

    async def fetcher(url: str, ua: str) -> str | None:
        calls.append(url)
        return next(titles)

    radio = RadioPlayer("ffmpeg", "/tmp/fifo", managed=False)
    radio.poll_titles = True
    radio.title_poll = 0.01
    radio.title_fetcher = fetcher
    changes = []
    radio.on_title = lambda: changes.append(radio.stream_title)
    await radio.play({"url": "http://radio.example/s", "name": "R"})
    for _ in range(100):
        if radio.stream_title == "C - Three":
            break
        await asyncio.sleep(0.01)
    assert radio.stream_title == "C - Three"
    assert changes == ["A - One", "B - Two", "C - Three"]
    await radio.stop()
    n = len(calls)
    await asyncio.sleep(0.05)
    assert len(calls) == n, "polling stops with the station"

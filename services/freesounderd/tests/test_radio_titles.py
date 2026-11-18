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


async def test_listener_cap_is_a_retry_not_no_icy() -> None:
    from freesounderd.radio import IcyUnavailable

    async def full(reader, writer):
        await reader.read(1024)
        writer.write(b"ICY 401 Service Unavailable\r\n\r\n")
        await writer.drain()
        writer.close()

    server, url = await serve(full)
    async with server:
        with pytest.raises(IcyUnavailable):
            await fetch_icy_title(url)


async def test_title_poll_survives_errors_and_no_icy_spells() -> None:
    """0.6.5 gave up for good after one 'no ICY' answer or 20 errors: the song froze."""

    answers = iter(
        [ConnectionResetError("reset"), RuntimeError("weird"), "A - One"]
        + [TimeoutError()] * 25
        + ["B - Two"]
    )

    async def fetcher(url: str, ua: str) -> str | None:
        item = next(answers, "B - Two")
        if isinstance(item, BaseException):
            raise item
        return item

    radio = RadioPlayer("ffmpeg", "/tmp/fifo", managed=False)
    radio.poll_titles = True
    radio.title_poll = 0.001
    radio.title_fetcher = fetcher
    radio.title_backoff_max = 0.005
    await radio.play({"url": "http://radio.example/s", "name": "R"})
    for _ in range(400):
        if radio.stream_title == "B - Two":
            break
        await asyncio.sleep(0.01)
    assert radio.stream_title == "B - Two"
    assert radio.title_checked_at is not None and radio.title_error is None
    await radio.stop()


async def test_dead_title_poll_is_restarted_by_the_watchdog() -> None:
    async def fetcher(url: str, ua: str) -> str | None:
        return "C - Three"

    radio = RadioPlayer("ffmpeg", "/tmp/fifo", managed=False)
    radio.poll_titles = True
    radio.title_poll = 0.001
    radio.title_fetcher = fetcher
    await radio.play({"url": "http://radio.example/s", "name": "R"})
    assert radio._titles is not None
    radio._titles.cancel()
    await asyncio.sleep(0.01)
    await radio.watchdog()
    for _ in range(100):
        if radio.stream_title == "C - Three":
            break
        await asyncio.sleep(0.01)
    assert radio.stream_title == "C - Three"
    await radio.stop()


def test_demuxer_splits_audio_and_titles_across_chunk_boundaries() -> None:
    from freesounderd.radio import IcyDemuxer

    def block(title: str | None) -> bytes:
        if title is None:
            return b"\x00"
        meta = f"StreamTitle='{title}';".encode()
        n = (len(meta) + 15) // 16
        return bytes([n]) + meta.ljust(n * 16, b"\0")

    body = b"a" * 8 + block("A - One") + b"b" * 8 + block(None) + b"c" * 8 + block("B - Two")
    for size in (1, 3, 7, 64):
        demux = IcyDemuxer(8)
        audio, titles = b"", []
        for i in range(0, len(body), size):
            got, t = demux.feed(body[i : i + size])
            audio += got
            titles += t
        assert audio == b"a" * 8 + b"b" * 8 + b"c" * 8
        assert titles == ["A - One", None, "B - Two"]


def test_error_text_is_never_empty() -> None:
    from freesounderd.radio import describe_error

    assert describe_error(TimeoutError()) == "TimeoutError: no answer in time"
    assert describe_error(OSError()) == "OSError: no detail"
    assert describe_error(ValueError("bad")) == "ValueError: bad"


async def test_titles_come_from_the_playing_stream(tmp_path) -> None:
    """The player reads the station once and takes the titles out of it."""

    import os

    titles = ["A - One", "B - Two"]
    connections = []

    async def station(reader, writer):
        connections.append(1)
        await reader.read(1024)
        writer.write(b"ICY 200 OK\r\nicy-metaint: 32\r\ncontent-type: audio/mpeg\r\n\r\n")
        for i in range(40):
            title = titles[0] if i < 20 else titles[1]
            meta = f"StreamTitle='{title}';".encode()
            n = (len(meta) + 15) // 16
            writer.write(b"\xaa" * 32 + bytes([n]) + meta.ljust(n * 16, b"\0"))
            await writer.drain()
            await asyncio.sleep(0.005)
        writer.close()

    fake = tmp_path / "ffmpeg"
    fake.write_text('#!/bin/sh\nfor last; do :; done\nexec cat > "$last"\n')
    os.chmod(fake, 0o755)
    out = tmp_path / "radio.fifo"
    server, url = await serve(station)
    async with server:
        radio = RadioPlayer(str(fake), str(out), managed=True)
        changes = []
        radio.on_title = lambda: changes.append(radio.stream_title)
        await radio.play({"url": url, "name": "R"})
        assert radio.title_source == "stream"
        for _ in range(300):
            if radio.stream_title == "B - Two":
                break
            await asyncio.sleep(0.01)
        assert changes[:2] == ["A - One", "B - Two"]
        assert radio.title_checked_at is not None
        await radio.stop()
    data = out.read_bytes()
    assert data and set(data) == {0xAA}, "only audio reaches ffmpeg"

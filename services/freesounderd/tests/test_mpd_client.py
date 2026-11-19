"""MpdClient speaks MPD's text protocol: answers, ACKs, binary, reconnects."""

from __future__ import annotations

import asyncio

import pytest

from freesounderd.mpd import MpdClient, MpdError, quote


async def _server(handler):
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def test_commands_ack_binary_and_reconnect() -> None:
    seen: list[str] = []
    connections = 0

    async def handle(reader, writer):
        nonlocal connections
        connections += 1
        writer.write(b"OK MPD 0.24.0\n")
        while line := await reader.readline():
            text = line.decode().strip()
            seen.append(text)
            if text == "status":
                writer.write(b"volume: 40\nstate: play\nOK\n")
            elif text.startswith("readpicture"):
                offset = int(text.rsplit(" ", 1)[1].strip('"'))
                data = b"ABCDEFGH"[offset : offset + 5]
                writer.write(
                    b"size: 8\ntype: image/png\nbinary: %d\n" % len(data) + data + b"\nOK\n"
                )
            elif text == "command_list_begin":
                continue
            elif text == "command_list_end":
                writer.write(b"OK\n")
            elif text.startswith(("clear", "add")):
                continue
            elif text == "bye":
                writer.close()
                return
            else:
                writer.write(b"ACK [5@0] {x} unknown command\n")
            await writer.drain()

    server, port = await _server(handle)
    client = MpdClient("127.0.0.1", port)
    assert (await client.status())["state"] == "play"
    with pytest.raises(MpdError, match="unknown command"):
        await client.command("nope")
    data, mime = await client.binary("readpicture", 'a "b".flac')
    assert data == b"ABCDEFGH" and mime == "image/png"
    assert seen[-1] == 'readpicture "a \\"b\\".flac" "5"'
    await client.command_list([["clear"], ["add", "http://x/y z"]])
    assert 'add "http://x/y z"' in seen
    # The server dropped us (MPD's idle timeout): the next command reconnects.
    client._writer.write(b"bye\n")  # type: ignore[union-attr]
    await asyncio.sleep(0.05)
    assert (await client.status())["volume"] == "40"
    assert connections == 2
    await client.close()
    server.close()


async def test_unreachable_is_an_mpd_error() -> None:
    client = MpdClient("127.0.0.1", 1, timeout=0.5)
    with pytest.raises(MpdError):
        await client.status()


def test_quote() -> None:
    assert quote('a\\b"c') == '"a\\\\b\\"c"'

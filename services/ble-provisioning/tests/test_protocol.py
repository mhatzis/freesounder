"""Improv framing and session behaviour, with the network port mocked."""

from __future__ import annotations

import pytest

from freesounder_ble_provisioning.protocol import (
    CAP_DEVICE_INFO,
    CAP_HOSTNAME,
    CAP_IDENTIFY,
    CAP_SCAN_WIFI,
    Command,
    ConnectResult,
    CurrentState,
    DeviceIdentity,
    ErrorState,
    ImprovSession,
    NetworkPort,
    RpcPacket,
    WifiNetwork,
    checksum,
    decode_rpc_strings,
    encode_rpc_result,
    parse_wifi_settings,
    try_parse_rpc,
)


class FakeNet(NetworkPort):
    def __init__(self) -> None:
        self.networks = [
            WifiNetwork(ssid="Home", rssi=-40, auth="WPA2"),
            WifiNetwork(ssid="Cafe", rssi=-70, auth="NO"),
        ]
        self.result = ConnectResult(ok=True, ip="192.168.1.20")
        self.last: tuple[str, str] | None = None
        self.online = False
        self.ip: str | None = None

    async def scan_wifi(self) -> list[WifiNetwork]:
        return list(self.networks)

    async def connect_wifi(self, ssid: str, password: str) -> ConnectResult:
        self.last = (ssid, password)
        return self.result

    def has_usable_network(self) -> bool:
        return self.online

    def current_ip(self) -> str | None:
        return self.ip


def _session(net: FakeNet | None = None) -> ImprovSession:
    return ImprovSession(
        network=net or FakeNet(),
        identity=DeviceIdentity(
            firmware_name="FreeSounder",
            firmware_version="0.3.0",
            hardware="raspberry-pi-5",
            device_name="Kitchen",
            player_id="player-abc",
            hostname="kitchen",
        ),
    )


def test_checksum_and_roundtrip() -> None:
    packet = RpcPacket(command=Command.IDENTIFY, data=b"")
    frame = packet.encode()
    parsed, rest, invalid = try_parse_rpc(frame)
    assert not invalid
    assert rest == b""
    assert parsed is not None
    assert parsed.command == Command.IDENTIFY
    assert parsed.data == b""
    assert checksum(frame[:-1]) == frame[-1]


def test_try_parse_reassembles_and_rejects_bad_checksum() -> None:
    packet = RpcPacket(command=Command.IDENTIFY, data=b"")
    frame = packet.encode()
    assert try_parse_rpc(frame[:2])[0] is None
    parsed, rest, invalid = try_parse_rpc(frame)
    assert parsed is not None and rest == b"" and not invalid
    bad = bytearray(frame)
    bad[-1] ^= 0xFF
    parsed, rest, invalid = try_parse_rpc(bytes(bad))
    assert parsed is None and invalid and rest == b""


def test_parse_wifi_settings() -> None:
    ssid, password = "Home", "secret-pass"
    data = bytes((len(ssid),)) + ssid.encode() + bytes((len(password),)) + password.encode()
    assert parse_wifi_settings(data) == (ssid, password)
    assert parse_wifi_settings(b"") is None
    assert parse_wifi_settings(bytes((5,))) is None


@pytest.mark.asyncio
async def test_fragmented_wifi_settings_success() -> None:
    net = FakeNet()
    session = _session(net)
    changes: list[CurrentState] = []

    async def on_change() -> None:
        changes.append(session.state)

    session.on_change = on_change
    payload = parse_ready("HomeNet", "hunter2")
    frame = RpcPacket(command=Command.WIFI_SETTINGS, data=payload).encode()
    await session.push_rpc_bytes(frame[:4])
    assert session.state is CurrentState.AUTHORIZED
    await session.push_rpc_bytes(frame[4:])
    assert net.last == ("HomeNet", "hunter2")
    assert session.state is CurrentState.PROVISIONED
    assert session.error is ErrorState.NO_ERROR
    strings = _result_strings(session.rpc_result)
    assert strings == ["http://192.168.1.20:8090"]
    assert CurrentState.PROVISIONING in changes


@pytest.mark.asyncio
async def test_wifi_errors_surface_reason() -> None:
    net = FakeNet()
    net.result = ConnectResult(ok=False, reason="wrong_password")
    session = _session(net)
    await session.push_rpc_bytes(RpcPacket(Command.WIFI_SETTINGS, parse_ready("x", "y")).encode())
    assert session.state is CurrentState.AUTHORIZED
    assert session.error is ErrorState.UNABLE_TO_CONNECT
    assert _result_strings(session.rpc_result) == ["wrong_password"]

    net.result = ConnectResult(ok=False, reason="ssid_not_found")
    await session.push_rpc_bytes(RpcPacket(Command.WIFI_SETTINGS, parse_ready("nope", "")).encode())
    assert _result_strings(session.rpc_result) == ["ssid_not_found"]


@pytest.mark.asyncio
async def test_scan_device_info_identify_hostname() -> None:
    session = _session()
    identified = []

    async def ident() -> None:
        identified.append(True)

    session.on_identify = ident
    await session.push_rpc_bytes(RpcPacket(Command.SCAN_WIFI, b"").encode())
    assert _result_strings(session.rpc_result) == ["Home", "-40", "WPA2", "Cafe", "-70", "NO"]

    await session.push_rpc_bytes(RpcPacket(Command.DEVICE_INFO, b"").encode())
    assert _result_strings(session.rpc_result) == [
        "FreeSounder",
        "0.3.0",
        "raspberry-pi-5",
        "Kitchen",
        "player-abc",
    ]

    await session.push_rpc_bytes(RpcPacket(Command.IDENTIFY, b"").encode())
    assert identified == [True]

    await session.push_rpc_bytes(RpcPacket(Command.HOSTNAME, b"lounge").encode())
    assert _result_strings(session.rpc_result) == ["lounge"]
    await session.push_rpc_bytes(RpcPacket(Command.HOSTNAME, b"-bad").encode())
    assert session.error is ErrorState.BAD_HOSTNAME

    await session.push_rpc_bytes(RpcPacket(0x99, b"").encode())
    assert session.error is ErrorState.UNKNOWN_RPC


def test_capabilities_and_service_data() -> None:
    session = _session()
    assert session.capabilities_bytes()[0] == (
        CAP_IDENTIFY | CAP_DEVICE_INFO | CAP_SCAN_WIFI | CAP_HOSTNAME
    )
    data = session.service_data()
    assert data[0] == CurrentState.AUTHORIZED
    assert data[1] == session.capabilities


def test_encode_rpc_result_roundtrip() -> None:
    frame = encode_rpc_result(Command.DEVICE_INFO, ["a", "b"])
    assert _result_strings(frame) == ["a", "b"]


def parse_ready(ssid: str, password: str) -> bytes:
    raw_s, raw_p = ssid.encode(), password.encode()
    return bytes((len(raw_s),)) + raw_s + bytes((len(raw_p),)) + raw_p


def _open(frame: bytes) -> tuple[int, bytes]:
    packet, rest, invalid = try_parse_rpc(frame)
    assert packet is not None and not invalid and rest == b""
    return packet.command, packet.data


def _result_strings(frame: bytes) -> list[str]:
    _, data = _open(frame)
    return decode_rpc_strings(data)

"""Improv Wi-Fi BLE framing and state machine (transport-agnostic)."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Final

# https://www.improv-wifi.com/ble/
SERVICE_UUID: Final = "00467768-6228-2272-4663-277478268000"
CHAR_CURRENT_STATE: Final = "00467768-6228-2272-4663-277478268001"
CHAR_ERROR_STATE: Final = "00467768-6228-2272-4663-277478268002"
CHAR_RPC_COMMAND: Final = "00467768-6228-2272-4663-277478268003"
CHAR_RPC_RESULT: Final = "00467768-6228-2272-4663-277478268004"
CHAR_CAPABILITIES: Final = "00467768-6228-2272-4663-277478268005"
ADVERTISEMENT_SERVICE_DATA_UUID: Final = "00004677-0000-1000-8000-00805f9b34fb"
ADVERTISEMENT_SERVICE_DATA_SHORT: Final = 0x4677


# Legacy BLE advertisements are 31 bytes. Improv requires the 128-bit service
# UUID and 16-bit service data in the *same* primary advertisement (not the
# scan response). Flags (3) + 128-bit UUID list (18) + service data (10) = 31,
# so a local name must not be packed into the primary PDU.


def legacy_advertisement_length(
    *,
    flags: bool = True,
    service_uuid_128: bool = True,
    service_data: bytes | None = None,
    local_name: str | None = None,
) -> int:
    """Packed size of a legacy AD payload (excluding the 31-byte limit itself)."""

    size = 0
    if flags:
        size += 3  # length, type, flags
    if service_uuid_128:
        size += 2 + 16  # length, type, 128-bit UUID
    if service_data is not None:
        # length, type (Service Data 16-bit UUID), UUID 0x4677, payload
        size += 2 + 2 + len(service_data)
    if local_name:
        raw = local_name.encode("utf-8")
        size += 2 + len(raw)
    return size


# Bluetooth SIG unassigned / test Company ID. Used until FreeSounder has a
# registered CID. Payload is the product name as UTF-8.
COMPANY_ID_TEST: Final = 0xFFFF
MANUFACTURER_PAYLOAD: Final = b"FreeSounder"
SHORT_LOCAL_NAME: Final = "FS"


def scan_response_name(local_name: str) -> str:
    """Short name for the scan response, e.g. FreeSounder-44BD -> FS-44BD.

    BlueZ puts the LocalName advertisement property in the scan response
    (legacy advertising). "FreeSounder-44BD" (18 bytes with header) does
    not fit next to the 15-byte manufacturer data, "FS-44BD" (9) does.
    """

    suffix = local_name.rsplit("-", 1)[-1] if "-" in local_name else ""
    suffix = "".join(ch for ch in suffix if ch.isalnum())[:8]
    return f"{SHORT_LOCAL_NAME}-{suffix}" if suffix else SHORT_LOCAL_NAME


def manufacturer_specific_data(
    payload: bytes = MANUFACTURER_PAYLOAD, company_id: int = COMPANY_ID_TEST
) -> bytes:
    """Company ID (little-endian) plus manufacturer payload, without the AD header."""

    return bytes((company_id & 0xFF, (company_id >> 8) & 0xFF)) + payload


def legacy_scan_response_length(
    *,
    manufacturer_payload: bytes | None = MANUFACTURER_PAYLOAD,
    company_id: int = COMPANY_ID_TEST,
    local_name: str | None = None,
) -> int:
    """Packed size of a legacy scan-response PDU."""

    size = 0
    if manufacturer_payload is not None:
        # length, type 0xFF, company ID (2), payload
        size += 2 + 2 + len(manufacturer_payload)
    if local_name:
        size += 2 + len(local_name.encode("utf-8"))
    return size



CAP_IDENTIFY: Final = 1 << 0
CAP_DEVICE_INFO: Final = 1 << 1
CAP_SCAN_WIFI: Final = 1 << 2
CAP_HOSTNAME: Final = 1 << 3
DEFAULT_CAPABILITIES: Final = CAP_IDENTIFY | CAP_DEVICE_INFO | CAP_SCAN_WIFI | CAP_HOSTNAME


class CurrentState(IntEnum):
    AUTHORIZATION_REQUIRED = 0x01
    AUTHORIZED = 0x02
    PROVISIONING = 0x03
    PROVISIONED = 0x04


class ErrorState(IntEnum):
    NO_ERROR = 0x00
    INVALID_RPC = 0x01
    UNKNOWN_RPC = 0x02
    UNABLE_TO_CONNECT = 0x03
    NOT_AUTHORIZED = 0x04
    BAD_HOSTNAME = 0x05
    UNKNOWN_ERROR = 0xFF


class Command(IntEnum):
    WIFI_SETTINGS = 0x01
    IDENTIFY = 0x02
    DEVICE_INFO = 0x03
    SCAN_WIFI = 0x04
    HOSTNAME = 0x05
    DEVICE_NAME = 0x06
    # FreeSounder extension (not in the Improv spec): ISO 3166 Wi-Fi country
    # as an Improv string. Older players answer UNKNOWN_RPC, which the app
    # ignores.
    WIFI_COUNTRY = 0x80


@dataclass(frozen=True)
class RpcPacket:
    command: int
    data: bytes

    def encode(self) -> bytes:
        body = bytes((self.command, len(self.data))) + self.data
        return body + bytes((checksum(body),))


def checksum(data: bytes) -> int:
    """LSB of the sum of every preceding byte."""

    return sum(data) & 0xFF


def try_parse_rpc(buffer: bytes) -> tuple[RpcPacket | None, bytes, bool]:
    """Pull one RPC frame out of a write buffer.

    Returns (packet or None if incomplete, remaining bytes, invalid_flag).
    """

    if len(buffer) < 3:
        return None, buffer, False
    data_length = buffer[1]
    frame_len = 3 + data_length
    if len(buffer) < frame_len:
        return None, buffer, False
    frame = buffer[:frame_len]
    rest = buffer[frame_len:]
    if checksum(frame[:-1]) != frame[-1]:
        return None, rest, True
    return RpcPacket(command=frame[0], data=frame[2:-1]), rest, False


def encode_rpc_result(command: int, strings: list[str]) -> bytes:
    payload = bytearray()
    for item in strings:
        raw = item.encode("utf-8")
        if len(raw) > 255:
            raw = raw[:255]
        payload.append(len(raw))
        payload.extend(raw)
    return RpcPacket(command, bytes(payload)).encode()


def decode_rpc_strings(data: bytes) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(data):
        length = data[i]
        i += 1
        chunk = data[i : i + length]
        if len(chunk) < length:
            break
        out.append(chunk.decode("utf-8", errors="replace"))
        i += length
    return out


def parse_wifi_settings(data: bytes) -> tuple[str, str] | None:
    if not data:
        return None
    ssid_len = data[0]
    if len(data) < 1 + ssid_len + 1:
        return None
    ssid = data[1 : 1 + ssid_len]
    pwd_len = data[1 + ssid_len]
    rest = data[2 + ssid_len :]
    if len(rest) < pwd_len:
        return None
    password = rest[:pwd_len]
    try:
        return ssid.decode("utf-8"), password.decode("utf-8")
    except UnicodeDecodeError:
        return None


@dataclass
class WifiNetwork:
    ssid: str
    rssi: int
    auth: str  # e.g. WPA2, NO, WPA/WPA2


@dataclass
class ConnectResult:
    ok: bool
    ip: str | None = None
    reason: str = "unable_to_connect"
    detail: str = ""


class NetworkPort:
    """Side effects used by the Improv session (mocked in tests)."""

    async def scan_wifi(self) -> list[WifiNetwork]:
        return []

    async def connect_wifi(self, ssid: str, password: str) -> ConnectResult:
        raise NotImplementedError

    def has_usable_network(self) -> bool:
        return False

    def current_ip(self) -> str | None:
        return None

    async def set_country(self, country: str) -> bool:
        del country
        return False


@dataclass
class DeviceIdentity:
    firmware_name: str = "FreeSounder"
    firmware_version: str = "0.3.1"
    hardware: str = "raspberry-pi"
    device_name: str = "FreeSounder"
    player_id: str = ""
    hostname: str = "freesounder"


NotifyFn = Callable[[], Awaitable[None] | None]


@dataclass
class ImprovSession:
    """One provisioning session. Call `push_rpc_bytes` as GATT writes arrive."""

    network: NetworkPort
    identity: DeviceIdentity
    capabilities: int = DEFAULT_CAPABILITIES
    state: CurrentState = CurrentState.AUTHORIZED
    error: ErrorState = ErrorState.NO_ERROR
    rpc_result: bytes = field(default_factory=bytes)
    _write_buffer: bytearray = field(default_factory=bytearray)
    on_identify: Callable[[], Awaitable[None] | None] | None = None
    on_hostname: Callable[[str], Awaitable[None] | None] | None = None
    on_device_name: Callable[[str], Awaitable[None] | None] | None = None
    on_change: NotifyFn | None = None
    # Monotonic deadline; the service keeps BLE up until then even once the
    # player is online, so the final Wi-Fi result reaches the phone.
    busy_until: float = 0.0

    def service_data(self) -> bytes:
        return bytes((int(self.state), self.capabilities, 0, 0, 0, 0))

    def current_state_bytes(self) -> bytes:
        return bytes((int(self.state),))

    def error_state_bytes(self) -> bytes:
        return bytes((int(self.error),))

    def capabilities_bytes(self) -> bytes:
        return bytes((self.capabilities,))

    async def push_rpc_bytes(self, chunk: bytes) -> None:
        self._write_buffer.extend(chunk)
        while True:
            packet, rest, invalid = try_parse_rpc(bytes(self._write_buffer))
            self._write_buffer = bytearray(rest)
            if invalid:
                await self._set_error(ErrorState.INVALID_RPC)
                continue
            if packet is None:
                return
            await self._handle(packet)

    async def _handle(self, packet: RpcPacket) -> None:
        try:
            command = Command(packet.command)
        except ValueError:
            await self._set_error(ErrorState.UNKNOWN_RPC)
            return

        if command is Command.WIFI_SETTINGS:
            await self._wifi_settings(packet.data)
        elif command is Command.IDENTIFY:
            await self._identify()
        elif command is Command.DEVICE_INFO:
            await self._device_info()
        elif command is Command.SCAN_WIFI:
            await self._scan_wifi()
        elif command is Command.HOSTNAME:
            await self._hostname(packet.data)
        elif command is Command.DEVICE_NAME:
            await self._device_name(packet.data)
        elif command is Command.WIFI_COUNTRY:
            await self._wifi_country(packet.data)

    async def _wifi_country(self, data: bytes) -> None:
        strings = decode_rpc_strings(data) if data else []
        code = (strings[0] if strings else "").strip().upper()
        ok = await self.network.set_country(code) if code else False
        if not ok:
            await self._set_error(ErrorState.INVALID_RPC)
            return
        self.error = ErrorState.NO_ERROR
        self.rpc_result = encode_rpc_result(Command.WIFI_COUNTRY, [code])
        await self._notify()

    async def _wifi_settings(self, data: bytes) -> None:
        if self.state not in {CurrentState.AUTHORIZED, CurrentState.PROVISIONING}:
            if self.state is CurrentState.AUTHORIZATION_REQUIRED:
                await self._set_error(ErrorState.NOT_AUTHORIZED)
                return
        parsed = parse_wifi_settings(data)
        if parsed is None:
            await self._set_error(ErrorState.INVALID_RPC)
            return
        ssid, password = parsed
        self.error = ErrorState.NO_ERROR
        self.state = CurrentState.PROVISIONING
        self.rpc_result = b""
        self.busy_until = time.monotonic() + 180
        await self._notify()
        try:
            result = await self.network.connect_wifi(ssid, password)
        except Exception as exc:  # noqa: BLE001
            result = ConnectResult(ok=False, reason="unable_to_connect", detail=str(exc)[:120])
        self.busy_until = time.monotonic() + 10
        if result.ok:
            self.state = CurrentState.PROVISIONED
            self.error = ErrorState.NO_ERROR
            ip = result.ip or self.network.current_ip() or ""
            redirect = f"http://{ip}:8090" if ip else ""
            self.rpc_result = encode_rpc_result(Command.WIFI_SETTINGS, [redirect])
        else:
            self.state = CurrentState.AUTHORIZED
            self.error = ErrorState.UNABLE_TO_CONNECT
            reason = result.reason if result.reason in _REASONS else "unable_to_connect"
            # [reason, detail]: the app maps the reason and shows the detail.
            self.rpc_result = encode_rpc_result(
                Command.WIFI_SETTINGS, [reason, (result.detail or "")[:120]]
            )
        await self._notify()

    async def _identify(self) -> None:
        if not (self.capabilities & CAP_IDENTIFY):
            await self._set_error(ErrorState.UNKNOWN_RPC)
            return
        if self.on_identify is not None:
            maybe = self.on_identify()
            if hasattr(maybe, "__await__"):
                await maybe  # type: ignore[misc]
        self.error = ErrorState.NO_ERROR
        self.rpc_result = b""
        await self._notify()

    async def _device_info(self) -> None:
        ident = self.identity
        strings = [
            ident.firmware_name,
            ident.firmware_version,
            ident.hardware,
            ident.device_name,
        ]
        if ident.player_id:
            strings.append(ident.player_id)
        self.error = ErrorState.NO_ERROR
        self.rpc_result = encode_rpc_result(Command.DEVICE_INFO, strings)
        await self._notify()

    async def _scan_wifi(self) -> None:
        networks = await self.network.scan_wifi()
        strings: list[str] = []
        for item in networks:
            strings.extend([item.ssid, str(item.rssi), item.auth or "NO"])
        self.error = ErrorState.NO_ERROR
        self.rpc_result = encode_rpc_result(Command.SCAN_WIFI, strings)
        await self._notify()

    async def _hostname(self, data: bytes) -> None:
        if self.state not in {CurrentState.AUTHORIZED, CurrentState.PROVISIONED}:
            await self._set_error(ErrorState.NOT_AUTHORIZED)
            return
        if data:
            name = data.decode("utf-8", errors="replace").strip()
            if not _valid_hostname(name):
                await self._set_error(ErrorState.BAD_HOSTNAME)
                return
            self.identity.hostname = name
            if self.on_hostname is not None:
                maybe = self.on_hostname(name)
                if hasattr(maybe, "__await__"):
                    await maybe  # type: ignore[misc]
        self.error = ErrorState.NO_ERROR
        self.rpc_result = encode_rpc_result(Command.HOSTNAME, [self.identity.hostname])
        await self._notify()

    async def _device_name(self, data: bytes) -> None:
        if self.state not in {CurrentState.AUTHORIZED, CurrentState.PROVISIONED}:
            await self._set_error(ErrorState.NOT_AUTHORIZED)
            return
        if data:
            name = data.decode("utf-8", errors="replace").strip()
            if not name or len(name.encode("utf-8")) > 255:
                await self._set_error(ErrorState.BAD_HOSTNAME)
                return
            self.identity.device_name = name
            if self.on_device_name is not None:
                maybe = self.on_device_name(name)
                if hasattr(maybe, "__await__"):
                    await maybe  # type: ignore[misc]
        self.error = ErrorState.NO_ERROR
        self.rpc_result = encode_rpc_result(Command.DEVICE_NAME, [self.identity.device_name])
        await self._notify()

    async def _set_error(self, error: ErrorState) -> None:
        self.error = error
        await self._notify()

    async def _notify(self) -> None:
        if self.on_change is not None:
            maybe = self.on_change()
            if hasattr(maybe, "__await__"):
                await maybe  # type: ignore[misc]


_REASONS = {
    "wrong_password",
    "invalid_password",
    "ssid_not_found",
    "unable_to_connect",
    "radio_off",
    "wifi_unmanaged",
    "no_address",
    "timeout",
}


def _valid_hostname(name: str) -> bool:
    if not name or len(name) > 255:
        return False
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-")
    if any(ch not in allowed for ch in name):
        return False
    if name.startswith("-") or name.endswith("-"):
        return False
    return True

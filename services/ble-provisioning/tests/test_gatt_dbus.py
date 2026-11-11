"""Drive the Improv GATT objects over a real (private) D-Bus daemon.

This exercises the same paths BlueZ uses for an iPhone: GetManagedObjects
(UUIDs and Flags), StartNotify, WriteValue (chunked, write-without-response
style) and notifications. BlueZ turns a standard
org.freedesktop.DBus.Properties.PropertiesChanged signal for "Value" into a
GATT notification. v0.4.2 emitted a custom signal on GattCharacteristic1
instead, so the phone never got the RPC result and the app spun forever.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from freesounder_ble_provisioning.protocol import (
    CHAR_CAPABILITIES,
    CHAR_CURRENT_STATE,
    CHAR_ERROR_STATE,
    CHAR_RPC_COMMAND,
    CHAR_RPC_RESULT,
    SERVICE_UUID,
    Command,
    ConnectResult,
    CurrentState,
    DeviceIdentity,
    ImprovSession,
    NetworkPort,
    RpcPacket,
    WifiNetwork,
    decode_rpc_strings,
    try_parse_rpc,
)

pytestmark = pytest.mark.skipif(
    shutil.which("dbus-daemon") is None, reason="dbus-daemon not installed"
)


class _Net(NetworkPort):
    async def scan_wifi(self) -> list[WifiNetwork]:
        return [WifiNetwork(ssid="Home", rssi=-40, auth="WPA2")]

    async def connect_wifi(self, ssid: str, password: str) -> ConnectResult:
        del ssid, password
        await asyncio.sleep(0.05)
        return ConnectResult(ok=True, ip="192.168.1.20")

    def has_usable_network(self) -> bool:
        return False


@pytest.fixture
def bus_address():
    proc = subprocess.Popen(
        ["dbus-daemon", "--session", "--nofork", "--print-address=1"],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None
    address = proc.stdout.readline().strip()
    try:
        yield address
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def _wifi_frame(ssid: str, password: str) -> bytes:
    data = bytes((len(ssid),)) + ssid.encode() + bytes((len(password),)) + password.encode()
    return RpcPacket(command=Command.WIFI_SETTINGS, data=data).encode()


async def _setup(address: str):
    from dbus_next.aio import MessageBus

    from freesounder_ble_provisioning.bluez import APP_PATH, ImprovApplication, _char_path

    server = await MessageBus(bus_address=address).connect()
    client = await MessageBus(bus_address=address).connect()
    session = ImprovSession(
        network=_Net(),
        identity=DeviceIdentity(
            firmware_name="FreeSounder",
            firmware_version="0.4.3",
            hardware="test",
            device_name="Test",
            player_id="player-test",
            hostname="fs-new",
        ),
    )
    app = ImprovApplication(session, server)

    async def on_change() -> None:
        await app.notify(session)

    session.on_change = on_change
    server.export(APP_PATH, app)
    for path, obj in app.objects():
        server.export(path, obj)

    async def iface(path: str, name: str):
        intro = await client.introspect(server.unique_name, path)
        return client.get_proxy_object(server.unique_name, path, intro).get_interface(name)

    return server, client, app, session, iface, _char_path


async def _collect(props_iface, sink: asyncio.Queue) -> None:
    def changed(interface, changed_props, invalidated) -> None:  # noqa: ANN001
        del invalidated
        if interface == "org.bluez.GattCharacteristic1" and "Value" in changed_props:
            sink.put_nowait(bytes(changed_props["Value"].value))

    props_iface.on_properties_changed(changed)


@pytest.mark.asyncio
async def test_managed_objects_match_improv_uuids_and_flags(bus_address: str) -> None:
    from freesounder_ble_provisioning.bluez import APP_PATH

    server, client, _app, _session, iface, char_path = await _setup(bus_address)
    try:
        om = await iface(APP_PATH, "org.freedesktop.DBus.ObjectManager")
        objects = await om.call_get_managed_objects()
        svc_iface = "org.bluez.GattService1"
        services = [v[svc_iface] for v in objects.values() if svc_iface in v]
        assert [s["UUID"].value for s in services] == [SERVICE_UUID]
        chars = {
            v["org.bluez.GattCharacteristic1"]["UUID"].value: v["org.bluez.GattCharacteristic1"]
            for v in objects.values()
            if "org.bluez.GattCharacteristic1" in v
        }
        assert set(chars) == {
            CHAR_CURRENT_STATE,
            CHAR_ERROR_STATE,
            CHAR_RPC_COMMAND,
            CHAR_RPC_RESULT,
            CHAR_CAPABILITIES,
        }
        assert "notify" in chars[CHAR_CURRENT_STATE]["Flags"].value
        assert "notify" in chars[CHAR_ERROR_STATE]["Flags"].value
        assert "notify" in chars[CHAR_RPC_RESULT]["Flags"].value
        assert "write" in chars[CHAR_RPC_COMMAND]["Flags"].value
        assert "write-without-response" in chars[CHAR_RPC_COMMAND]["Flags"].value
        cap = await iface(char_path(CHAR_CAPABILITIES), "org.bluez.GattCharacteristic1")
        assert bytes(await cap.call_read_value({}))[0] == 0x0F
        state = await iface(char_path(CHAR_CURRENT_STATE), "org.bluez.GattCharacteristic1")
        assert bytes(await state.call_read_value({})) == bytes((CurrentState.AUTHORIZED,))
    finally:
        client.disconnect()
        server.disconnect()


@pytest.mark.asyncio
async def test_device_info_rpc_notifies_result_over_dbus(bus_address: str) -> None:
    server, client, _app, _session, iface, char_path = await _setup(bus_address)
    try:
        result_props = await iface(char_path(CHAR_RPC_RESULT), "org.freedesktop.DBus.Properties")
        sink: asyncio.Queue[bytes] = asyncio.Queue()
        await _collect(result_props, sink)
        result_char = await iface(char_path(CHAR_RPC_RESULT), "org.bluez.GattCharacteristic1")
        await result_char.call_start_notify()
        command = await iface(char_path(CHAR_RPC_COMMAND), "org.bluez.GattCharacteristic1")
        frame = RpcPacket(command=Command.DEVICE_INFO, data=b"").encode()
        await command.call_write_value(frame[:2], {"type": _variant_s("command")})
        await command.call_write_value(frame[2:], {"type": _variant_s("command")})
        value = await asyncio.wait_for(_next_non_empty(sink), timeout=3)
        packet, _rest, invalid = try_parse_rpc(value)
        assert packet is not None and not invalid
        assert decode_rpc_strings(packet.data)[:2] == ["FreeSounder", "0.4.3"]
    finally:
        client.disconnect()
        server.disconnect()


@pytest.mark.asyncio
async def test_wifi_settings_rpc_chunked_reports_provisioned(bus_address: str) -> None:
    server, client, _app, _session, iface, char_path = await _setup(bus_address)
    try:
        state_props = await iface(char_path(CHAR_CURRENT_STATE), "org.freedesktop.DBus.Properties")
        result_props = await iface(char_path(CHAR_RPC_RESULT), "org.freedesktop.DBus.Properties")
        states: asyncio.Queue[bytes] = asyncio.Queue()
        results: asyncio.Queue[bytes] = asyncio.Queue()
        await _collect(state_props, states)
        await _collect(result_props, results)
        command = await iface(char_path(CHAR_RPC_COMMAND), "org.bluez.GattCharacteristic1")
        frame = _wifi_frame("HomeNetwork-5G", "a-long-wifi-password-123")
        assert len(frame) > 20
        for i in range(0, len(frame), 20):
            await command.call_write_value(frame[i : i + 20], {"type": _variant_s("command")})
        result = await asyncio.wait_for(_next_non_empty(results), timeout=3)
        packet, _rest, invalid = try_parse_rpc(result)
        assert packet is not None and not invalid
        assert decode_rpc_strings(packet.data) == ["http://192.168.1.20:8080"]
        seen = []
        while not states.empty():
            seen.append(states.get_nowait()[0])
        assert CurrentState.PROVISIONED in seen
    finally:
        client.disconnect()
        server.disconnect()


def test_python_and_dart_improv_uuids_match() -> None:
    dart = Path(__file__).resolve().parents[3] / "apps" / "flutter" / "lib" / "api" / "improv.dart"
    if not dart.exists():
        pytest.skip("Flutter app not in this checkout")
    text = dart.read_text(encoding="utf-8")
    found = dict(re.findall(r"const (improv\w+Uuid) = '([0-9a-f-]+)';", text))
    assert found["improvServiceUuid"] == SERVICE_UUID
    assert found["improvCurrentStateUuid"] == CHAR_CURRENT_STATE
    assert found["improvErrorStateUuid"] == CHAR_ERROR_STATE
    assert found["improvRpcCommandUuid"] == CHAR_RPC_COMMAND
    assert found["improvRpcResultUuid"] == CHAR_RPC_RESULT
    assert found["improvCapabilitiesUuid"] == CHAR_CAPABILITIES


def _variant_s(value: str):
    from dbus_next import Variant

    return Variant("s", value)


async def _next_non_empty(queue: asyncio.Queue[bytes]) -> bytes:
    while True:
        value = await queue.get()
        if value:
            return value


@pytest.mark.asyncio
async def test_wifi_failure_notifies_reason_before_error(bus_address: str) -> None:
    server, client, _app, session, iface, char_path = await _setup(bus_address)

    class _FailNet(_Net):
        async def connect_wifi(self, ssid: str, password: str) -> ConnectResult:
            del ssid, password
            return ConnectResult(ok=False, reason="radio_off", detail="wlan0 is unavailable")

    session.network = _FailNet()
    try:
        order: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue()

        async def watch(uuid: str, tag: str) -> None:
            props = await iface(char_path(uuid), "org.freedesktop.DBus.Properties")

            def changed(interface, changed_props, invalidated) -> None:  # noqa: ANN001
                del interface, invalidated
                if "Value" in changed_props:
                    order.put_nowait((tag, bytes(changed_props["Value"].value)))

            props.on_properties_changed(changed)

        await watch(CHAR_RPC_RESULT, "result")
        await watch(CHAR_ERROR_STATE, "error")
        command = await iface(char_path(CHAR_RPC_COMMAND), "org.bluez.GattCharacteristic1")
        await command.call_write_value(_wifi_frame("Home", "password123"), {})
        seen: list[tuple[str, bytes]] = []
        while True:
            tag, value = await asyncio.wait_for(order.get(), timeout=3)
            seen.append((tag, value))
            if tag == "error" and value == b"\x03":
                break
        results = [v for t, v in seen if t == "result" and v]
        assert results, seen
        packet, _rest, _invalid = try_parse_rpc(results[-1])
        assert packet is not None
        assert decode_rpc_strings(packet.data) == ["radio_off", "wlan0 is unavailable"]
        first_error = next(i for i, (t, v) in enumerate(seen) if t == "error" and v == b"\x03")
        last_result = max(i for i, (t, v) in enumerate(seen) if t == "result" and v)
        assert last_result < first_error
    finally:
        client.disconnect()
        server.disconnect()

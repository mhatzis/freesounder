"""Uplink classification and legacy advertisement size."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from freesounder_ble_provisioning.network import (
    has_lan_gateway,
    inspect_default_routes,
    inspect_ipv4,
    is_virtual_iface,
    parse_ip_ipv4,
)
from freesounder_ble_provisioning.protocol import (
    COMPANY_ID_TEST,
    MANUFACTURER_PAYLOAD,
    SHORT_LOCAL_NAME,
    legacy_advertisement_length,
    legacy_scan_response_length,
    manufacturer_specific_data,
)
from freesounder_ble_provisioning.service import PeripheralHandle, run_forever
from freesounder_ble_provisioning.status import write_status

IP_TEXT = """\
1: lo    inet 127.0.0.1/8 scope host lo
2: eth0    inet 169.254.12.34/16 scope link eth0
3: docker0    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0
4: eth0    inet 192.168.1.20/24 brd 192.168.1.255 scope global eth0
5: wlan0    inet 10.0.0.8/24 scope global wlan0
"""


def _sys(tmp: Path, iface: str, *, oper: str, carrier: str) -> None:
    d = tmp / iface
    d.mkdir(parents=True, exist_ok=True)
    (d / "operstate").write_text(oper + "\n", encoding="utf-8")
    (d / "carrier").write_text(carrier + "\n", encoding="utf-8")


def test_parse_and_virtual() -> None:
    assert parse_ip_ipv4(IP_TEXT)[0] == ("lo", "127.0.0.1")
    assert is_virtual_iface("docker0")
    assert is_virtual_iface("br-abc")
    assert is_virtual_iface("veth1234")
    assert not is_virtual_iface("eth0")
    assert not is_virtual_iface("wlan0")


def test_docker_and_no_carrier_are_not_usable(tmp_path: Path) -> None:
    _sys(tmp_path, "lo", oper="unknown", carrier="1")
    _sys(tmp_path, "docker0", oper="up", carrier="1")
    _sys(tmp_path, "eth0", oper="down", carrier="0")
    _sys(tmp_path, "wlan0", oper="down", carrier="0")
    rows = inspect_ipv4(IP_TEXT, sys_class_net=tmp_path)
    by_iface = {row.iface: row for row in rows}
    # docker0 is up with an address but must not count as "online"
    assert by_iface["docker0"].address == "172.17.0.1"
    assert by_iface["docker0"].usable is False
    assert by_iface["docker0"].skip_reason == "virtual"
    assert by_iface["lo"].usable is False
    # link-local on eth0, and no carrier
    eth_link = [row for row in rows if row.iface == "eth0" and row.link_local]
    assert eth_link and eth_link[0].usable is False
    assert not any(row.usable for row in rows)


def test_eth_with_carrier_is_usable(tmp_path: Path) -> None:
    _sys(tmp_path, "eth0", oper="up", carrier="1")
    _sys(tmp_path, "docker0", oper="up", carrier="1")
    _sys(tmp_path, "lo", oper="unknown", carrier="1")
    _sys(tmp_path, "wlan0", oper="down", carrier="0")
    rows = inspect_ipv4(IP_TEXT, sys_class_net=tmp_path)
    usable = [row for row in rows if row.usable]
    assert [(row.iface, row.address) for row in usable] == [("eth0", "192.168.1.20")]


def test_legacy_advertisement_fits_only_without_name() -> None:
    data = bytes((0x02, 0x0F, 0, 0, 0, 0))
    assert legacy_advertisement_length(service_data=data) == 31
    with_name = legacy_advertisement_length(service_data=data, local_name="FreeSounder-ABCD")
    assert with_name > 31


@pytest.mark.asyncio
async def test_run_forever_retries_failed_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from freesounder_ble_provisioning.identity import load_identity
    from freesounder_ble_provisioning.protocol import ConnectResult, NetworkPort, WifiNetwork

    monkeypatch.setattr(
        "freesounder_ble_provisioning.service.load_identity",
        lambda: load_identity(tmp_path),
    )
    monkeypatch.setattr(
        "freesounder_ble_provisioning.service.advertise_name",
        lambda: "FreeSounder-TEST",
    )

    class Offline(NetworkPort):
        async def scan_wifi(self) -> list[WifiNetwork]:
            return []

        async def connect_wifi(self, ssid: str, password: str) -> ConnectResult:
            del ssid, password
            return ConnectResult(ok=False)

        def has_usable_network(self) -> bool:
            return False

    log: list[str] = []
    attempts = {"n": 0}

    class Flaky(PeripheralHandle):
        async def start(self, session, local_name: str) -> None:  # noqa: ANN001
            del session
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("No BlueZ adapter found")
            log.append(f"start:{local_name}")

        async def stop(self) -> None:
            log.append("stop")

        async def notify(self, session) -> None:  # noqa: ANN001
            del session

    stop = asyncio.Event()

    async def factory() -> Flaky:
        return Flaky()

    task = asyncio.create_task(
        run_forever(
            network=Offline(),
            peripheral_factory=factory,
            poll_seconds=0.05,
            stop_event=stop,
        )
    )
    await asyncio.sleep(0.2)
    assert attempts["n"] >= 2
    assert any(item.startswith("start:") for item in log)
    stop.set()
    await task


def test_manufacturer_data_and_scan_response_fit() -> None:
    data = bytes((0x02, 0x0F, 0, 0, 0, 0))
    primary = legacy_advertisement_length(service_data=data)
    assert primary == 31
    # Manufacturer data and a name must not go in the primary PDU.
    with_mfg = primary + (2 + 2 + len(MANUFACTURER_PAYLOAD))
    assert with_mfg > 31
    mfg = manufacturer_specific_data()
    assert mfg[:2] == bytes((0xFF, 0xFF))
    assert mfg[2:] == b"FreeSounder"
    assert COMPANY_ID_TEST == 0xFFFF
    scan = legacy_scan_response_length(
        manufacturer_payload=MANUFACTURER_PAYLOAD,
        local_name=SHORT_LOCAL_NAME,
    )
    # 15-byte mfg AD (len+type+0xFFFF+FreeSounder) + 4-byte short name (len+type+FS)
    assert scan == 19
    assert scan <= 31
    # The full advertise name does not fit next to manufacturer data in 31
    # bytes, which is why the scan response uses the short name "FS-XXXX".
    full_name = legacy_scan_response_length(
        manufacturer_payload=MANUFACTURER_PAYLOAD,
        local_name="FreeSounder-XXXX",
    )
    assert full_name == 33
    assert full_name > 31


def test_docker_default_route_is_not_a_lan_gateway() -> None:
    text = "default via 172.17.0.1 dev docker0 proto kernel\n"
    rows = inspect_default_routes(text)
    assert len(rows) == 1
    assert rows[0]["dev"] == "docker0"
    assert rows[0]["usable"] is False
    assert has_lan_gateway(text) is False
    # Address + docker route still leave the player "offline" for Improv.
    wlan = (
        "default via 192.168.1.1 dev wlan0 proto dhcp metric 600\n"
        "default via 172.17.0.1 dev docker0 proto kernel metric 1000\n"
    )
    assert has_lan_gateway(wlan) is True
    # "connected (local only)": an address but no default route.
    assert has_lan_gateway("") is False
    link_local_only = "default dev eth0 scope link\n"
    # A default route on a real iface counts, even without a via (rare).
    assert has_lan_gateway(link_local_only) is True


def test_write_status_persists_register_error(tmp_path: Path) -> None:
    path = tmp_path / "ble-status.json"
    write_status(
        {
            "advertising": False,
            "register_result": "failed: org.bluez.Error.Failed",
            "last_error": "org.bluez.Error.Failed",
            "last_error_at": "2026-11-11T02:00:00Z",
            "rfkill": "unblocked",
            "adapter_address": "AA:BB:CC:DD:EE:FF",
            "powered": True,
        },
        path=path,
    )
    text = path.read_text(encoding="utf-8")
    assert "org.bluez.Error.Failed" in text
    assert "AA:BB:CC:DD:EE:FF" in text
    write_status({"advertising": True, "register_result": "ok", "last_error": ""}, path=path)
    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["advertising"] is True
    assert data["register_result"] == "ok"
    assert data["adapter_address"] == "AA:BB:CC:DD:EE:FF"
    assert "updated" in data

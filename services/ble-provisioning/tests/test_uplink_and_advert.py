"""Uplink classification and legacy advertisement size."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from freesounder_ble_provisioning.network import inspect_ipv4, is_virtual_iface, parse_ip_ipv4
from freesounder_ble_provisioning.protocol import legacy_advertisement_length
from freesounder_ble_provisioning.service import PeripheralHandle, run_forever

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

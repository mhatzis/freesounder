"""Identity helpers and the advertise-only-when-offline loop."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from freesounder_ble_provisioning.identity import (
    advertise_name,
    load_identity,
    mac_suffix,
    persist_name,
)
from freesounder_ble_provisioning.protocol import ConnectResult, NetworkPort, WifiNetwork
from freesounder_ble_provisioning.service import PeripheralHandle, run_forever


def test_mac_suffix_and_name() -> None:
    assert mac_suffix("aa:bb:cc:dd:ee:ff") == "EEFF"
    assert advertise_name("aa:bb:cc:dd:ee:ff") == "FreeSounder-EEFF"
    assert mac_suffix("zz") == "0000"


def test_load_identity_persists_player_id(tmp_path: Path) -> None:
    first = load_identity(tmp_path)
    second = load_identity(tmp_path)
    assert first.player_id == second.player_id
    assert first.player_id
    persist_name("Lounge", tmp_path)
    assert load_identity(tmp_path).device_name == "Lounge"


class FakeNet(NetworkPort):
    def __init__(self) -> None:
        self.online = False

    async def scan_wifi(self) -> list[WifiNetwork]:
        return []

    async def connect_wifi(self, ssid: str, password: str) -> ConnectResult:
        del ssid, password
        return ConnectResult(ok=False)

    def has_usable_network(self) -> bool:
        return self.online


class FakePeripheral(PeripheralHandle):
    def __init__(self, log: list[str]) -> None:
        self.log = log

    async def start(self, session, local_name: str) -> None:  # noqa: ANN001
        del session
        self.log.append(f"start:{local_name}")

    async def stop(self) -> None:
        self.log.append("stop")

    async def notify(self, session) -> None:  # noqa: ANN001
        del session
        self.log.append("notify")


@pytest.mark.asyncio
async def test_run_forever_advertises_only_while_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "freesounder_ble_provisioning.service.load_identity",
        lambda: load_identity(tmp_path),
    )
    monkeypatch.setattr(
        "freesounder_ble_provisioning.service.advertise_name",
        lambda: "FreeSounder-TEST",
    )
    net = FakeNet()
    events: list[str] = []
    stop = asyncio.Event()

    async def factory() -> FakePeripheral:
        return FakePeripheral(events)

    task = asyncio.create_task(
        run_forever(
            network=net,
            peripheral_factory=factory,
            poll_seconds=0.05,
            stop_event=stop,
        )
    )
    await asyncio.sleep(0.12)
    assert any(item.startswith("start:") for item in events)
    net.online = True
    await asyncio.sleep(0.12)
    assert "stop" in events
    started = events.count("start:FreeSounder-TEST")
    net.online = False
    await asyncio.sleep(0.12)
    assert events.count("start:FreeSounder-TEST") > started
    stop.set()
    await task
    assert events[-1] == "stop"

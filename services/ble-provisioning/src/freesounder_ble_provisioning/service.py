"""Advertise Improv only while the player has no usable network."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from freesounder_ble_provisioning.identify import blink_activity_led
from freesounder_ble_provisioning.identity import (
    advertise_name,
    load_identity,
    persist_hostname,
    persist_name,
)
from freesounder_ble_provisioning.network import NetworkManagerPort
from freesounder_ble_provisioning.protocol import ImprovSession, NetworkPort

logger = logging.getLogger(__name__)


@dataclass
class PeripheralHandle:
    """Started BLE peripheral; tests supply a fake."""

    async def start(self, session: ImprovSession, local_name: str) -> None: ...

    async def stop(self) -> None: ...

    async def notify(self, session: ImprovSession) -> None: ...


PeripheralFactory = Callable[[], Awaitable[PeripheralHandle] | PeripheralHandle]


async def run_forever(
    *,
    network: NetworkPort | None = None,
    peripheral_factory: PeripheralFactory | None = None,
    poll_seconds: float = 2.0,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Main loop: advertise when offline, go quiet once we have an IP."""

    network = network or NetworkManagerPort()
    local_name = advertise_name()
    done = stop_event or asyncio.Event()
    handle: PeripheralHandle | None = None
    advertising = False

    def make_session() -> ImprovSession:
        session = ImprovSession(network=network, identity=load_identity())

        async def on_change() -> None:
            if handle is not None:
                await handle.notify(session)

        session.on_change = on_change
        session.on_identify = blink_activity_led
        session.on_hostname = _on_hostname
        session.on_device_name = _on_device_name
        return session

    session = make_session()

    async def ensure_started() -> None:
        nonlocal handle, advertising, session
        if advertising:
            return
        if peripheral_factory is None:
            from freesounder_ble_provisioning.bluez import BlueZPeripheral

            handle = BlueZPeripheral()
        else:
            created = peripheral_factory()
            handle = await created if hasattr(created, "__await__") else created  # type: ignore[assignment]
        session = make_session()
        assert handle is not None
        await handle.start(session, local_name)
        advertising = True
        logger.info("Advertising %s (Improv)", local_name)

    async def ensure_stopped() -> None:
        nonlocal advertising
        if handle is not None and advertising:
            await handle.stop()
            logger.info("Stopped Improv advertising")
        advertising = False

    try:
        while not done.is_set():
            online = network.has_usable_network()
            if online:
                await ensure_stopped()
            else:
                await ensure_started()
            try:
                await asyncio.wait_for(done.wait(), timeout=poll_seconds)
            except TimeoutError:
                continue
    finally:
        await ensure_stopped()


async def _on_hostname(name: str) -> None:
    persist_hostname(name)


async def _on_device_name(name: str) -> None:
    persist_name(name)

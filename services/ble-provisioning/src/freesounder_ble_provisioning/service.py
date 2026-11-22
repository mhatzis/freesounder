"""Advertise Improv only while the player has no usable network."""

from __future__ import annotations

import asyncio
import logging
import time
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

# The bootfs doctor report runs Bluetooth and network tools and rewrites a
# file on the boot partition. Doing that on every 2 s poll kept the shared
# Bluetooth/Wi-Fi radio busy and broke up A2DP audio to Bluetooth speakers
# every few seconds (0.6.17). Now it is rewritten when the state changes and,
# while offline (when the boot partition is the only way to read it), at most
# once per OFFLINE_DOCTOR_SECONDS. While online it is never rewritten on a timer.
OFFLINE_DOCTOR_SECONDS = 60.0


class DoctorThrottle:
    """Decide when the bootfs doctor report should be rewritten."""

    def __init__(self, offline_interval: float = OFFLINE_DOCTOR_SECONDS) -> None:
        self.offline_interval = offline_interval
        self._last_state: tuple[object, ...] | None = None
        self._last_written: float | None = None

    def due(self, state: tuple[object, ...], *, online: bool, now: float) -> bool:
        if state != self._last_state or self._last_written is None:
            return True
        if online:
            return False
        return now - self._last_written >= self.offline_interval

    def mark(self, state: tuple[object, ...], *, now: float) -> None:
        self._last_state = state
        self._last_written = now


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

    _logged_online: bool | None = None
    doctor_throttle = DoctorThrottle()

    def _describe_network() -> str:
        try:
            from freesounder_ble_provisioning.network import (
                has_lan_gateway,
                inspect_default_routes,
                inspect_ipv4,
            )

            rows = inspect_ipv4()
        except Exception:  # noqa: BLE001
            return "network inspection failed"
        parts = []
        if not rows:
            parts.append("no IPv4 addresses")
        for row in rows:
            reason = row.skip_reason
            if reason:
                parts.append(f"{row.iface}/{row.address} ({reason})")
            else:
                parts.append(f"{row.iface}/{row.address} (usable)")
        try:
            routes = inspect_default_routes()
            if not routes:
                parts.append("no default IPv4 route")
            for route in routes:
                via = route["via"] or "no-via"
                dev = route["dev"] or "?"
                tag = "gateway" if route["usable"] else "ignored"
                parts.append(f"default via {via} dev {dev} ({tag})")
            if not has_lan_gateway():
                parts.append("no route to a LAN gateway")
        except Exception:  # noqa: BLE001
            parts.append("route inspection failed")
        return ", ".join(parts)

    def _publish_loop_status(*, online: bool, reason: str) -> None:
        try:
            from freesounder_ble_provisioning.doctor import collect_report
            from freesounder_ble_provisioning.status import try_write_bootfs_doctor, write_status

            write_status(
                {
                    "wanted": (not online),
                    "online": online,
                    "online_reason": reason,
                    "advertising": advertising,
                    "local_name": local_name,
                }
            )
            # Keep a copy on the boot partition so it can be read without a
            # keyboard or a network, but only when something changed (see
            # DoctorThrottle): it must not poll the radio every 2 s.
            state = (online, reason, advertising)
            now = time.monotonic()
            if doctor_throttle.due(state, online=online, now=now):
                doctor_throttle.mark(state, now=now)
                try_write_bootfs_doctor(collect_report())
        except Exception:  # noqa: BLE001
            logger.debug("could not publish ble status", exc_info=True)

    try:
        logger.info("Provisioning service starting as %s", local_name)
        while not done.is_set():
            online = network.has_usable_network()
            reason = _describe_network()
            if online != _logged_online:
                logger.info(
                    "Network state: %s (%s)",
                    "online — will not advertise" if online else "offline — will advertise",
                    reason,
                )
                _logged_online = online
            if online and time.monotonic() < session.busy_until:
                # Wi-Fi just came up mid-provisioning: keep BLE until the
                # result has been notified to the phone.
                pass
            elif online:
                await ensure_stopped()
            else:
                try:
                    await ensure_started()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Could not start advertising (will retry): %s", exc)
                    advertising = False
                    if handle is not None:
                        try:
                            await handle.stop()
                        except Exception:  # noqa: BLE001
                            logger.debug("stop after failed start", exc_info=True)
            _publish_loop_status(online=online, reason=reason)
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

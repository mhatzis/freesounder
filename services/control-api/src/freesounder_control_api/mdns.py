"""mDNS advertisement of the control API as _freesounder._tcp."""

from __future__ import annotations

import logging
import socket

from zeroconf import ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

from freesounder_control_api import __version__
from freesounder_control_api.config import Settings

logger = logging.getLogger(__name__)

SERVICE_TYPE = "_freesounder._tcp.local."


def _local_ip() -> str:
    """Best-effort outbound IPv4 address, falling back to loopback."""

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("1.1.1.1", 80))
        ip = sock.getsockname()[0]
    except OSError:
        ip = "127.0.0.1"
    finally:
        sock.close()
    return ip


class MdnsAdvertiser:
    """Registers and unregisters a single `_freesounder._tcp` service."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._aiozc: AsyncZeroconf | None = None

    async def start(self) -> None:
        if not self._settings.mdns_enabled:
            return
        ip = _local_ip()
        name = f"{self._settings.mdns_name}.{SERVICE_TYPE}"
        info = ServiceInfo(
            SERVICE_TYPE,
            name,
            addresses=[socket.inet_aton(ip)],
            port=self._settings.port,
            properties={
                "path": "/api/v1",
                "ws": "/api/v1/ws",
                "version": __version__,
            },
            server=f"{self._settings.mdns_name}.local.",
        )
        self._aiozc = AsyncZeroconf()
        await self._aiozc.async_register_service(info)
        logger.info("Advertised %s on %s:%s", name, ip, self._settings.port)

    async def stop(self) -> None:
        if self._aiozc is None:
            return
        await self._aiozc.async_unregister_all_services()
        await self._aiozc.async_close()
        self._aiozc = None

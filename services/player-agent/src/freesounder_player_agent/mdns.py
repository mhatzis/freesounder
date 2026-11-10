"""mDNS advertisement of the player agent as _freesounder-player._tcp."""

from __future__ import annotations

import logging
import socket

from zeroconf import ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

from freesounder_player_agent import __version__
from freesounder_player_agent.config import AgentSettings
from freesounder_player_agent.store import PlayerStore

logger = logging.getLogger(__name__)

SERVICE_TYPE = "_freesounder-player._tcp.local."


def _local_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("1.1.1.1", 80))
        ip = sock.getsockname()[0]
    except OSError:
        ip = "127.0.0.1"
    finally:
        sock.close()
    return ip


class PlayerAdvertiser:
    """Registers `_freesounder-player._tcp` with state and identity TXT records."""

    def __init__(self, settings: AgentSettings, store: PlayerStore) -> None:
        self._settings = settings
        self._store = store
        self._aiozc: AsyncZeroconf | None = None
        self._info: ServiceInfo | None = None

    def _service_info(self) -> ServiceInfo:
        info = self._store.info()
        ip = _local_ip()
        instance = f"{info.id}.{SERVICE_TYPE}"
        return ServiceInfo(
            SERVICE_TYPE,
            instance,
            addresses=[socket.inet_aton(ip)],
            port=self._settings.agent_port,
            properties={
                "id": info.id,
                "name": info.name,
                "state": info.state,
                "role": info.effective_role,
                "path": "/api/v1",
                "version": __version__,
            },
            server=f"{info.hostname}.local.",
        )

    async def start(self) -> None:
        if not self._settings.mdns_enabled:
            return
        self._info = self._service_info()
        self._aiozc = AsyncZeroconf()
        await self._aiozc.async_register_service(self._info)
        logger.info(
            "Advertised %s on port %s",
            self._info.name,
            self._settings.agent_port,
        )

    async def update(self) -> None:
        if self._aiozc is None:
            return
        self._info = self._service_info()
        await self._aiozc.async_update_service(self._info)

    async def stop(self) -> None:
        if self._aiozc is None:
            return
        await self._aiozc.async_unregister_all_services()
        await self._aiozc.async_close()
        self._aiozc = None

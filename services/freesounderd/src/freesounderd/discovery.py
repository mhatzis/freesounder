"""mDNS: advertise this player as _freesounder._tcp and browse for others."""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from zeroconf import IPVersion, ServiceStateChange, Zeroconf
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

logger = logging.getLogger(__name__)

SERVICE_TYPE = "_freesounder._tcp.local."


def local_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))
        ip = sock.getsockname()[0]
    except OSError:
        ip = "127.0.0.1"
    finally:
        sock.close()
    return ip


@dataclass
class Found:
    """A player seen via mDNS."""

    name: str
    ip: str
    port: int
    txt: dict[str, str] = field(default_factory=dict)
    seen: float = field(default_factory=time.monotonic)

    @property
    def id(self) -> str:
        return self.txt.get("id", "")


class Discovery:
    def __init__(
        self,
        *,
        port: int,
        txt: Callable[[], dict[str, str]],
        hostname: Callable[[], str],
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self.port = port
        self._txt = txt
        self._hostname = hostname
        self._on_change = on_change
        self._aiozc: AsyncZeroconf | None = None
        self._browser: AsyncServiceBrowser | None = None
        self._info: AsyncServiceInfo | None = None
        self._ip = ""
        self.found: dict[str, Found] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def _service_info(self) -> AsyncServiceInfo:
        txt = self._txt()
        self._ip = local_ip()
        return AsyncServiceInfo(
            SERVICE_TYPE,
            f"fs-{txt['id'][:8]}.{SERVICE_TYPE}",
            addresses=[socket.inet_aton(self._ip)],
            port=self.port,
            properties=txt,
            server=f"{self._hostname()}.local.",
        )

    async def start(self) -> None:
        self._aiozc = AsyncZeroconf(ip_version=IPVersion.V4Only)
        self._info = self._service_info()
        await self._aiozc.async_register_service(self._info, allow_name_change=True)
        self._browser = AsyncServiceBrowser(
            self._aiozc.zeroconf, SERVICE_TYPE, handlers=[self._on_service]
        )

    def _on_service(
        self,
        zeroconf: Zeroconf,
        service_type: str,
        name: str,
        state_change: ServiceStateChange,
    ) -> None:
        if state_change is ServiceStateChange.Removed:
            self.found.pop(name, None)
            if self._on_change:
                self._on_change()
            return
        task = asyncio.ensure_future(self._resolve(zeroconf, service_type, name))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _resolve(self, zeroconf: Zeroconf, service_type: str, name: str) -> None:
        info = AsyncServiceInfo(service_type, name)
        if not await info.async_request(zeroconf, 3000):
            return
        addrs = info.parsed_addresses(IPVersion.V4Only)
        if not addrs:
            return
        txt = {
            (k.decode() if isinstance(k, bytes) else str(k)): (
                v.decode() if isinstance(v, bytes) else ""
            )
            for k, v in (info.properties or {}).items()
        }
        self.found[name] = Found(name=name, ip=addrs[0], port=info.port or 8080, txt=txt)
        if self._on_change:
            self._on_change()

    async def refresh(self) -> None:
        """Re-advertise when TXT data or the IP changes."""

        if self._aiozc is None or self._info is None:
            return
        txt = self._txt()
        current = {
            (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
            for k, v in (self._info.properties or {}).items()
        }
        if current == txt and local_ip() == self._ip:
            return
        self._info = self._service_info()
        try:
            await self._aiozc.async_update_service(self._info)
        except Exception:  # noqa: BLE001 - zeroconf raises various errors
            logger.warning("mDNS update failed", exc_info=True)

    async def stop(self) -> None:
        if self._browser is not None:
            await self._browser.async_cancel()
        if self._aiozc is not None:
            await self._aiozc.async_unregister_all_services()
            await self._aiozc.async_close()
        self._aiozc = None

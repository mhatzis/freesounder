"""Discover player agents and proxy adopt / settings calls.

The Flutter app talks only to this control API. The household host browses
`_freesounder-player._tcp`, then forwards HTTP to the matching agent.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import Any, Protocol, runtime_checkable

import aiohttp

from freesounder_control_api.models import (
    AdoptBody,
    PlayerSettings,
    PlayerSettingsPatch,
    SshDeveloperBody,
    SshDeveloperStatus,
    UnclaimedPlayer,
)

logger = logging.getLogger(__name__)

PLAYER_SERVICE = "_freesounder-player._tcp.local."


def local_ip() -> str:
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


@runtime_checkable
class PlayerIndex(Protocol):
    """Lookup and proxy for per-player agents."""

    async def list_unclaimed(self) -> list[UnclaimedPlayer]: ...

    async def adopt(self, body: AdoptBody, household_host: str) -> UnclaimedPlayer: ...

    async def get_settings(self, player_id: str) -> PlayerSettings: ...

    async def patch_settings(
        self, player_id: str, patch: PlayerSettingsPatch
    ) -> PlayerSettings: ...

    async def ssh_status(self, player_id: str) -> SshDeveloperStatus: ...

    async def set_ssh(self, player_id: str, body: SshDeveloperBody) -> SshDeveloperStatus: ...


class PlayerNotFound(KeyError):
    """No agent (or mock player) matches the requested id."""


class MockPlayerIndex:
    """In-memory agents used when the control API runs with the mock backend."""

    def __init__(self) -> None:
        self.unclaimed: dict[str, UnclaimedPlayer] = {
            "attic-pi": UnclaimedPlayer(
                id="attic-pi",
                name="freesounder-attic",
                address="192.168.1.42",
                port=8090,
                role="room",
                state="unconfigured",
                url="http://192.168.1.42:8090",
            )
        }
        self.settings: dict[str, PlayerSettings] = {
            "kitchen": PlayerSettings(name="Kitchen", bass=2, treble=-1, volume_limit=100),
            "lounge": PlayerSettings(name="Lounge", balance=-2, volume_limit=90),
            "bedroom": PlayerSettings(name="Bedroom", loudness=True, volume_limit=80),
        }
        self.adopted: list[UnclaimedPlayer] = []
        self._on_adopted: Any = None
        self.ssh: dict[str, SshDeveloperStatus] = {}

    def bind_adopt(self, callback: Any) -> None:
        """Optional hook so a mock backend can add a new room."""

        self._on_adopted = callback

    async def list_unclaimed(self) -> list[UnclaimedPlayer]:
        return list(self.unclaimed.values())

    async def adopt(self, body: AdoptBody, household_host: str) -> UnclaimedPlayer:
        del household_host
        key = body.id
        player = None
        if key and key in self.unclaimed:
            player = self.unclaimed.pop(key)
        elif body.url:
            for item_id, item in list(self.unclaimed.items()):
                if item.url == body.url or item.id == body.url:
                    player = self.unclaimed.pop(item_id)
                    break
        if player is None:
            # Allow adopting a synthetic player so the demo wizard always works.
            player = UnclaimedPlayer(
                id=key or body.name.lower().replace(" ", "-"),
                name=body.name,
                address="127.0.0.1",
                port=8090,
                role="room",
                state="configured",
                url=body.url or "http://127.0.0.1:8090",
            )
        else:
            player = player.model_copy(update={"name": body.name, "state": "configured"})
        self.settings[player.id] = PlayerSettings(name=body.name)
        self.adopted.append(player)
        if self._on_adopted is not None:
            self._on_adopted(player)
        return player

    async def get_settings(self, player_id: str) -> PlayerSettings:
        if player_id in self.settings:
            return self.settings[player_id]
        raise PlayerNotFound(player_id)

    async def patch_settings(self, player_id: str, patch: PlayerSettingsPatch) -> PlayerSettings:
        current = await self.get_settings(player_id)
        updates = patch.model_dump(exclude_none=True)
        dac_changed = "dac" in updates and updates["dac"] != current.dac
        updated = current.model_copy(update=updates)
        if dac_changed:
            updated = updated.model_copy(update={"dac_reboot_required": True})
        self.settings[player_id] = updated
        return updated

    def _ssh_host(self, player_id: str) -> str:
        if player_id in self.unclaimed:
            return self.unclaimed[player_id].name
        for item in self.adopted:
            if item.id == player_id:
                return item.name
        settings = self.settings.get(player_id)
        if settings:
            return settings.name.lower().replace(" ", "-")
        return player_id

    async def ssh_status(self, player_id: str) -> SshDeveloperStatus:
        if player_id in self.ssh:
            current = self.ssh[player_id]
            return current.model_copy(update={"password": None})
        if player_id not in self.settings and player_id not in self.unclaimed:
            raise PlayerNotFound(player_id)
        host = self._ssh_host(player_id)
        return SshDeveloperStatus(
            enabled=False,
            hostname=host,
            command=f"ssh developer@{host}.local",
        )

    async def set_ssh(self, player_id: str, body: SshDeveloperBody) -> SshDeveloperStatus:
        if player_id not in self.settings and player_id not in self.unclaimed:
            raise PlayerNotFound(player_id)
        host = self._ssh_host(player_id)
        if not body.enabled:
            status = SshDeveloperStatus(
                enabled=False,
                hostname=host,
                command=f"ssh developer@{host}.local",
            )
            self.ssh[player_id] = status
            return status
        status = SshDeveloperStatus(
            enabled=True,
            hostname=host,
            command=f"ssh developer@{host}.local",
            password="demo-ssh-password",
        )
        self.ssh[player_id] = status
        return status


class LanPlayerIndex:
    """Browse `_freesounder-player._tcp` and forward HTTP to the chosen agent."""

    def __init__(
        self, *, timeout: float = 2.5, local_agent_url: str = "http://127.0.0.1:8090"
    ) -> None:
        self._timeout = timeout
        self._local_agent_url = local_agent_url.rstrip("/")

    async def list_agents(self) -> list[UnclaimedPlayer]:
        agents = await _browse_player_agents(self._timeout)
        # Always consider the local agent, which may not appear in mDNS from this host.
        try:
            local = await _fetch_agent(self._local_agent_url)
        except Exception:  # noqa: BLE001
            local = None
        if local is not None and all(item.id != local.id for item in agents):
            agents.append(local)
        return agents

    async def list_unclaimed(self) -> list[UnclaimedPlayer]:
        return [agent for agent in await self.list_agents() if agent.state == "unconfigured"]

    async def adopt(self, body: AdoptBody, household_host: str) -> UnclaimedPlayer:
        url = await self._resolve_url(body.id, body.url)
        payload = {"name": body.name, "host": household_host, "role": "room"}
        data = await _request_json("POST", f"{url}/api/v1/agent/adopt", json=payload)
        return UnclaimedPlayer(
            id=str(data.get("id") or body.id or body.name),
            name=str(data.get("name") or body.name),
            address=_host_of(url),
            port=_port_of(url),
            role=str(data.get("role") or "room"),
            state=str(data.get("state") or "configured"),
            url=url,
        )

    async def get_settings(self, player_id: str) -> PlayerSettings:
        url = await self._resolve_url(player_id, None)
        data = await _request_json("GET", f"{url}/api/v1/settings")
        return PlayerSettings.model_validate(data)

    async def patch_settings(self, player_id: str, patch: PlayerSettingsPatch) -> PlayerSettings:
        url = await self._resolve_url(player_id, None)
        data = await _request_json(
            "PATCH",
            f"{url}/api/v1/settings",
            json=patch.model_dump(exclude_none=True),
        )
        return PlayerSettings.model_validate(data)

    async def ssh_status(self, player_id: str) -> SshDeveloperStatus:
        url = await self._resolve_url(player_id, None)
        data = await _request_json("GET", f"{url}/api/v1/developer/ssh")
        return SshDeveloperStatus.model_validate(data)

    async def set_ssh(self, player_id: str, body: SshDeveloperBody) -> SshDeveloperStatus:
        url = await self._resolve_url(player_id, None)
        data = await _request_json(
            "POST",
            f"{url}/api/v1/developer/ssh",
            json=body.model_dump(exclude_none=True),
        )
        return SshDeveloperStatus.model_validate(data)

    async def _resolve_url(self, player_id: str | None, explicit_url: str | None) -> str:
        if explicit_url:
            return explicit_url.rstrip("/")
        if not player_id:
            raise PlayerNotFound("missing player id")
        # Local agent short-circuit: match its id or the reserved name "local".
        try:
            local = await _fetch_agent(self._local_agent_url)
        except Exception:  # noqa: BLE001
            local = None
        if local is not None and player_id in {local.id, local.name, "local"}:
            return self._local_agent_url
        for agent in await self.list_agents():
            if player_id in {agent.id, agent.name} or player_id.lower() == agent.name.lower():
                return agent.url.rstrip("/")
        raise PlayerNotFound(player_id)


async def _fetch_agent(base_url: str) -> UnclaimedPlayer | None:
    data = await _request_json("GET", f"{base_url}/api/v1/agent")
    return UnclaimedPlayer(
        id=str(data["id"]),
        name=str(data.get("name") or data["id"]),
        address=_host_of(base_url),
        port=_port_of(base_url),
        role=data.get("role"),
        state=str(data.get("state") or "configured"),
        url=base_url.rstrip("/"),
    )


async def _request_json(method: str, url: str, json: dict | None = None) -> dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=5)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.request(method, url, json=json) as response:
            if response.status >= 400:
                body = await response.text()
                raise PlayerNotFound(f"{url} -> HTTP {response.status}: {body}")
            return await response.json()


def _host_of(url: str) -> str:
    from urllib.parse import urlparse

    return urlparse(url).hostname or url


def _port_of(url: str) -> int:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.port:
        return parsed.port
    return 443 if parsed.scheme == "https" else 80


async def _browse_player_agents(wait_seconds: float) -> list[UnclaimedPlayer]:
    """Browse mDNS for player agents. Returns an empty list if zeroconf fails."""

    try:
        from zeroconf import ServiceBrowser, ServiceStateChange, Zeroconf
    except ImportError:
        return []

    found: dict[str, UnclaimedPlayer] = {}
    zc = Zeroconf()

    def _on_change(
        zeroconf: Zeroconf,
        service_type: str,
        name: str,
        state_change: ServiceStateChange,
    ) -> None:
        del service_type
        if state_change not in {ServiceStateChange.Added, ServiceStateChange.Updated}:
            return
        info = zeroconf.get_service_info(PLAYER_SERVICE, name, timeout=500)
        if info is None:
            return
        props = {
            k.decode() if isinstance(k, bytes) else str(k): _txt_value(v)
            for k, v in info.properties.items()
        }
        address = None
        for parsed in info.parsed_addresses():
            address = parsed
            break
        if not address:
            return
        port = info.port or 8090
        agent_id = props.get("id") or name.split(".", 1)[0]
        found[agent_id] = UnclaimedPlayer(
            id=agent_id,
            name=props.get("name") or agent_id,
            address=address,
            port=port,
            role=props.get("role"),
            state=props.get("state") or "unconfigured",
            url=f"http://{address}:{port}",
        )

    browser = ServiceBrowser(zc, PLAYER_SERVICE, handlers=[_on_change])
    try:
        await asyncio.sleep(wait_seconds)
    finally:
        browser.cancel()
        zc.close()
    return list(found.values())


def _txt_value(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value) if value is not None else ""

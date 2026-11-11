"""FastAPI application for the per-player agent."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from freesounder_player_agent import __version__
from freesounder_player_agent.config import AgentSettings, load_settings
from freesounder_player_agent.mdns import PlayerAdvertiser
from freesounder_player_agent.models import (
    AdoptBody,
    AgentInfo,
    Health,
    PlayerSettings,
    PlayerSettingsPatch,
    SshDeveloperBody,
    SshDeveloperStatus,
)
from freesounder_player_agent.ssh import ssh_set, ssh_status
from freesounder_player_agent.store import PlayerStore


def create_app(
    settings: AgentSettings | None = None,
    store: PlayerStore | None = None,
    *,
    enable_mdns: bool | None = None,
) -> FastAPI:
    """Application factory (tests pass a temp store and disable mDNS)."""

    settings = settings or load_settings()
    store = store or PlayerStore(settings)
    advertiser = PlayerAdvertiser(settings, store)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        should_advertise = settings.mdns_enabled if enable_mdns is None else enable_mdns
        if should_advertise:
            with suppress(Exception):
                await advertiser.start()
        yield
        await advertiser.stop()

    app = FastAPI(title="freesounder player agent", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.store = store
    app.state.settings = settings

    @app.get("/health", response_model=Health)
    async def health() -> Health:
        info = store.info()
        return Health(version=__version__, state=info.state, id=info.id)

    @app.get("/api/v1/agent", response_model=AgentInfo)
    async def agent() -> AgentInfo:
        return store.info()

    @app.post("/api/v1/agent/adopt", response_model=AgentInfo)
    async def adopt(body: AdoptBody) -> AgentInfo:
        info = store.adopt(body)
        with suppress(Exception):
            await advertiser.update()
        return info

    @app.get("/api/v1/settings", response_model=PlayerSettings)
    async def get_settings() -> PlayerSettings:
        return store.load_settings()

    @app.patch("/api/v1/settings", response_model=PlayerSettings)
    async def patch_settings(body: PlayerSettingsPatch) -> PlayerSettings:
        result = store.patch_settings(body)
        with suppress(Exception):
            await advertiser.update()
        return result

    @app.get("/api/v1/developer/ssh", response_model=SshDeveloperStatus)
    async def get_ssh() -> SshDeveloperStatus:
        return ssh_status(settings.ssh_command, store.info().hostname)

    @app.post("/api/v1/developer/ssh", response_model=SshDeveloperStatus)
    async def set_ssh(body: SshDeveloperBody) -> SshDeveloperStatus:
        if body.enabled and not body.confirm:
            raise HTTPException(
                status_code=400,
                detail="Set confirm=true to enable developer SSH. "
                "The control API has no authentication.",
            )
        return ssh_set(
            settings.ssh_command,
            store.info().hostname,
            enabled=body.enabled,
            public_key=body.public_key,
        )

    return app

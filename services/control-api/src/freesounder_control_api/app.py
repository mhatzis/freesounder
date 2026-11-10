"""FastAPI application: HTTP facade and WebSocket event stream."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from freesounder_control_api import __version__
from freesounder_control_api.backend import MusicBackend
from freesounder_control_api.config import Settings, load_settings
from freesounder_control_api.mdns import MdnsAdvertiser
from freesounder_control_api.mock_backend import MockMusicBackend
from freesounder_control_api.models import (
    BrowseResponse,
    CreateGroupBody,
    EventMessage,
    Health,
    JoinLeaveBody,
    NowPlaying,
    Room,
    SearchResponse,
    VolumeBody,
)

logger = logging.getLogger(__name__)


class Hub:
    """Shared backend, settings and WebSocket subscribers."""

    def __init__(self, settings: Settings, backend: MusicBackend) -> None:
        self.settings = settings
        self.backend = backend
        self.subscribers: set[WebSocket] = set()

    async def broadcast(self, event: str, data: Any) -> None:
        message = EventMessage(event=event, data=data).model_dump()
        stale: list[WebSocket] = []
        for ws in list(self.subscribers):
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001
                stale.append(ws)
        for ws in stale:
            self.subscribers.discard(ws)


def create_backend(settings: Settings) -> MusicBackend:
    """Build a mock or live Music Assistant backend from settings."""

    if settings.mock_ma:
        return MockMusicBackend()
    from freesounder_control_api.ma_backend import MusicAssistantBackend

    return MusicAssistantBackend(settings.ma_url, settings.ma_token)


def create_app(
    settings: Settings | None = None,
    backend: MusicBackend | None = None,
    *,
    enable_mdns: bool | None = None,
) -> FastAPI:
    """Application factory (tests pass an in-memory backend and disable mDNS)."""

    settings = settings or load_settings()
    backend = backend or create_backend(settings)
    hub = Hub(settings, backend)
    advertiser = MdnsAdvertiser(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        with suppress(Exception):
            await backend.connect()
        should_advertise = settings.mdns_enabled if enable_mdns is None else enable_mdns
        if should_advertise:
            with suppress(Exception):
                await advertiser.start()
        poll = asyncio.create_task(_poll_events(hub))
        yield
        poll.cancel()
        with suppress(asyncio.CancelledError):
            await poll
        await advertiser.stop()
        with suppress(Exception):
            await backend.disconnect()

    app = FastAPI(
        title="freesounder control API",
        version=__version__,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.hub = hub

    @app.get("/health", response_model=Health)
    async def health() -> Health:
        connected = await backend.health_connected()
        return Health(
            status="ok" if connected else "degraded",
            ma_connected=connected,
            version=__version__,
            mock=settings.mock_ma or isinstance(backend, MockMusicBackend),
        )

    @app.get("/api/v1/rooms", response_model=list[Room])
    async def list_rooms() -> list[Room]:
        return await backend.list_rooms()

    @app.get("/api/v1/groups", response_model=list[Room])
    async def list_groups() -> list[Room]:
        return [room for room in await backend.list_rooms() if room.is_group]

    @app.post("/api/v1/groups", response_model=Room)
    async def create_group(body: CreateGroupBody) -> Room:
        provider = body.provider or settings.group_provider
        room = await backend.create_group(body.name, body.member_ids, provider, body.dynamic)
        await hub.broadcast("rooms", [r.model_dump() for r in await backend.list_rooms()])
        return room

    @app.post("/api/v1/groups/{group_id}/join", status_code=204)
    async def join_group(group_id: str, body: JoinLeaveBody) -> None:
        await backend.join_group(group_id, body.player_id)
        await hub.broadcast("rooms", [r.model_dump() for r in await backend.list_rooms()])

    @app.post("/api/v1/groups/{group_id}/leave", status_code=204)
    async def leave_group(group_id: str, body: JoinLeaveBody) -> None:
        await backend.leave_group(group_id, body.player_id)
        await hub.broadcast("rooms", [r.model_dump() for r in await backend.list_rooms()])

    @app.get("/api/v1/now-playing", response_model=NowPlaying)
    async def now_playing(player_id: str = Query(..., min_length=1)) -> NowPlaying:
        return await backend.now_playing(player_id)

    @app.post("/api/v1/players/{player_id}/play", status_code=204)
    async def play(player_id: str) -> None:
        await backend.play(player_id)
        await hub.broadcast(
            "now_playing",
            (await backend.now_playing(player_id)).model_dump(),
        )

    @app.post("/api/v1/players/{player_id}/pause", status_code=204)
    async def pause(player_id: str) -> None:
        await backend.pause(player_id)
        await hub.broadcast(
            "now_playing",
            (await backend.now_playing(player_id)).model_dump(),
        )

    @app.post("/api/v1/players/{player_id}/next", status_code=204)
    async def next_track(player_id: str) -> None:
        await backend.next_track(player_id)
        await hub.broadcast(
            "now_playing",
            (await backend.now_playing(player_id)).model_dump(),
        )

    @app.post("/api/v1/players/{player_id}/volume", status_code=204)
    async def set_volume(player_id: str, body: VolumeBody) -> None:
        await backend.set_volume(player_id, body.level)
        await hub.broadcast("rooms", [r.model_dump() for r in await backend.list_rooms()])

    @app.post("/api/v1/groups/{group_id}/volume", status_code=204)
    async def set_group_volume(group_id: str, body: VolumeBody) -> None:
        await backend.set_group_volume(group_id, body.level)
        await hub.broadcast("rooms", [r.model_dump() for r in await backend.list_rooms()])

    @app.get("/api/v1/search", response_model=SearchResponse)
    async def search(
        q: str = Query(..., min_length=1),
        limit: int = Query(50, ge=1, le=100),
    ) -> SearchResponse:
        return await backend.search(q, limit=limit)

    @app.get("/api/v1/browse", response_model=BrowseResponse)
    async def browse(path: str | None = None) -> BrowseResponse:
        return await backend.browse(path)

    @app.websocket("/api/v1/ws")
    async def websocket(ws: WebSocket) -> None:
        await ws.accept()
        hub.subscribers.add(ws)
        try:
            connected = await backend.health_connected()
            await ws.send_json(
                EventMessage(
                    event="health",
                    data=Health(
                        status="ok" if connected else "degraded",
                        ma_connected=connected,
                        version=__version__,
                        mock=settings.mock_ma or isinstance(backend, MockMusicBackend),
                    ).model_dump(),
                ).model_dump()
            )
            rooms = await backend.list_rooms()
            await ws.send_json(
                EventMessage(event="rooms", data=[r.model_dump() for r in rooms]).model_dump()
            )
            while True:
                # Keep the socket open; the client may send pings or ignore outbound-only.
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            hub.subscribers.discard(ws)

    return app


async def _poll_events(hub: Hub) -> None:
    """Push a rooms snapshot periodically so WS clients stay reasonably fresh."""

    while True:
        await asyncio.sleep(2.0)
        try:
            rooms = await hub.backend.list_rooms()
            await hub.broadcast("rooms", [r.model_dump() for r in rooms])
        except Exception:  # noqa: BLE001
            logger.debug("event poll failed", exc_info=True)

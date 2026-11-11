"""FastAPI application: HTTP facade and WebSocket event stream."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from freesounder_control_api import __version__
from freesounder_control_api.backend import MusicAssistantUnavailable, MusicBackend
from freesounder_control_api.config import Settings, load_settings
from freesounder_control_api.mdns import MdnsAdvertiser
from freesounder_control_api.mock_backend import MockMusicBackend
from freesounder_control_api.models import (
    AddMusicServiceBody,
    AdoptBody,
    AuthSession,
    BrowseResponse,
    CreateGroupBody,
    EventMessage,
    Health,
    JoinLeaveBody,
    MusicService,
    MusicServicesCatalog,
    MusicServiceType,
    NowPlaying,
    PlayerSettings,
    PlayerSettingsPatch,
    Room,
    SearchResponse,
    SpotifyConnectStatus,
    SpotifyConnectToggleBody,
    SshDeveloperBody,
    SshDeveloperStatus,
    StartAuthBody,
    SubmitAuthBody,
    SystemInfo,
    UnclaimedPlayer,
    UpdateMusicServiceBody,
    VolumeBody,
)
from freesounder_control_api.music_catalog import CALLBACK_PATH, CALLBACK_SCHEME
from freesounder_control_api.music_sessions import MusicServiceError
from freesounder_control_api.players import (
    AgentError,
    LanPlayerIndex,
    MockPlayerIndex,
    PlayerIndex,
    PlayerNotFound,
    local_ip,
)

logger = logging.getLogger(__name__)


class Hub:
    """Shared backend, settings and WebSocket subscribers."""

    def __init__(
        self,
        settings: Settings,
        backend: MusicBackend,
        players: PlayerIndex,
    ) -> None:
        self.settings = settings
        self.backend = backend
        self.players = players
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

    return MusicAssistantBackend(
        settings.ma_url,
        settings.ma_token,
        token_file=settings.ma_token_file,
        admin_file=settings.ma_admin_file,
        auto_setup=settings.ma_auto_setup,
        status_file=settings.ma_status_file,
    )


def backend_status(backend: MusicBackend, connected: bool) -> tuple[str, str | None]:
    status = getattr(backend, "status", None)
    if callable(status):
        return status()
    return ("connected", None) if connected else ("unavailable", None)


def create_app(
    settings: Settings | None = None,
    backend: MusicBackend | None = None,
    players: PlayerIndex | None = None,
    *,
    enable_mdns: bool | None = None,
) -> FastAPI:
    """Application factory (tests pass an in-memory backend and disable mDNS)."""

    settings = settings or load_settings()
    backend = backend or create_backend(settings)
    if players is None:
        if settings.mock_ma or isinstance(backend, MockMusicBackend):
            players = MockPlayerIndex()
            if isinstance(backend, MockMusicBackend):
                def _on_adopted(player: UnclaimedPlayer) -> None:
                    backend.add_room(player.id, player.name)

                players.bind_adopt(_on_adopted)
                # Seed settings for the two default mock rooms if missing.
                if hasattr(players, "settings"):
                    for room in backend.rooms.values():
                        players.settings.setdefault(room.id, PlayerSettings(name=room.name))
        else:
            players = LanPlayerIndex()
    hub = Hub(settings, backend, players)

    async def _forward_backend_event(event: str, data: Any) -> None:
        await hub.broadcast(event, data)

    bind = getattr(backend, "bind_listener", None)
    if callable(bind):
        bind(_forward_backend_event)
    advertiser = MdnsAdvertiser(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        # Never block startup on Music Assistant: on a fresh host it can take
        # minutes to pull and start, and the app must reach /health, /system and
        # adopt meanwhile. run_forever() keeps retrying in the background.
        connector: asyncio.Task[None] | None = None
        run_forever = getattr(backend, "run_forever", None)
        if callable(run_forever):
            connector = asyncio.create_task(run_forever())
        else:
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
        if connector is not None:
            connector.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await connector
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

    @app.exception_handler(AgentError)
    async def _agent_error(request: Request, exc: AgentError) -> JSONResponse:
        del request
        return JSONResponse(status_code=502, content={"detail": f"Player agent error: {exc}"})

    @app.exception_handler(MusicAssistantUnavailable)
    async def _not_ready(request: Request, exc: MusicAssistantUnavailable) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=503,
            content={"detail": str(exc), "ma_state": exc.state, "ma_detail": exc.detail},
        )

    @app.get("/health", response_model=Health)
    async def health() -> Health:
        connected = await backend.health_connected()
        state, detail = backend_status(backend, connected)
        return Health(
            status="ok" if connected else "degraded",
            ma_connected=connected,
            version=__version__,
            mock=settings.mock_ma or isinstance(backend, MockMusicBackend),
            ma_state=state,
            ma_detail=detail,
        )

    @app.get("/")
    async def root() -> dict[str, Any]:
        """Friendly landing for a browser pointed at the player."""

        connected = await backend.health_connected()
        state, detail = backend_status(backend, connected)
        return {
            "service": "FreeSounder control API",
            "name": settings.mdns_name,
            "version": __version__,
            "status": "ok" if connected else "degraded",
            "music_assistant": {"state": state, "detail": detail},
            "address": local_ip(),
            "links": {
                "health": "/health",
                "system": "/api/v1/system",
                "rooms": "/api/v1/rooms",
                "other_speakers": "/api/v1/other-speakers",
                "players": "/api/v1/players",
                "docs": "/docs",
            },
        }

    @app.get("/api/v1/players", response_model=list[UnclaimedPlayer])
    async def list_players() -> list[UnclaimedPlayer]:
        """Every player agent this host can see (adopted or not), for diagnostics."""

        list_all = getattr(players, "list_agents", None)
        if callable(list_all):
            return await list_all()
        return await players.list_unclaimed()

    @app.get("/api/v1/rooms", response_model=list[Room])
    async def list_rooms() -> list[Room]:
        return await backend.list_rooms()

    @app.get("/api/v1/other-speakers", response_model=list[Room])
    async def list_other_speakers() -> list[Room]:
        """AirPlay / Chromecast speakers Music Assistant found (not FreeSounder rooms)."""

        lister = getattr(backend, "list_other_speakers", None)
        return await lister() if lister is not None else []

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
        level = body.level
        try:
            player_settings = await players.get_settings(player_id)
            level = min(level, player_settings.volume_limit)
        except PlayerNotFound:
            pass
        await backend.set_volume(player_id, level)
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

    @app.get("/api/v1/system", response_model=SystemInfo)
    async def system() -> SystemInfo:
        connected = await backend.health_connected()
        state, detail = backend_status(backend, connected)
        return SystemInfo(
            name=settings.mdns_name,
            version=__version__,
            role="host",
            mock=settings.mock_ma or isinstance(backend, MockMusicBackend),
            ma_connected=connected,
            ma_state=state,
            ma_detail=detail,
            address=local_ip(),
        )

    @app.get("/api/v1/players/unclaimed", response_model=list[UnclaimedPlayer])
    async def list_unclaimed() -> list[UnclaimedPlayer]:
        return await players.list_unclaimed()

    @app.post("/api/v1/players/adopt", response_model=UnclaimedPlayer)
    async def adopt_player(body: AdoptBody) -> UnclaimedPlayer:
        if not body.id and not body.url:
            raise HTTPException(status_code=422, detail="Provide a player id or agent url")
        household = settings.mdns_name
        # The player needs a reachable host address, not just the mDNS instance name.
        host_addr = local_ip()
        try:
            result = await players.adopt(body, host_addr or household)
        except PlayerNotFound as exc:
            raise HTTPException(status_code=404, detail=f"Player not found: {exc}") from exc
        except AgentError as exc:
            raise HTTPException(status_code=502, detail=f"Player agent error: {exc}") from exc
        with suppress(Exception):
            await hub.broadcast("rooms", [r.model_dump() for r in await backend.list_rooms()])
        return result

    @app.get("/api/v1/players/{player_id}/settings", response_model=PlayerSettings)
    async def get_player_settings(player_id: str) -> PlayerSettings:
        try:
            return await players.get_settings(player_id)
        except PlayerNotFound as exc:
            # Fall back to the room's display name if the backend knows the room.
            rooms = {room.id: room for room in await backend.list_rooms()}
            if player_id in rooms:
                return PlayerSettings(name=rooms[player_id].name)
            raise HTTPException(status_code=404, detail=f"Player not found: {exc}") from exc

    @app.patch("/api/v1/players/{player_id}/settings", response_model=PlayerSettings)
    async def patch_player_settings(player_id: str, body: PlayerSettingsPatch) -> PlayerSettings:
        try:
            result = await players.patch_settings(player_id, body)
        except PlayerNotFound as exc:
            raise HTTPException(status_code=404, detail=f"Player not found: {exc}") from exc
        if body.name and isinstance(backend, MockMusicBackend):
            backend.rename_room(player_id, body.name)
        await hub.broadcast("rooms", [r.model_dump() for r in await backend.list_rooms()])
        return result


    @app.get("/api/v1/players/{player_id}/developer/ssh", response_model=SshDeveloperStatus)
    async def get_player_ssh(player_id: str) -> SshDeveloperStatus:
        """SSH developer status for an adopted player (proxied to the agent)."""
        try:
            return await players.ssh_status(player_id)
        except PlayerNotFound as exc:
            raise HTTPException(status_code=404, detail=f"Player not found: {exc}") from exc

    @app.post("/api/v1/players/{player_id}/developer/ssh", response_model=SshDeveloperStatus)
    async def set_player_ssh(player_id: str, body: SshDeveloperBody) -> SshDeveloperStatus:
        """Enable or disable ephemeral SSH on an adopted player.

        Requires confirm=true to enable. The control API currently has no
        authentication; only use this on a trusted household LAN.
        """
        if body.enabled and not body.confirm:
            raise HTTPException(
                status_code=400,
                detail="Set confirm=true to enable developer SSH. "
                "The control API has no authentication.",
            )
        try:
            return await players.set_ssh(player_id, body)
        except PlayerNotFound as exc:
            raise HTTPException(status_code=404, detail=f"Player not found: {exc}") from exc

    def _music_error(exc: MusicServiceError) -> HTTPException:
        return HTTPException(status_code=exc.status_code, detail=exc.detail)

    @app.get("/api/v1/music-services/catalog", response_model=MusicServicesCatalog)
    async def music_services_catalog() -> MusicServicesCatalog:
        try:
            return await backend.catalog_music_services()
        except MusicServiceError as exc:
            raise _music_error(exc) from exc

    @app.get("/api/v1/music-services/types", response_model=list[MusicServiceType])
    async def list_music_service_types() -> list[MusicServiceType]:
        try:
            return await backend.list_music_service_types()
        except MusicServiceError as exc:
            raise _music_error(exc) from exc

    @app.get("/api/v1/music-services", response_model=list[MusicService])
    async def list_music_services() -> list[MusicService]:
        try:
            return await backend.list_music_services()
        except MusicServiceError as exc:
            raise _music_error(exc) from exc

    @app.get("/api/v1/music-services/spotify-connect", response_model=SpotifyConnectStatus)
    async def get_spotify_connect() -> SpotifyConnectStatus:
        try:
            return await backend.spotify_connect_status()
        except MusicServiceError as exc:
            raise _music_error(exc) from exc

    @app.post(
        "/api/v1/music-services/spotify-connect/{player_id}",
        response_model=SpotifyConnectStatus,
    )
    async def set_spotify_connect(
        player_id: str, body: SpotifyConnectToggleBody
    ) -> SpotifyConnectStatus:
        try:
            return await backend.set_spotify_connect(player_id, body.enabled)
        except MusicServiceError as exc:
            raise _music_error(exc) from exc

    @app.post("/api/v1/music-services/auth", response_model=AuthSession)
    async def start_music_service_auth(body: StartAuthBody) -> AuthSession:
        try:
            return await backend.start_music_service_auth(body)
        except MusicServiceError as exc:
            raise _music_error(exc) from exc

    @app.get("/api/v1/music-services/auth/{session_id}", response_model=AuthSession)
    async def get_music_service_auth(session_id: str) -> AuthSession:
        try:
            return await backend.get_music_service_auth(session_id)
        except MusicServiceError as exc:
            raise _music_error(exc) from exc

    @app.post("/api/v1/music-services/auth/{session_id}/submit", response_model=AuthSession)
    async def submit_music_service_auth(session_id: str, body: SubmitAuthBody) -> AuthSession:
        try:
            return await backend.submit_music_service_auth(session_id, body)
        except MusicServiceError as exc:
            raise _music_error(exc) from exc

    @app.post("/api/v1/music-services/auth/{session_id}/abort", response_model=AuthSession)
    async def abort_music_service_auth(session_id: str) -> AuthSession:
        try:
            return await backend.abort_music_service_auth(session_id)
        except MusicServiceError as exc:
            raise _music_error(exc) from exc

    @app.api_route(
        "/api/v1/music-services/auth/{session_id}/callback",
        methods=["GET", "POST"],
        response_model=None,
    )
    async def music_service_auth_callback(
        session_id: str, request: Request
    ) -> RedirectResponse:
        """Landing URL for demo auth and for in-app browsers that return here.

        Completes the session when the backend can, then redirects to
        freesounder://music-services/auth so ASWebAuthenticationSession can close.
        """
        params = {key: value for key, value in request.query_params.items()}
        if request.method == "POST":
            try:
                form = await request.form()
                params.update({str(key): str(value) for key, value in form.items()})
            except Exception:  # noqa: BLE001
                pass
        try:
            session = await backend.complete_music_service_auth_callback(session_id, params)
        except MusicServiceError as exc:
            raise _music_error(exc) from exc
        target = (
            f"{CALLBACK_SCHEME}://{CALLBACK_PATH}"
            f"?session_id={session.session_id}&status={session.status}"
        )
        return RedirectResponse(url=target, status_code=302)

    @app.get("/api/v1/music-services/{instance_id}", response_model=MusicService)
    async def get_music_service(instance_id: str) -> MusicService:
        try:
            return await backend.get_music_service(instance_id)
        except MusicServiceError as exc:
            raise _music_error(exc) from exc

    @app.post("/api/v1/music-services")
    async def add_music_service(body: AddMusicServiceBody) -> AuthSession | MusicService:
        try:
            return await backend.add_music_service(body)
        except MusicServiceError as exc:
            raise _music_error(exc) from exc

    @app.patch("/api/v1/music-services/{instance_id}", response_model=MusicService)
    async def update_music_service(
        instance_id: str, body: UpdateMusicServiceBody
    ) -> MusicService:
        try:
            return await backend.update_music_service(instance_id, body)
        except MusicServiceError as exc:
            raise _music_error(exc) from exc

    @app.delete("/api/v1/music-services/{instance_id}", status_code=204)
    async def remove_music_service(instance_id: str) -> None:
        try:
            await backend.remove_music_service(instance_id)
        except MusicServiceError as exc:
            raise _music_error(exc) from exc

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
        poll_auth = getattr(hub.backend, "poll_music_service_auth", None)
        if callable(poll_auth):
            try:
                await poll_auth()
            except Exception:  # noqa: BLE001
                logger.debug("auth session poll failed", exc_info=True)

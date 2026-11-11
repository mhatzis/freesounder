"""Real Music Assistant backend, wrapping music-assistant-client 1.8.x."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
from music_assistant_client import MusicAssistantClient

from freesounder_control_api import ma_compat
from freesounder_control_api.backend import (
    MusicAssistantUnavailable,
    _flatten_search,
    _player_to_room,
    dict_to_search_items,
)
from freesounder_control_api.ma_bootstrap import (
    BootstrapError,
    is_local_server,
    obtain_token,
    read_secret,
)
from freesounder_control_api.ma_music_services import MaMusicServices
from freesounder_control_api.models import (
    AddMusicServiceBody,
    AuthSession,
    BrowseResponse,
    MusicService,
    MusicServicesCatalog,
    MusicServiceType,
    NowPlaying,
    Room,
    SearchResponse,
    SpotifyConnectStatus,
    StartAuthBody,
    SubmitAuthBody,
    UpdateMusicServiceBody,
)

logger = logging.getLogger(__name__)


class MusicAssistantBackend:
    """Facade over MusicAssistantClient controllers (players, player_queues, music)."""

    def __init__(
        self,
        server_url: str,
        token: str | None = None,
        *,
        token_file: Path | None = None,
        admin_file: Path | None = None,
        auto_setup: bool = False,
        status_file: Path | None = None,
    ) -> None:
        self._server_url = server_url
        self._token = token or None
        self._token_file = token_file
        self._admin_file = admin_file
        self._auto_setup = auto_setup and is_local_server(server_url)
        self._status_file = status_file
        self._client: MusicAssistantClient | None = None
        self._listen_task: asyncio.Task[None] | None = None
        self._music_services = MaMusicServices(self)
        self.state = "starting"
        self.detail: str | None = "Connecting to Music Assistant"
        self.snapcast_ok = False
        self.snapcast_detail: str | None = None

    @property
    def client(self) -> MusicAssistantClient:
        if self._client is None or not self.is_connected():
            raise MusicAssistantUnavailable(
                f"Music Assistant is {self.state.replace('_', ' ')}: {self.detail}",
                state=self.state if self.state != "connected" else "unavailable",
                detail=self.detail,
            )
        return self._client

    def is_connected(self) -> bool:
        client = self._client
        if client is None:
            return False
        if self._listen_task is not None and self._listen_task.done():
            return False
        connection = getattr(client, "connection", None)
        connected = getattr(connection, "connected", True)
        return bool(connected)

    def status(self) -> tuple[str, str | None]:
        if self.is_connected():
            return "connected", self.snapcast_detail
        return self.state, self.detail

    def _current_token(self) -> str | None:
        if self._token:
            return self._token
        if self._token_file is not None:
            return read_secret(self._token_file)
        return None

    def _launcher_detail(self) -> str | None:
        """What run-music-assistant last reported (loading, waiting for network...)."""

        if self._status_file is None:
            return None
        try:
            data = json.loads(self._status_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return str(data.get("detail") or data.get("state") or "") or None

    async def _listening(self) -> bool:
        parsed = urlparse(self._server_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme in {"https", "wss"} else 80)
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 3)
        except (OSError, TimeoutError):
            return False
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return True

    def _set(self, state: str, detail: str | None) -> MusicAssistantUnavailable:
        self.state = state
        self.detail = detail
        return MusicAssistantUnavailable(f"Music Assistant is {state}: {detail}", state, detail)

    async def connect(self, wait_seconds: float = 15.0) -> None:
        """One connection attempt. Raises MusicAssistantUnavailable on failure.

        Music Assistant runs in Docker and can take minutes on a fresh host
        (loading the bundled image, first start), so callers use run_forever()
        rather than blocking startup on this.
        """

        if not await self._listening():
            raise self._set(
                "starting",
                self._launcher_detail() or f"Waiting for Music Assistant at {self._server_url}",
            )
        token = self._current_token()
        if token is None and self._auto_setup and self._admin_file and self._token_file:
            token = await self._bootstrap()
        try:
            await self._connect_with(token, wait_seconds)
        except MusicAssistantUnavailable as exc:
            can_retry = self._auto_setup and self._admin_file and self._token_file
            if exc.state == "auth_required" and can_retry and not self._token:
                token = await self._bootstrap()
                await self._connect_with(token, wait_seconds)
            else:
                raise
        self.state, self.detail = "connected", None
        logger.info("Connected to Music Assistant at %s", self._server_url)
        await self._after_connect()

    async def _bootstrap(self) -> str:
        assert self._admin_file is not None and self._token_file is not None
        try:
            return await obtain_token(self._server_url, self._admin_file, self._token_file)
        except BootstrapError as exc:
            raise self._set("auth_required", str(exc)) from exc
        except (aiohttp.ClientError, OSError, TimeoutError) as exc:
            raise self._set("starting", f"Music Assistant setup not ready: {exc}") from exc

    async def _connect_with(self, token: str | None, wait_seconds: float) -> None:
        client = MusicAssistantClient(self._server_url, None, token=token)
        ready = asyncio.Event()
        listen = asyncio.create_task(client.start_listening(ready))
        waiter = asyncio.create_task(ready.wait())
        try:
            await asyncio.wait(
                {listen, waiter},
                timeout=wait_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            waiter.cancel()
        if ready.is_set() and not listen.done():
            self._client = client
            self._listen_task = listen
            self._music_services.attach_events()
            return
        reason = "did not finish connecting in time"
        if listen.done() and not listen.cancelled() and listen.exception() is not None:
            reason = f"{type(listen.exception()).__name__}: {listen.exception()}"
        listen.cancel()
        with contextlib.suppress(BaseException):
            await listen
        with contextlib.suppress(Exception):
            await client.disconnect()
        lowered = reason.lower()
        if "auth" in lowered or "token" in lowered:
            raise self._set("auth_required", f"Music Assistant rejected the login: {reason}")
        raise self._set("unavailable", f"Music Assistant at {self._server_url}: {reason}")

    async def _after_connect(self) -> None:
        """Host only: finish onboarding and make sure Snapcast players exist."""

        if not self._auto_setup or self._client is None:
            return
        try:
            await self._client.send_command("config/onboard_complete")
        except Exception as exc:  # noqa: BLE001
            logger.debug("onboard_complete skipped: %s", exc)
        await self._ensure_snapcast()

    async def _ensure_snapcast(self) -> None:
        if self._client is None:
            return
        try:
            result = await ma_compat.ensure_snapcast(self._client)
        except Exception as exc:  # noqa: BLE001
            self.snapcast_ok = False
            self.snapcast_detail = f"Snapcast provider not enabled: {type(exc).__name__}: {exc}"
            logger.warning("Could not ensure the Snapcast provider", exc_info=True)
            return
        if not self.snapcast_ok:
            logger.info("Music Assistant Snapcast provider: %s", result)
        self.snapcast_ok = True
        self.snapcast_detail = None

    async def run_forever(self, retry_seconds: float = 5.0) -> None:
        """Keep a Music Assistant connection open, reconnecting when it drops."""

        while True:
            if not self.is_connected():
                if self._client is not None:
                    with contextlib.suppress(Exception):
                        await self.disconnect()
                try:
                    await self.connect()
                except MusicAssistantUnavailable as exc:
                    logger.info("Music Assistant %s: %s", exc.state, exc.detail)
                except Exception as exc:  # noqa: BLE001
                    self._set("unavailable", f"{type(exc).__name__}: {exc}")
                    logger.warning("Music Assistant connect failed", exc_info=True)
            elif self._auto_setup and not self.snapcast_ok:
                await self._ensure_snapcast()
            await asyncio.sleep(retry_seconds)

    async def disconnect(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.disconnect()
        if self._listen_task is not None:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._listen_task = None
        self._client = None
        self._music_services.detach_events()

    async def health_connected(self) -> bool:
        return self.is_connected()

    async def list_rooms(self) -> list[Room]:
        if not self.is_connected():
            # Fresh host: Music Assistant may still be pulling or starting.
            return []
        players = list(self.client.players.players)
        rooms = [_player_to_room(player) for player in players]
        return [room for room in rooms if room.kind == "freesounder"]

    async def list_other_speakers(self) -> list[Room]:
        """AirPlay/Chromecast/etc. players MA discovered: not FreeSounder rooms."""

        if not self.is_connected():
            return []
        rooms = [_player_to_room(player) for player in self.client.players.players]
        return [room for room in rooms if room.kind != "freesounder"]

    def snapshot_rooms(self) -> list[Room]:
        if self._client is None:
            return []
        rooms = [_player_to_room(player) for player in self._client.players.players]
        return [room for room in rooms if room.kind == "freesounder"]

    async def create_group(
        self,
        name: str,
        member_ids: list[str],
        provider: str,
        dynamic: bool | None,
    ) -> Room:
        player = await self.client.players.create_group_player(
            provider=provider,
            name=name,
            members=member_ids,
            dynamic=dynamic,
        )
        if hasattr(self.client.players, "fetch_state"):
            await self.client.players.fetch_state()
        return _player_to_room(player)

    async def join_group(self, group_id: str, player_id: str) -> None:
        # Official client: group(player_id, target_player) adds player to the leader/group.
        await self.client.players.group(player_id, group_id)

    async def leave_group(self, group_id: str, player_id: str) -> None:
        del group_id
        await self.client.players.ungroup(player_id)

    async def now_playing(self, player_id: str) -> NowPlaying:
        player = self.client.players.get(player_id)
        state = "idle"
        if player is not None:
            raw_state = getattr(
                getattr(player, "state", None), "value", getattr(player, "state", None)
            )
            if raw_state is not None:
                state = str(raw_state)
        title = artist = album = image = None
        position = duration = None
        media = getattr(player, "current_media", None) if player is not None else None
        if media is not None:
            title = getattr(media, "title", None)
            artist = getattr(media, "artist", None)
            album = getattr(media, "album", None)
            image = getattr(media, "image_url", None) or getattr(media, "uri", None)
            duration = getattr(media, "duration", None)
        queue = None
        get_active = getattr(self.client.player_queues, "get_active_queue", None)
        if get_active is not None:
            try:
                queue = await get_active(player_id)
            except Exception:  # noqa: BLE001 — passthrough must not break now-playing
                logger.debug("get_active_queue failed for %s", player_id, exc_info=True)
        if queue is not None:
            current = getattr(queue, "current_item", None)
            if current is not None:
                item = getattr(current, "media_item", None) or current
                title = getattr(item, "name", None) or title
                artists = getattr(item, "artists", None)
                if artists and not artist:
                    names = [getattr(a, "name", str(a)) for a in artists]
                    artist = ", ".join(str(n) for n in names if n)
                album_obj = getattr(item, "album", None)
                if album_obj is not None:
                    album = getattr(album_obj, "name", None) or album
            elapsed = getattr(queue, "elapsed_time", None)
            if elapsed is not None:
                position = float(elapsed)
        return NowPlaying(
            player_id=player_id,
            title=title,
            artist=artist,
            album=album,
            image_url=image,
            state=state,
            position=position,
            duration=float(duration) if duration is not None else None,
        )

    async def play(self, player_id: str) -> None:
        await self.client.players.play(player_id)

    async def pause(self, player_id: str) -> None:
        await self.client.players.pause(player_id)

    async def next_track(self, player_id: str) -> None:
        await self.client.players.next_track(player_id)

    async def set_volume(self, player_id: str, level: int) -> None:
        await self.client.players.volume_set(player_id, level)

    async def set_group_volume(self, group_id: str, level: int) -> None:
        await self.client.players.group_volume(group_id, level)

    async def search(self, query: str, limit: int = 50) -> SearchResponse:
        raw = await self.client.music.search(search_query=query, limit=limit)
        items = dict_to_search_items(_flatten_search(raw))
        return SearchResponse(query=query, items=items)

    async def browse(self, path: str | None) -> BrowseResponse:
        raw_list = await self.client.music.browse(path=path)
        flat: list[dict] = []
        for entry in raw_list or []:
            if hasattr(entry, "to_dict"):
                flat.append(entry.to_dict())
            elif isinstance(entry, dict):
                flat.append(entry)
            else:
                flat.append({"name": str(entry)})
        return BrowseResponse(path=path, items=dict_to_search_items(flat))


    def bind_listener(self, listener) -> None:
        self._music_services.bind_listener(listener)

    async def list_music_service_types(self) -> list[MusicServiceType]:
        return await self._music_services.list_music_service_types()

    async def catalog_music_services(self) -> MusicServicesCatalog:
        return MusicServicesCatalog(
            services=await self.list_music_service_types(),
            configured=await self.list_music_services(),
        )

    async def list_music_services(self) -> list[MusicService]:
        return await self._music_services.list_music_services()

    async def get_music_service(self, instance_id: str) -> MusicService:
        return await self._music_services.get_music_service(instance_id)

    async def add_music_service(self, body: AddMusicServiceBody) -> AuthSession | MusicService:
        return await self._music_services.add_music_service(body)

    async def update_music_service(
        self, instance_id: str, body: UpdateMusicServiceBody
    ) -> MusicService:
        return await self._music_services.update_music_service(instance_id, body)

    async def remove_music_service(self, instance_id: str) -> None:
        await self._music_services.remove_music_service(instance_id)

    async def start_music_service_auth(self, body: StartAuthBody) -> AuthSession:
        return await self._music_services.start_music_service_auth(body)

    async def get_music_service_auth(self, session_id: str) -> AuthSession:
        return await self._music_services.get_music_service_auth(session_id)

    async def submit_music_service_auth(
        self, session_id: str, body: SubmitAuthBody
    ) -> AuthSession:
        return await self._music_services.submit_music_service_auth(session_id, body)

    async def abort_music_service_auth(self, session_id: str) -> AuthSession:
        return await self._music_services.abort_music_service_auth(session_id)

    async def complete_music_service_auth_callback(
        self, session_id: str, params: dict[str, str]
    ) -> AuthSession:
        return await self._music_services.complete_music_service_auth_callback(session_id, params)

    async def poll_music_service_auth(self) -> list[AuthSession]:
        return await self._music_services.poll_music_service_auth()

    async def spotify_connect_status(self) -> SpotifyConnectStatus:
        return await self._music_services.spotify_connect_status()

    async def set_spotify_connect(self, player_id: str, enabled: bool) -> SpotifyConnectStatus:
        return await self._music_services.set_spotify_connect(player_id, enabled)

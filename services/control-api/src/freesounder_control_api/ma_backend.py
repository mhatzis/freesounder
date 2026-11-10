"""Real Music Assistant backend, wrapping music-assistant-client 1.8.x."""

from __future__ import annotations

import asyncio
import logging

from music_assistant_client import MusicAssistantClient

from freesounder_control_api.backend import _flatten_search, _player_to_room, dict_to_search_items
from freesounder_control_api.models import BrowseResponse, NowPlaying, Room, SearchResponse

logger = logging.getLogger(__name__)


class MusicAssistantBackend:
    """Facade over MusicAssistantClient controllers (players, player_queues, music)."""

    def __init__(self, server_url: str, token: str | None = None) -> None:
        self._server_url = server_url
        self._token = token or None
        self._client: MusicAssistantClient | None = None
        self._listen_task: asyncio.Task[None] | None = None

    @property
    def client(self) -> MusicAssistantClient:
        if self._client is None:
            raise RuntimeError("Music Assistant client is not connected")
        return self._client

    async def connect(self) -> None:
        self._client = MusicAssistantClient(self._server_url, None, token=self._token)
        ready = asyncio.Event()
        self._listen_task = asyncio.create_task(self._client.start_listening(ready))
        try:
            await asyncio.wait_for(ready.wait(), timeout=30)
        except TimeoutError:
            logger.warning("Music Assistant start_listening did not signal ready within 30s")

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
        if self._listen_task is not None:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._listen_task = None
        self._client = None

    async def health_connected(self) -> bool:
        return self._client is not None

    async def list_rooms(self) -> list[Room]:
        players = list(self.client.players.players)
        return [_player_to_room(player) for player in players]

    def snapshot_rooms(self) -> list[Room]:
        if self._client is None:
            return []
        return [_player_to_room(player) for player in self._client.players.players]

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

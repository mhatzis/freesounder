"""Music Assistant port used by the HTTP facade."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

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


class MusicAssistantUnavailable(RuntimeError):
    """Music Assistant is not connected (still starting, pulling, or down).

    state is one of: starting, auth_required, unavailable. detail says why.
    """

    def __init__(self, message: str, state: str = "unavailable", detail: str | None = None):
        super().__init__(message)
        self.state = state
        self.detail = detail or message


@runtime_checkable
class MusicBackend(Protocol):
    """Operations the control API needs from Music Assistant (or a mock)."""

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def health_connected(self) -> bool: ...

    async def list_rooms(self) -> list[Room]: ...

    async def create_group(
        self,
        name: str,
        member_ids: list[str],
        provider: str,
        dynamic: bool | None,
    ) -> Room: ...

    async def join_group(self, group_id: str, player_id: str) -> None: ...

    async def leave_group(self, group_id: str, player_id: str) -> None: ...

    async def now_playing(self, player_id: str) -> NowPlaying: ...

    async def play(self, player_id: str) -> None: ...

    async def pause(self, player_id: str) -> None: ...

    async def next_track(self, player_id: str) -> None: ...

    async def set_volume(self, player_id: str, level: int) -> None: ...

    async def set_group_volume(self, group_id: str, level: int) -> None: ...

    async def search(self, query: str, limit: int = 50) -> SearchResponse: ...

    async def browse(self, path: str | None) -> BrowseResponse: ...

    def snapshot_rooms(self) -> list[Room]:
        """Best-effort cached rooms for WebSocket fan-out."""

        return []

    def bind_listener(self, listener) -> None:
        """Optional hook for backend events (music_services, music_service_auth)."""

        return None

    async def list_music_service_types(self) -> list[MusicServiceType]: ...

    async def list_music_services(self) -> list[MusicService]: ...

    async def get_music_service(self, instance_id: str) -> MusicService: ...

    async def add_music_service(self, body: AddMusicServiceBody) -> AuthSession | MusicService: ...

    async def update_music_service(
        self, instance_id: str, body: UpdateMusicServiceBody
    ) -> MusicService: ...

    async def remove_music_service(self, instance_id: str) -> None: ...

    async def start_music_service_auth(self, body: StartAuthBody) -> AuthSession: ...

    async def get_music_service_auth(self, session_id: str) -> AuthSession: ...

    async def submit_music_service_auth(
        self, session_id: str, body: SubmitAuthBody
    ) -> AuthSession: ...

    async def abort_music_service_auth(self, session_id: str) -> AuthSession: ...

    async def complete_music_service_auth_callback(
        self, session_id: str, params: dict[str, str]
    ) -> AuthSession: ...

    async def catalog_music_services(self) -> MusicServicesCatalog:
        types = await self.list_music_service_types()
        configured = await self.list_music_services()
        return MusicServicesCatalog(services=types, configured=configured)

    async def poll_music_service_auth(self) -> list[AuthSession]:
        """Refresh any in-flight setup flows (no-op when none are pending)."""

        return []

    async def spotify_connect_status(self) -> SpotifyConnectStatus: ...

    async def set_spotify_connect(self, player_id: str, enabled: bool) -> SpotifyConnectStatus: ...


def _player_to_room(player: Any) -> Room:
    """Map a music_assistant_models.player.Player (or similar) to a Room."""

    player_id = getattr(player, "player_id", None) or getattr(player, "id", "")
    name = getattr(player, "display_name", None) or getattr(player, "name", player_id)
    available = bool(getattr(player, "available", True))
    volume = getattr(player, "volume_level", None)
    muted = getattr(player, "volume_muted", None)
    # Sync-group leadership / membership — attribute names vary slightly by schema.
    group_id = (
        getattr(player, "synced_to", None)
        or getattr(player, "active_group", None)
        or getattr(player, "group_id", None)
    )
    members = list(
        getattr(player, "group_members", None) or getattr(player, "group_childs", None) or []
    )
    typ = str(getattr(player, "type", "") or getattr(player, "player_type", "") or "")
    is_group = typ.lower().endswith("group") or typ.lower() == "group" or bool(members)
    if is_group and not group_id:
        group_id = player_id
    state = getattr(getattr(player, "state", None), "value", getattr(player, "state", None))
    if state is not None:
        state = str(state)
    return Room(
        id=str(player_id),
        name=str(name),
        available=available,
        volume=int(volume) if volume is not None else None,
        muted=bool(muted) if muted is not None else None,
        group_id=str(group_id) if group_id else None,
        is_group=is_group,
        member_ids=[str(m) for m in members],
        powered=getattr(player, "powered", None),
        state=state,
    )


def _flatten_search(raw: Any) -> list[dict[str, Any]]:
    """Turn a SearchResults-like object or dict into a list of item dicts."""

    items: list[dict[str, Any]] = []
    if raw is None:
        return items
    if hasattr(raw, "to_dict"):
        raw = raw.to_dict()
    if isinstance(raw, dict):
        for key, value in raw.items():
            if key.startswith("_"):
                continue
            if isinstance(value, list):
                for entry in value:
                    if hasattr(entry, "to_dict"):
                        entry = entry.to_dict()
                    if isinstance(entry, dict):
                        items.append(entry)
                    else:
                        items.append({"name": str(entry)})
    elif isinstance(raw, list):
        for entry in raw:
            if hasattr(entry, "to_dict"):
                entry = entry.to_dict()
            if isinstance(entry, dict):
                items.append(entry)
    return items


def dict_to_search_items(raw_items: list[dict[str, Any]]) -> list:
    from freesounder_control_api.models import SearchItem

    out = []
    for entry in raw_items:
        out.append(
            SearchItem(
                uri=entry.get("uri"),
                name=entry.get("name") or entry.get("title"),
                media_type=str(entry.get("media_type") or entry.get("media_type_value") or "")
                or None,
                raw=entry,
            )
        )
    return out

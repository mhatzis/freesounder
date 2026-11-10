"""In-memory Music Assistant stand-in for tests and UI mock mode."""

from __future__ import annotations

from freesounder_control_api.models import (
    BrowseResponse,
    NowPlaying,
    Room,
    SearchItem,
    SearchResponse,
)


class MockMusicBackend:
    """Two rooms and optional groups, no network required."""

    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {
            "kitchen": Room(
                id="kitchen",
                name="Kitchen",
                available=True,
                volume=40,
                muted=False,
                state="playing",
            ),
            "lounge": Room(
                id="lounge",
                name="Lounge",
                available=True,
                volume=30,
                muted=False,
                state="idle",
            ),
        }
        self.now: dict[str, NowPlaying] = {
            "kitchen": NowPlaying(
                player_id="kitchen",
                title="Example Track",
                artist="Example Artist",
                album="Example Album",
                state="playing",
                position=42,
                duration=240,
            ),
            "lounge": NowPlaying(player_id="lounge", state="idle"),
        }
        self.connected = True
        self._group_seq = 0

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def health_connected(self) -> bool:
        return self.connected

    async def list_rooms(self) -> list[Room]:
        return list(self.rooms.values())

    def snapshot_rooms(self) -> list[Room]:
        return list(self.rooms.values())

    async def create_group(
        self,
        name: str,
        member_ids: list[str],
        provider: str,
        dynamic: bool | None,
    ) -> Room:
        del provider, dynamic
        self._group_seq += 1
        group_id = f"group-{self._group_seq}"
        members = [mid for mid in member_ids if mid in self.rooms]
        group = Room(
            id=group_id,
            name=name,
            available=True,
            volume=40,
            is_group=True,
            member_ids=members,
            group_id=group_id,
            state="idle",
        )
        self.rooms[group_id] = group
        for mid in members:
            room = self.rooms[mid]
            self.rooms[mid] = room.model_copy(update={"group_id": group_id})
        self.now[group_id] = NowPlaying(player_id=group_id, state="idle")
        return group

    async def join_group(self, group_id: str, player_id: str) -> None:
        group = self.rooms[group_id]
        members = list(group.member_ids)
        if player_id not in members:
            members.append(player_id)
        self.rooms[group_id] = group.model_copy(update={"member_ids": members, "is_group": True})
        if player_id in self.rooms:
            self.rooms[player_id] = self.rooms[player_id].model_copy(update={"group_id": group_id})

    async def leave_group(self, group_id: str, player_id: str) -> None:
        if group_id in self.rooms:
            group = self.rooms[group_id]
            members = [m for m in group.member_ids if m != player_id]
            self.rooms[group_id] = group.model_copy(update={"member_ids": members})
        if player_id in self.rooms:
            self.rooms[player_id] = self.rooms[player_id].model_copy(update={"group_id": None})

    async def now_playing(self, player_id: str) -> NowPlaying:
        if player_id in self.now:
            return self.now[player_id]
        return NowPlaying(player_id=player_id, state="idle")

    async def play(self, player_id: str) -> None:
        current = await self.now_playing(player_id)
        self.now[player_id] = current.model_copy(update={"state": "playing"})
        if player_id in self.rooms:
            self.rooms[player_id] = self.rooms[player_id].model_copy(update={"state": "playing"})

    async def pause(self, player_id: str) -> None:
        current = await self.now_playing(player_id)
        self.now[player_id] = current.model_copy(update={"state": "paused"})
        if player_id in self.rooms:
            self.rooms[player_id] = self.rooms[player_id].model_copy(update={"state": "paused"})

    async def next_track(self, player_id: str) -> None:
        current = await self.now_playing(player_id)
        title = current.title or "Example Track"
        self.now[player_id] = current.model_copy(
            update={"title": f"{title} (next)", "state": "playing"}
        )

    async def set_volume(self, player_id: str, level: int) -> None:
        if player_id in self.rooms:
            self.rooms[player_id] = self.rooms[player_id].model_copy(update={"volume": level})

    async def set_group_volume(self, group_id: str, level: int) -> None:
        await self.set_volume(group_id, level)
        group = self.rooms.get(group_id)
        if group:
            for mid in group.member_ids:
                await self.set_volume(mid, level)

    async def search(self, query: str, limit: int = 50) -> SearchResponse:
        del limit
        item = SearchItem(
            uri="library://track/example",
            name=f"Result for {query}",
            media_type="track",
            raw={"uri": "library://track/example", "name": f"Result for {query}"},
        )
        return SearchResponse(query=query, items=[item])

    async def browse(self, path: str | None) -> BrowseResponse:
        item = SearchItem(
            uri="library://album/example",
            name="Example Album",
            media_type="album",
            raw={"path": path, "name": "Example Album"},
        )
        return BrowseResponse(path=path, items=[item])

"""Public JSON models for the control API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Room(BaseModel):
    """A Music Assistant player, presented as a freesounder room."""

    id: str
    name: str
    available: bool = True
    volume: int | None = None
    muted: bool | None = None
    group_id: str | None = None
    is_group: bool = False
    member_ids: list[str] = Field(default_factory=list)
    powered: bool | None = None
    state: str | None = None


class NowPlaying(BaseModel):
    """Current playback for a room or group."""

    player_id: str
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    image_url: str | None = None
    state: str = "idle"
    position: float | None = None
    duration: float | None = None


class SearchItem(BaseModel):
    """A single browse/search result, passed through from Music Assistant."""

    uri: str | None = None
    name: str | None = None
    media_type: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    """Passthrough search results, flattened per media type."""

    query: str
    items: list[SearchItem] = Field(default_factory=list)


class BrowseResponse(BaseModel):
    """Passthrough browse listing."""

    path: str | None = None
    items: list[SearchItem] = Field(default_factory=list)


class VolumeBody(BaseModel):
    """Volume payload, 0–100."""

    level: int = Field(ge=0, le=100)


class JoinLeaveBody(BaseModel):
    """Identify which room should join or leave a group."""

    player_id: str


class CreateGroupBody(BaseModel):
    """Create a Music Assistant group player / sync group."""

    name: str
    member_ids: list[str] = Field(default_factory=list)
    provider: str | None = None
    dynamic: bool | None = True


class Health(BaseModel):
    """Liveness and Music Assistant reachability."""

    status: Literal["ok", "degraded"]
    ma_connected: bool
    version: str
    mock: bool = False


class EventMessage(BaseModel):
    """WebSocket event envelope."""

    type: Literal["event"] = "event"
    event: str
    data: Any = None

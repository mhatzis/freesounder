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
    # "freesounder" = a FreeSounder player (Snapcast client) or a group of them;
    # "other" = AirPlay/Chromecast/etc. speakers Music Assistant found on the LAN.
    # /api/v1/rooms lists only "freesounder"; others are at /api/v1/other-speakers.
    kind: str = "freesounder"
    provider: str | None = None


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
    # connected | starting | auth_required | unavailable
    ma_state: str = "connected"
    ma_detail: str | None = None


class EventMessage(BaseModel):
    """WebSocket event envelope."""

    type: Literal["event"] = "event"
    event: str
    data: Any = None


DacChoice = Literal["none", "dacplus", "digiampplus"]


class PlayerSettings(BaseModel):
    """Per-player name, tone controls, volume cap and DAC."""

    name: str
    volume_limit: int = Field(default=100, ge=1, le=100)
    bass: float = Field(default=0, ge=-10, le=10)
    treble: float = Field(default=0, ge=-10, le=10)
    balance: float = Field(default=0, ge=-10, le=10)
    loudness: bool = False
    dac: DacChoice = "none"
    dac_reboot_required: bool = False


class PlayerSettingsPatch(BaseModel):
    """Partial update for player settings."""

    name: str | None = None
    volume_limit: int | None = Field(default=None, ge=1, le=100)
    bass: float | None = Field(default=None, ge=-10, le=10)
    treble: float | None = Field(default=None, ge=-10, le=10)
    balance: float | None = Field(default=None, ge=-10, le=10)
    loudness: bool | None = None
    dac: DacChoice | None = None


class UnclaimedPlayer(BaseModel):
    """A player agent on the LAN that has not joined this household yet."""

    id: str
    name: str
    address: str
    port: int = 8090
    role: str | None = None
    state: str = "unconfigured"
    url: str


class AdoptBody(BaseModel):
    """Adopt a discovered (or manually addressed) player into this household."""

    name: str = Field(min_length=1)
    id: str | None = None
    url: str | None = None
    # "host" when the app creates a new system on this board (first player);
    # "room" when joining an existing system. None: host for this board's own
    # agent, room for any other player.
    role: Literal["host", "room"] | None = None


class SystemInfo(BaseModel):
    """The household this control API represents."""

    name: str
    version: str
    role: str = "host"
    mock: bool = False
    ma_connected: bool | None = None
    ma_state: str | None = None
    ma_detail: str | None = None
    address: str | None = None


SSH_API_WARNING = (
    "The control API has no authentication. Anyone on this LAN who can reach "
    "the household host can call this endpoint with confirm=true. Only enable "
    "SSH from a device already on the household."
)


class SshDeveloperBody(BaseModel):
    """Enable or disable ephemeral developer SSH on an adopted player."""

    enabled: bool
    confirm: bool = False
    public_key: str | None = None


class SshDeveloperStatus(BaseModel):
    """Developer SSH state for a player. Password is only set when enabling."""

    enabled: bool
    username: str = "developer"
    hostname: str
    port: int = 22
    command: str | None = None
    password: str | None = None
    expires: str = "reboot"
    warning: str = SSH_API_WARNING



class FormOption(BaseModel):
    """A selectable option for a dropdown config field."""

    value: Any
    title: str
    disabled: bool = False
    description: str | None = None


class FormField(BaseModel):
    """A single Music Assistant config entry, ready for a dynamic in-app form."""

    key: str
    type: str
    label: str | None = None
    description: str | None = None
    required: bool = False
    value: Any = None
    has_value: bool = False
    secret: bool = False
    options: list[FormOption] = Field(default_factory=list)
    hidden: bool = False
    advanced: bool = False
    read_only: bool = False
    default: Any = None


class MusicServiceType(BaseModel):
    """A curated music-service kind the app can offer to add."""

    domain: str
    name: str
    category: str
    auth_kind: str
    description: str | None = None
    documentation: str | None = None
    available_on_server: bool = False
    ma_stage: str | None = None
    limited: bool = False
    limitation: str | None = None
    multi_instance: bool = False
    configured: bool = False
    builtin: bool = False


class MusicService(BaseModel):
    """A configured Music Assistant provider instance, with secrets stripped."""

    instance_id: str
    domain: str
    name: str
    type: str | None = None
    status: str
    available: bool = False
    enabled: bool = True
    account: str | None = None
    error: str | None = None
    limited: bool = False
    limitation: str | None = None
    auth_kind: str = "credentials"
    can_reauthenticate: bool = False
    values: dict[str, Any] = Field(default_factory=dict)
    entries: list[FormField] = Field(default_factory=list)


class MusicServicesCatalog(BaseModel):
    """Curated types plus currently configured instances."""

    services: list[MusicServiceType] = Field(default_factory=list)
    configured: list[MusicService] = Field(default_factory=list)


class AddMusicServiceBody(BaseModel):
    """Add a provider. Values are used for credential and path forms."""

    domain: str
    values: dict[str, Any] = Field(default_factory=dict)
    name: str | None = None


class UpdateMusicServiceBody(BaseModel):
    """Update an existing provider's options. Omitted secrets are left unchanged."""

    values: dict[str, Any] = Field(default_factory=dict)
    name: str | None = None
    enabled: bool | None = None


class StartAuthBody(BaseModel):
    """Start a sign-in or re-authentication flow."""

    domain: str | None = None
    instance_id: str | None = None


class SubmitAuthBody(BaseModel):
    """Submit a form step of a running setup or auth flow."""

    values: dict[str, Any] = Field(default_factory=dict)


class AuthSession(BaseModel):
    """In-app-driven Music Assistant setup or OAuth session."""

    session_id: str
    status: Literal[
        "pending",
        "awaiting_form",
        "awaiting_browser",
        "progress",
        "success",
        "error",
        "aborted",
    ]
    domain: str
    instance_id: str | None = None
    flow_id: str | None = None
    auth_url: str | None = None
    callback_scheme: str = "freesounder"
    callback_url: str | None = None
    title: str | None = None
    description: str | None = None
    entries: list[FormField] = Field(default_factory=list)
    errors: dict[str, str] = Field(default_factory=dict)
    last_step: bool | None = None
    copy_text: str | None = None
    progress_text: str | None = None
    image: str | None = None
    error: str | None = None


class SpotifyConnectRoom(BaseModel):
    """Whether a room is advertised as a Spotify Connect speaker."""

    player_id: str
    name: str
    enabled: bool


class SpotifyConnectStatus(BaseModel):
    """Spotify Connect plugin state, including the go-librespot or Soloist engine."""

    available: bool
    configured: bool = False
    instance_id: str | None = None
    engine: str | None = None
    engine_label: str | None = None
    status: str | None = None
    rooms: list[SpotifyConnectRoom] = Field(default_factory=list)
    note: str | None = None


class SpotifyConnectToggleBody(BaseModel):
    """Enable or disable Spotify Connect for one room."""

    enabled: bool

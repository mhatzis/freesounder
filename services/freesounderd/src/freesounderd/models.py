"""JSON models for the freesounderd v2 API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

DacChoice = Literal["auto", "none", "dacplus", "digiampplus", "hifiberry-dac", "iqaudio-dacplus"]
DAC_CHOICES: tuple[str, ...] = (
    "auto",
    "none",
    "dacplus",
    "digiampplus",
    "hifiberry-dac",
    "iqaudio-dacplus",
)
PlayerState = Literal["unclaimed", "ready"]
SourceName = Literal["none", "radio", "spotify", "airplay"]


class PlayerSettings(BaseModel):
    """Per-room playback and hardware settings (applied on this player only)."""

    name: str = "FreeSounder"
    volume_limit: int = Field(default=100, ge=1, le=100)
    bass: float = Field(default=0, ge=-10, le=10)
    treble: float = Field(default=0, ge=-10, le=10)
    balance: float = Field(default=0, ge=-10, le=10)
    loudness: bool = False
    dac: DacChoice = "auto"
    dac_reboot_required: bool = False


class PlayerSettingsPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    volume_limit: int | None = Field(default=None, ge=1, le=100)
    bass: float | None = Field(default=None, ge=-10, le=10)
    treble: float | None = Field(default=None, ge=-10, le=10)
    balance: float | None = Field(default=None, ge=-10, le=10)
    loudness: bool | None = None
    dac: DacChoice | None = None


class HouseholdSecret(BaseModel):
    """Household identity. The key never leaves players (peer-to-peer only)."""

    id: str
    key: str
    name: str = "Home"


class SetupBody(BaseModel):
    """First setup of an unclaimed player: the room name, optionally a household.

    With no household the player creates a new one. A player that is already
    part of a household passes it when it adopts a new player.
    """

    room: str = Field(min_length=1, max_length=40)
    household: HouseholdSecret | None = None


class AdoptBody(BaseModel):
    """Ask this (set up) player to set up a new player into its household."""

    url: str | None = None
    ip: str | None = None
    room: str = Field(min_length=1, max_length=40)


class VolumeBody(BaseModel):
    volume: int | None = Field(default=None, ge=0, le=100)
    muted: bool | None = None


class GroupMembersBody(BaseModel):
    members: list[str]


class JoinBody(BaseModel):
    """Join a group: the target is a group id or any room id in that group."""

    group: str | None = None
    room: str | None = None


class RadioPlayBody(BaseModel):
    stationuuid: str | None = None
    url: str | None = None
    name: str | None = None
    favicon: str | None = None


class RadioStation(BaseModel):
    stationuuid: str
    name: str
    url: str | None = None
    url_resolved: str | None = None
    favicon: str | None = None
    country: str | None = None
    countrycode: str | None = None
    tags: str | None = None
    codec: str | None = None
    bitrate: int | None = None
    votes: int | None = None


class NowPlaying(BaseModel):
    """Matches the app's NowPlaying model (plus source fields)."""

    player_id: str
    group_id: str
    source: SourceName = "none"
    state: Literal["idle", "playing", "paused"] = "idle"
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    image_url: str | None = None
    position: float | None = None
    duration: float | None = None
    station: dict[str, Any] | None = None


class Room(BaseModel):
    """One player as seen by the app (matches the app's Room model)."""

    id: str
    name: str
    available: bool = True
    volume: int = 50
    muted: bool = False
    group_id: str
    is_group: bool = False
    member_ids: list[str] = []
    state: str = "idle"
    leader_id: str
    model: str | None = None
    ip: str | None = None
    port: int = 8080
    url: str | None = None
    hostname: str | None = None
    version: str | None = None
    source: SourceName = "none"


class Group(BaseModel):
    id: str
    name: str
    leader_id: str
    member_ids: list[str]
    volume: int
    muted: bool = False
    source: SourceName = "none"
    state: str = "idle"


class Health(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    state: PlayerState
    id: str
    api: str = "v2"


class SshDeveloperBody(BaseModel):
    enabled: bool
    confirm: bool = False
    public_key: str | None = None


SSH_API_WARNING = (
    "The player API has no authentication on the LAN in this alpha. Anyone on "
    "this network can call this endpoint with confirm=true."
)


class SshDeveloperStatus(BaseModel):
    enabled: bool
    username: str = "developer"
    hostname: str
    port: int = 22
    command: str | None = None
    password: str | None = None
    expires: str = "reboot"
    warning: str = SSH_API_WARNING

"""JSON models for the per-player agent."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DacChoice = Literal["none", "dacplus", "digiampplus"]
PlayerState = Literal["unconfigured", "configured"]
RoleChoice = Literal["host", "room", "auto"]


class PlayerSettings(BaseModel):
    """Per-player playback and hardware settings."""

    name: str = "FreeSounder"
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


class AdoptBody(BaseModel):
    """Join this player to a household host."""

    name: str = Field(min_length=1)
    host: str = Field(min_length=1)
    role: RoleChoice = "room"


class AgentInfo(BaseModel):
    """Identity advertised to the household host and shown in Add player."""

    id: str
    name: str
    role: str
    effective_role: str
    state: PlayerState
    host: str | None = None
    dac: DacChoice = "none"
    hostname: str
    url: str | None = None


class Health(BaseModel):
    """Liveness for the player agent."""

    status: Literal["ok"] = "ok"
    version: str
    state: PlayerState
    id: str


SSH_API_WARNING = (
    "The control API has no authentication. Anyone on this LAN who can reach "
    "the household host can call this endpoint with confirm=true."
)


class SshDeveloperBody(BaseModel):
    """Enable or disable ephemeral developer SSH on this player."""

    enabled: bool
    confirm: bool = False
    public_key: str | None = None


class SshDeveloperStatus(BaseModel):
    """Current developer SSH state. The password is only returned on enable."""

    enabled: bool
    username: str = "developer"
    hostname: str
    port: int = 22
    command: str | None = None
    password: str | None = None
    expires: str = "reboot"
    warning: str = SSH_API_WARNING

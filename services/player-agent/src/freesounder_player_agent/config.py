"""Process configuration for the player agent."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    """Runtime settings, overridable in tests via env or constructor."""

    model_config = SettingsConfigDict(env_prefix="FREESOUNDER_", extra="ignore")

    host: str = "0.0.0.0"
    agent_port: int = 8090
    mdns_enabled: bool = True
    config_dir: Path = Path("/etc/freesounder")
    state_dir: Path = Path("/var/lib/freesounder")
    alsa_conf: Path = Path("/etc/alsa/conf.d/99-freesounder.conf")
    camilla_conf: Path = Path("/var/lib/freesounder/camilladsp.yml")
    apply_command: str | None = Field(
        default="/usr/lib/freesounder/apply-player-agent",
        description="Hook run after adopt or settings changes. Empty to skip.",
    )


def load_settings() -> AgentSettings:
    """Load settings from the environment."""

    return AgentSettings()

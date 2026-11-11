"""Process configuration, loaded from environment variables."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the control API."""

    model_config = SettingsConfigDict(env_prefix="FREESOUNDER_", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8080
    ma_url: str = "http://127.0.0.1:8095"
    ma_token: str | None = None
    # Host only: where the auto-created Music Assistant token/admin live (root, 0600).
    ma_token_file: Path = Path("/etc/freesounder/ma-token")
    ma_admin_file: Path = Path("/etc/freesounder/ma-admin.json")
    ma_auto_setup: bool = True
    ma_status_file: Path = Path("/run/freesounder/ma-status.json")
    mdns_enabled: bool = True
    mdns_name: str = "freesounder"
    mock_ma: bool = False
    group_provider: str = Field(
        default="snapcast",
        description="Music Assistant provider domain used when creating a group player.",
    )


def load_settings() -> Settings:
    """Load settings from the environment."""

    return Settings()

"""Process configuration, loaded from environment variables."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the control API."""

    model_config = SettingsConfigDict(env_prefix="FREESOUNDER_", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8080
    ma_url: str = "http://127.0.0.1:8095"
    ma_token: str | None = None
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

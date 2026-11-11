"""Process configuration for freesounderd."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DaemonSettings(BaseSettings):
    """Runtime settings, overridable in tests via env or constructor."""

    model_config = SettingsConfigDict(env_prefix="FREESOUNDER_", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8080
    advertise_ip: str | None = None
    mdns_enabled: bool = True
    # When false (tests, dev laptops) no systemd units or processes are touched.
    manage_system: bool = True
    config_dir: Path = Path("/etc/freesounder")
    state_dir: Path = Path("/var/lib/freesounder")
    run_dir: Path = Path("/run/freesounder")
    alsa_conf: Path = Path("/etc/alsa/conf.d/99-freesounder.conf")
    camilla_conf: Path = Path("/var/lib/freesounder/camilladsp.yml")
    snapserver_conf: Path = Path("/etc/snapserver.conf")
    snapclient_env: Path = Path("/run/freesounder/snapclient.env")
    spotify_dir: Path = Path("/var/lib/freesounder/spotify")
    shairport_bin: str = "/usr/bin/shairport-sync"
    airplay_version: int = 0  # 0: read /etc/freesounder/features.json
    librespot_api: str = "http://127.0.0.1:3678"
    snapserver_rpc: str = "http://127.0.0.1:1780/jsonrpc"
    ffmpeg_bin: str = "ffmpeg"
    radio_browser_base: str | None = None
    apply_command: str | None = Field(
        default="/usr/lib/freesounder/apply-player-settings",
        description="Hook run after room name / DSP / DAC changes. Empty to skip.",
    )
    ssh_command: str | None = "/usr/lib/freesounder/ssh-developer"
    poll_interval: float = 2.0
    leader_timeout: float = 6.0


def load_settings() -> DaemonSettings:
    return DaemonSettings()

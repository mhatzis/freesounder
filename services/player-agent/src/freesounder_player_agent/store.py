"""Filesystem-backed player identity and settings."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import uuid
from pathlib import Path

from freesounder_player_agent.config import AgentSettings
from freesounder_player_agent.dsp import write_dsp_configs
from freesounder_player_agent.models import (
    AdoptBody,
    AgentInfo,
    PlayerSettings,
    PlayerSettingsPatch,
    PlayerState,
)

logger = logging.getLogger(__name__)

_HOSTNAME_RE = re.compile(r"[^a-z0-9]+")


def hostname_friendly(name: str) -> str:
    """Turn a room name into a DNS-safe hostname label."""

    label = _HOSTNAME_RE.sub("-", name.strip().lower()).strip("-")
    return (label[:63] or "freesounder")


def _read_text(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


class PlayerStore:
    """Read and write /etc/freesounder and /var/lib/freesounder."""

    def __init__(self, settings: AgentSettings) -> None:
        self._settings = settings
        self._settings.config_dir.mkdir(parents=True, exist_ok=True)
        self._settings.state_dir.mkdir(parents=True, exist_ok=True)

    @property
    def player_id(self) -> str:
        path = self._settings.config_dir / "player-id"
        existing = _read_text(path)
        if existing:
            return existing
        value = str(uuid.uuid4())
        path.write_text(value + "\n", encoding="utf-8")
        return value

    def role(self) -> str:
        return _read_text(self._settings.config_dir / "role") or "auto"

    def effective_role(self) -> str:
        configured = self.role()
        if configured in {"host", "room"}:
            return configured
        # Tests and non-Pi hosts: treat auto as room unless RAM looks host-capable.
        mem_kb = _mem_total_kb()
        if mem_kb >= 3670016:
            return "host"
        return "room"

    def household_host(self) -> str | None:
        return _read_text(self._settings.config_dir / "host")

    def state(self) -> PlayerState:
        if (self._settings.config_dir / "configured").is_file():
            return "configured"
        if self.effective_role() == "host":
            return "configured"
        return "unconfigured"

    def mark_configured(self) -> None:
        (self._settings.config_dir / "configured").write_text("1\n", encoding="utf-8")

    def settings_path(self) -> Path:
        return self._settings.state_dir / "settings.json"

    def load_settings(self) -> PlayerSettings:
        path = self.settings_path()
        name = _read_text(self._settings.config_dir / "name") or _default_name()
        dac = _read_text(self._settings.config_dir / "dac") or "none"
        if dac not in {"none", "dacplus", "digiampplus"}:
            dac = "none"
        base = PlayerSettings(name=name, dac=dac)  # type: ignore[arg-type]
        if not path.is_file():
            return base
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            merged = {
                **base.model_dump(),
                **raw,
                "name": raw.get("name") or name,
                "dac": raw.get("dac") or dac,
            }
            return PlayerSettings.model_validate(merged)
        except (OSError, json.JSONDecodeError, ValueError):
            logger.warning("Ignoring invalid settings at %s", path, exc_info=True)
            return base

    def save_settings(self, settings: PlayerSettings) -> None:
        path = self.settings_path()
        payload = settings.model_dump()
        payload.pop("dac_reboot_required", None)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        (self._settings.config_dir / "name").write_text(settings.name + "\n", encoding="utf-8")
        (self._settings.config_dir / "dac").write_text(settings.dac + "\n", encoding="utf-8")
        write_dsp_configs(
            settings,
            alsa_path=self._settings.alsa_conf,
            camilla_path=self._settings.camilla_conf,
            output_file=self._settings.config_dir / "audio-output",
        )

    def apply(
        self,
        settings: PlayerSettings,
        *,
        dac_changed: bool = False,
        run_hook: bool = True,
    ) -> None:
        self.save_settings(settings)
        if run_hook:
            self.run_hook(dac_changed=dac_changed)

    def run_hook(self, *, dac_changed: bool = False) -> None:
        """Run the apply hook (hostname, runtime config, service restarts)."""

        command = self._settings.apply_command
        if not command:
            return
        env = os.environ.copy()
        env["FREESOUNDER_DAC_CHANGED"] = "1" if dac_changed else "0"
        try:
            subprocess.run(command, shell=True, check=False, env=env, timeout=60)
        except (OSError, subprocess.SubprocessError):
            logger.warning("Apply hook failed: %s", command, exc_info=True)

    def adopt(self, body: AdoptBody, *, run_hook: bool = True) -> AgentInfo:
        """Record the household and role. With run_hook=False the caller runs
        run_hook() after replying: the hook renames the host and restarts the
        control API, which may be the very process waiting on this reply."""

        (self._settings.config_dir / "host").write_text(body.host + "\n", encoding="utf-8")
        (self._settings.config_dir / "role").write_text(body.role + "\n", encoding="utf-8")
        current = self.load_settings()
        updated = current.model_copy(update={"name": body.name})
        self.mark_configured()
        self.apply(updated, run_hook=run_hook)
        return self.info()

    def patch_settings(
        self, patch: PlayerSettingsPatch, *, run_hook: bool = True
    ) -> PlayerSettings:
        current = self.load_settings()
        updates = patch.model_dump(exclude_none=True)
        dac_changed = "dac" in updates and updates["dac"] != current.dac
        updated = current.model_copy(update=updates)
        if dac_changed:
            updated = updated.model_copy(update={"dac_reboot_required": True})
        self.mark_configured()
        self.apply(updated, dac_changed=dac_changed, run_hook=run_hook)
        return updated

    def info(self) -> AgentInfo:
        settings = self.load_settings()
        return AgentInfo(
            id=self.player_id,
            name=settings.name,
            role=self.role(),
            effective_role=self.effective_role(),
            state=self.state(),
            host=self.household_host(),
            dac=settings.dac,
            hostname=os.uname().nodename,
        )


def _default_name() -> str:
    return os.uname().nodename or "FreeSounder"


def _mem_total_kb() -> int:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1])
    except (OSError, ValueError):
        return 0
    return 0

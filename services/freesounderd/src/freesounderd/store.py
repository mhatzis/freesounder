"""Filesystem-backed identity, household, room settings and group state."""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import subprocess
import uuid
from pathlib import Path
from typing import Any

from freesounderd.config import DaemonSettings
from freesounderd.dsp import write_dsp_configs
from freesounderd.models import (
    DAC_CHOICES,
    HouseholdSecret,
    PlayerSettings,
    PlayerSettingsPatch,
)

logger = logging.getLogger(__name__)


def _read_text(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def _write_atomic(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


class Store:
    """Read and write /etc/freesounder and /var/lib/freesounder."""

    def __init__(self, settings: DaemonSettings) -> None:
        self.settings = settings
        settings.config_dir.mkdir(parents=True, exist_ok=True)
        settings.state_dir.mkdir(parents=True, exist_ok=True)

    # identity -----------------------------------------------------------
    @property
    def player_id(self) -> str:
        path = self.settings.config_dir / "player-id"
        existing = _read_text(path)
        if existing:
            return existing
        value = str(uuid.uuid4())
        _write_atomic(path, value + "\n")
        return value

    # household ----------------------------------------------------------
    @property
    def household_path(self) -> Path:
        return self.settings.state_dir / "household.json"

    def household(self) -> HouseholdSecret | None:
        try:
            raw = json.loads(self.household_path.read_text(encoding="utf-8"))
            return HouseholdSecret.model_validate(raw)
        except (OSError, ValueError):
            return None

    def save_household(self, household: HouseholdSecret) -> None:
        _write_atomic(self.household_path, household.model_dump_json() + "\n", 0o600)

    @staticmethod
    def new_household(name: str = "Home") -> HouseholdSecret:
        return HouseholdSecret(id=str(uuid.uuid4()), key=secrets.token_hex(32), name=name)

    def clear_household(self) -> None:
        self.household_path.unlink(missing_ok=True)
        self.group_path.unlink(missing_ok=True)
        (self.settings.config_dir / "configured").unlink(missing_ok=True)

    def state(self) -> str:
        return "ready" if self.household() is not None else "unclaimed"

    # group / room runtime state ----------------------------------------
    @property
    def group_path(self) -> Path:
        return self.settings.state_dir / "group.json"

    def load_group(self) -> dict[str, Any]:
        base: dict[str, Any] = {"follow": None, "term": 0, "volume": 40, "muted": False}
        try:
            base.update(json.loads(self.group_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        return base

    def save_group(self, data: dict[str, Any]) -> None:
        _write_atomic(self.group_path, json.dumps(data, indent=2) + "\n")

    def radio_favourites(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(
                (self.settings.state_dir / "radio-favourites.json").read_text(encoding="utf-8")
            )
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def save_radio_favourites(self, items: list[dict[str, Any]]) -> None:
        _write_atomic(
            self.settings.state_dir / "radio-favourites.json", json.dumps(items, indent=2) + "\n"
        )

    # settings -----------------------------------------------------------
    def settings_path(self) -> Path:
        return self.settings.state_dir / "settings.json"

    def load_settings(self) -> PlayerSettings:
        name = _read_text(self.settings.config_dir / "name") or "FreeSounder"
        dac = _read_text(self.settings.config_dir / "dac") or "auto"
        if dac not in DAC_CHOICES:
            dac = "auto"
        base = PlayerSettings(name=name, dac=dac)  # type: ignore[arg-type]
        path = self.settings_path()
        if not path.is_file():
            return base
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw.pop("name", None)
            raw.pop("dac", None)
            return PlayerSettings.model_validate({**base.model_dump(), **raw})
        except (OSError, ValueError):
            logger.warning("Ignoring invalid settings at %s", path, exc_info=True)
            return base

    def save_settings(self, settings: PlayerSettings) -> None:
        payload = settings.model_dump()
        payload.pop("dac_reboot_required", None)
        _write_atomic(self.settings_path(), json.dumps(payload, indent=2) + "\n")
        _write_atomic(self.settings.config_dir / "name", settings.name + "\n")
        _write_atomic(self.settings.config_dir / "dac", settings.dac + "\n")
        # Tone settings go live through the fsdsp params file; the ALSA
        # config only changes (and snapclient only re-opens) if it must.
        self.last_alsa_changed = False
        try:
            self.last_alsa_changed = write_dsp_configs(
                settings,
                alsa_path=self.settings.alsa_conf,
                camilla_path=self.settings.camilla_conf,
                output_file=self.settings.config_dir / "audio-output",
                params_path=self.settings.run_dir / "dsp-params",
                status_path=self.settings.run_dir / "audio-chain.json",
            )
        except OSError:
            logger.warning("Could not write DSP configs", exc_info=True)

    def patch_settings(self, patch: PlayerSettingsPatch) -> tuple[PlayerSettings, bool, bool]:
        """Apply a patch; returns (settings, name_changed, dac_changed)."""

        current = self.load_settings()
        updates = patch.model_dump(exclude_none=True)
        if "name" in updates:
            updates["name"] = updates["name"].strip()
        name_changed = "name" in updates and updates["name"] != current.name
        dac_changed = "dac" in updates and updates["dac"] != current.dac
        updated = current.model_copy(update=updates)
        if dac_changed:
            updated = updated.model_copy(update={"dac_reboot_required": True})
        self.save_settings(updated)
        return updated, name_changed, dac_changed

    def run_hook(self, *, dac_changed: bool = False) -> None:
        command = self.settings.apply_command
        if not command or not self.settings.manage_system:
            return
        env = os.environ.copy()
        env["FREESOUNDER_DAC_CHANGED"] = "1" if dac_changed else "0"
        try:
            subprocess.run(command, shell=True, check=False, env=env, timeout=60)
        except (OSError, subprocess.SubprocessError):
            logger.warning("Apply hook failed: %s", command, exc_info=True)

    def features(self) -> dict[str, Any]:
        try:
            data = json.loads((self.settings.config_dir / "features.json").read_text("utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}


def hostname() -> str:
    return os.uname().nodename


def board_model() -> str:
    raw = _read_text(Path("/proc/device-tree/model")) or ""
    return raw.replace("\x00", "").strip() or "unknown"


def detected_hat() -> str | None:
    """Product string of an EEPROM HAT (the firmware loads its overlay itself)."""

    raw = _read_text(Path("/proc/device-tree/hat/product"))
    return raw.replace("\x00", "").strip() if raw else None


_HOSTNAME_RE = re.compile(r"[^a-z0-9]+")


def hostname_friendly(name: str) -> str:
    """Turn a room name into a DNS-safe hostname label."""

    label = _HOSTNAME_RE.sub("-", name.strip().lower()).strip("-")
    return label[:63] or "freesounder"

"""Local device identity: MAC suffix, player id, hardware model."""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

from freesounder_ble_provisioning import __version__
from freesounder_ble_provisioning.protocol import DeviceIdentity


def mac_suffix(mac: str | None = None) -> str:
    """Last four hex digits of a MAC, upper-case, or '0000'."""

    raw = (mac or _first_mac() or "0000").replace(":", "").replace("-", "")
    digits = re.sub(r"[^0-9A-Fa-f]", "", raw)
    if len(digits) < 4:
        return "0000"
    return digits[-4:].upper()


def advertise_name(mac: str | None = None) -> str:
    return f"FreeSounder-{mac_suffix(mac)}"


def hardware_label() -> str:
    try:
        model = Path("/proc/device-tree/model").read_text(encoding="utf-8", errors="replace")
        model = model.replace("\x00", "").strip()
    except OSError:
        model = ""
    if "Raspberry Pi 5" in model:
        return "raspberry-pi-5"
    if "Raspberry Pi 4" in model:
        return "raspberry-pi-4"
    if "Raspberry Pi" in model:
        return "raspberry-pi"
    return model or "raspberry-pi"


def player_id(config_dir: Path | None = None) -> str:
    directory = config_dir or Path("/etc/freesounder")
    path = directory / "player-id"
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        existing = ""
    value = str(uuid.uuid4())
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text(value + "\n", encoding="utf-8")
    except OSError:
        pass
    return value


def load_identity(config_dir: Path | None = None) -> DeviceIdentity:
    directory = config_dir or Path("/etc/freesounder")
    name = _read(directory / "name") or advertise_name()
    host = os.uname().nodename if hasattr(os, "uname") else "freesounder"
    return DeviceIdentity(
        firmware_name="FreeSounder",
        firmware_version=__version__,
        hardware=hardware_label(),
        device_name=name,
        player_id=player_id(directory),
        hostname=host,
    )


def persist_name(name: str, config_dir: Path | None = None) -> None:
    directory = config_dir or Path("/etc/freesounder")
    try:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "name").write_text(name + "\n", encoding="utf-8")
    except OSError:
        pass


def persist_hostname(hostname: str) -> None:
    """Set the pretty hostname and the static name to fs-<room> when possible."""
    try:
        import subprocess

        helper = Path("/usr/lib/freesounder/apply-hostname")
        if helper.is_file():
            subprocess.run(
                [str(helper), hostname],
                check=False,
                timeout=10,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        subprocess.run(
            ["hostnamectl", "set-hostname", "--pretty", hostname],
            check=False,
            timeout=10,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _read(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def _first_mac() -> str | None:
    sys = Path("/sys/class/net")
    if not sys.is_dir():
        return None
    preferred = []
    others = []
    for dev in sys.iterdir():
        if dev.name == "lo":
            continue
        addr = dev / "address"
        try:
            mac = addr.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not mac or mac == "00:00:00:00:00:00":
            continue
        if dev.name.startswith(("eth", "en", "wlan", "wl")):
            preferred.append(mac)
        else:
            others.append(mac)
    return (preferred or others or [None])[0]

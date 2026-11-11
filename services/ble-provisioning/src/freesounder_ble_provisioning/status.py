"""Runtime BLE status for the tty1 screen and the bootfs doctor file."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

STATUS_PATH = Path("/run/freesounder/ble-status.json")
BOOTFS_DOCTOR = "freesounder-doctor.txt"


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_status(update: dict[str, Any], path: Path = STATUS_PATH) -> None:
    current: dict[str, Any] = {}
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(current, dict):
            current = {}
    except (OSError, json.JSONDecodeError):
        current = {}
    current.update(update)
    current["updated"] = now_iso()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(current, indent=2, sort_keys=True) + "\n"
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".ble-status.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(data)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError:
        pass


def try_write_bootfs_doctor(text: str, filename: str = BOOTFS_DOCTOR) -> str | None:
    """Write a doctor report onto the boot partition so it can be read on a PC."""

    for candidate in ("/boot/firmware", "/boot"):
        dest_dir = Path(candidate)
        if not dest_dir.is_dir():
            continue
        if not os.access(dest_dir, os.W_OK):
            try:
                import subprocess

                subprocess.run(
                    ["mount", "-o", "remount,rw", candidate],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
            except (OSError, subprocess.SubprocessError):
                pass
        dest = dest_dir / filename
        try:
            dest.write_text(text, encoding="utf-8")
            dest.chmod(0o644)
            return str(dest)
        except OSError:
            continue
    return None


def read_live_adapter(
    *,
    sys_class_bluetooth: Path = Path("/sys/class/bluetooth"),
    run=None,
) -> dict[str, Any]:
    """Adapter address and Powered state from sysfs and BlueZ D-Bus.

    Used by the tty1 status screen even when RegisterAdvertisement failed
    before the service could cache properties.
    """

    info: dict[str, Any] = {"address": "", "powered": None, "adapter": ""}
    hci0 = sys_class_bluetooth / "hci0"
    if hci0.is_dir():
        info["adapter"] = "hci0"
        try:
            info["address"] = (hci0 / "address").read_text(encoding="utf-8").strip()
        except OSError:
            pass
        # Some kernels export a 'name' and flags; Powered is a BlueZ property.
        flags = _read_optional(hci0 / "flags")
        if flags and "UP" in flags.split():
            info["powered"] = True
        elif flags and "DOWN" in flags.split():
            info["powered"] = False

    powered = _busctl_adapter_property("Powered", run=run)
    if powered is not None:
        info["powered"] = powered
    address = _busctl_adapter_property("Address", run=run)
    if isinstance(address, str) and address:
        info["address"] = address
    return info


def _read_optional(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _busctl_adapter_property(name: str, *, run=None, adapter: str = "/org/bluez/hci0"):
    import subprocess

    argv = [
        "busctl",
        "--system",
        "get-property",
        "org.bluez",
        adapter,
        "org.bluez.Adapter1",
        name,
    ]
    try:
        if run is not None:
            completed = run(argv)
            text = completed
        else:
            proc = subprocess.run(
                argv,
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            )
            text = (proc.stdout or "").strip()
            if proc.returncode != 0:
                return None
    except (OSError, subprocess.SubprocessError):
        return None
    return _parse_busctl_variant(text)


def _parse_busctl_variant(text: str):
    """Parse `busctl get-property` output such as 'b true' or 's \"AA:BB\"'."""

    raw = (text or "").strip()
    if not raw:
        return None
    # When tests inject a value they may pass the already-decoded form.
    if raw in {"true", "yes", "on"}:
        return True
    if raw in {"false", "no", "off"}:
        return False
    parts = raw.split(None, 1)
    if len(parts) < 2:
        return None
    typ, value = parts[0], parts[1].strip()
    if typ == "b":
        return value.lower() in {"true", "t", "1", "yes"}
    if typ == "s":
        return value.strip('"')
    return value.strip('"')

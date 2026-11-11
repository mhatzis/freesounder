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

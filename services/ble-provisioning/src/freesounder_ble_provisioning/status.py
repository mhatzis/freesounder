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


WIFI_STATUS_PATH = Path("/run/freesounder/wifi-status.json")


def read_rfkill_wlan(sys_class_rfkill: Path = Path("/sys/class/rfkill")) -> str:
    """'unblocked', 'soft-blocked', 'hard-blocked' or 'no wlan rfkill'."""

    states: list[str] = []
    try:
        entries = sorted(sys_class_rfkill.iterdir())
    except OSError:
        entries = []
    for entry in entries:
        if _read_optional(entry / "type") != "wlan":
            continue
        soft = _read_optional(entry / "soft") == "1"
        hard = _read_optional(entry / "hard") == "1"
        if hard:
            states.append("hard-blocked")
        elif soft:
            states.append("soft-blocked")
        else:
            states.append("unblocked")
    if not states:
        return "no wlan rfkill"
    return ", ".join(sorted(set(states)))


def parse_iw_reg(text: str) -> str:
    """First 'country XX:' from `iw reg get` (global section)."""

    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("country "):
            return line.split()[1].rstrip(":")
    return ""


def parse_nm_device_state(text: str, ifname: str = "wlan0") -> str:
    """State of [ifname] from `nmcli -t -f DEVICE,TYPE,STATE device`."""

    for line in (text or "").splitlines():
        parts = line.split(":")
        if len(parts) >= 3 and parts[0] == ifname:
            return parts[2]
    return ""


def read_wifi_live(
    *, run=None, sys_class_rfkill: Path = Path("/sys/class/rfkill")
) -> dict[str, Any]:
    """Live Wi-Fi radio facts for the status screen and doctor (no secrets)."""

    import subprocess

    def cmd(argv: list[str]) -> str:
        if run is not None:
            return run(argv)
        try:
            proc = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=4)
        except (OSError, subprocess.SubprocessError):
            return ""
        return proc.stdout or ""

    wlan0 = Path("/sys/class/net/wlan0")
    return {
        "wlan0": "present" if wlan0.exists() or run is not None else "missing",
        "rfkill_wlan": read_rfkill_wlan(sys_class_rfkill),
        "country": parse_iw_reg(cmd(["iw", "reg", "get"])) or "unset",
        "nm_state": parse_nm_device_state(cmd(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"]))
        or "not listed",
        "nm_radio": (cmd(["nmcli", "radio", "wifi"]).strip() or "unknown"),
    }


def wifi_status_lines(live: dict[str, Any], saved: dict[str, Any]) -> list[str]:
    """Lines shared by the tty1 screen and the doctor report."""

    lines = [
        f"  wlan0       : {live.get('wlan0', '?')}  NM={live.get('nm_state', '?')} "
        f"radio={live.get('nm_radio', '?')}",
        f"  rfkill wlan : {live.get('rfkill_wlan', '?')}",
        f"  country     : {live.get('country', '?')}"
        + (f" (saved {saved['country']})" if saved.get("country") else ""),
    ]
    if "scan_count" in saved:
        count = saved.get("scan_count")
        lines.append(f"  last scan   : {count} networks ({saved.get('scan_at', '')})")
    attempt = saved.get("last_attempt")
    if isinstance(attempt, dict):
        ssid = str(attempt.get("ssid", ""))[:32]
        if attempt.get("ok"):
            outcome = f"joined, {attempt.get('ip') or 'no address'}"
        else:
            outcome = str(attempt.get("reason") or "failed")
            if attempt.get("detail"):
                outcome += f": {str(attempt['detail'])[:70]}"
        lines.append(f"  last join   : '{ssid}' {outcome} ({attempt.get('at', '')})")
    else:
        lines.append("  last join   : no attempt yet")
    return lines

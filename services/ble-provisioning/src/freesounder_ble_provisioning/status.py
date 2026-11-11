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


# --- Household services: control API, player agent, Music Assistant ---------

MA_STATUS_PATH = Path("/run/freesounder/ma-status.json")
CONTROL_API_HEALTH = "http://127.0.0.1:8080/health"
AGENT_HEALTH = "http://127.0.0.1:8090/health"


def _http_json(url: str, timeout: float = 1.5) -> dict[str, Any] | None:
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _run_text(argv: list[str], timeout: float = 4.0) -> str:
    import subprocess

    try:
        proc = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (proc.stdout or "").strip()


SNAPCLIENT_DEFAULTS = Path("/etc/default/snapclient")
AUDIO_OUTPUT_PATH = Path("/etc/freesounder/audio-output")


def _snap_host(text: str) -> str:
    import shlex

    for line in text.splitlines():
        if line.startswith("SNAPCLIENT_OPTS="):
            try:
                args = " ".join(shlex.split(line.partition("=")[2])).split()
            except ValueError:
                return "127.0.0.1"
            for flag in ("--host", "-h"):
                if flag in args and args.index(flag) + 1 < len(args):
                    return args[args.index(flag) + 1]
    return "127.0.0.1"


def _audio_output(text: str) -> str:
    values = dict(line.partition("=")[::2] for line in text.splitlines() if "=" in line)
    pcm = values.get("pcm", "").strip()
    if not pcm:
        return "not selected yet (ALSA default)"
    reason = values.get("reason", "").strip()
    return f"{pcm} ({reason})" if reason else pcm


def read_services_live(run=_run_text, http=_http_json, port_open=_port_open) -> dict[str, Any]:
    """Snapshot of the three household services (no secrets)."""

    def unit(name: str) -> str:
        return run(["systemctl", "is-active", name]) or "unknown"

    try:
        ma_status = json.loads(MA_STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ma_status = {}
    fmt = "{{.State.Status}} since {{.State.StartedAt}}"
    container = run(["docker", "inspect", "-f", fmt, "music-assistant"])
    preload = sorted(str(p) for p in Path("/var/lib/freesounder/ma-image").glob("*.tar"))
    snap_host = _snap_host(_read_optional(SNAPCLIENT_DEFAULTS))
    journal = run(
        ["journalctl", "-u", "snapclient.service", "-b", "-n", "40", "--no-pager", "-o", "cat"]
    )
    snap_errors = [
        line
        for line in journal.splitlines()
        if any(word in line.lower() for word in ("error", "failed", "exception", "cannot"))
    ]
    return {
        "snapclient_unit": unit("snapclient.service"),
        "snap_host": snap_host,
        "snapserver_stream": port_open(1704, snap_host),
        "snapserver_control": port_open(1705, snap_host),
        "snapclient_last_error": snap_errors[-1][:110] if snap_errors else "",
        "audio_output": _audio_output(_read_optional(AUDIO_OUTPUT_PATH)),
        "control_api_unit": unit("freesounder-control-api.service"),
        "control_api": http(CONTROL_API_HEALTH),
        "agent_unit": unit("freesounder-player-agent.service"),
        "agent": http(AGENT_HEALTH),
        "ma_unit": unit("freesounder-music-assistant.service"),
        "ma_status": ma_status,
        "ma_container": container or "not running",
        "ma_port": port_open(8095),
        "ma_preload": preload,
        "role": _read_optional(Path("/run/freesounder/effective-role")).strip() or "?",
    }


def services_status_lines(live: dict[str, Any], lan_ip: str | None) -> list[str]:
    lines = [f"  LAN IP      : {lan_ip or 'none yet'}"]
    api = live.get("control_api")
    if api:
        version = api.get("version", "?")
        unit = live.get("control_api_unit")
        lines.append(f"  control API : up :8080 (v{version}, unit {unit})")
        state = api.get("ma_state") or ("connected" if api.get("ma_connected") else "unavailable")
        detail = api.get("ma_detail")
        lines.append(f"  API -> MA   : {state}" + (f" - {detail}" if detail else ""))
    else:
        unit = live.get("control_api_unit")
        lines.append(f"  control API : not answering on :8080 (unit {unit})")
    agent = live.get("agent")
    if agent:
        lines.append(f"  player agent: up :8090 ({agent.get('state', '?')})")
    else:
        lines.append(f"  player agent: not answering on :8090 (unit {live.get('agent_unit')})")
    ma = live.get("ma_status") or {}
    ma_line = f"  Music Asst. : unit {live.get('ma_unit')}, container {live.get('ma_container')}"
    ma_line += ", :8095 open" if live.get("ma_port") else ", :8095 closed"
    lines.append(ma_line)
    if ma:
        launcher = f"{ma.get('state', '?')} - {ma.get('detail', '')}".rstrip(" -")
        lines.append(f"  MA launcher : {launcher}")
    if live.get("ma_preload"):
        lines.append("  MA image    : bundled archive not unpacked yet")
    if "snapclient_unit" in live:
        snap_unit, snap_host = live.get("snapclient_unit"), live.get("snap_host")
        lines.append(f"  snapclient  : unit {snap_unit} -> {snap_host}")
        stream = "open" if live.get("snapserver_stream") else "closed"
        control = "open" if live.get("snapserver_control") else "closed"
        lines.append(f"  snapserver  : :1704 {stream}, :1705 {control}")
        lines.append(f"  audio out   : {live.get('audio_output')}")
        if live.get("snapclient_last_error"):
            lines.append(f"  snap error  : {live.get('snapclient_last_error')}")
    lines.append(f"  role        : {live.get('role')}")
    return lines

"""Diagnostic report for Bluetooth provisioning (no secrets)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from freesounder_ble_provisioning.network import (
    has_lan_gateway,
    inspect_default_routes,
    inspect_ipv4,
)
from freesounder_ble_provisioning.protocol import (
    COMPANY_ID_TEST,
    MANUFACTURER_PAYLOAD,
    SHORT_LOCAL_NAME,
    legacy_advertisement_length,
    legacy_scan_response_length,
)
from freesounder_ble_provisioning.status import (
    STATUS_PATH,
    WIFI_STATUS_PATH,
    read_services_live,
    read_wifi_live,
    services_status_lines,
    wifi_status_lines,
)


def collect_report() -> str:
    lines: list[str] = ["FreeSounder BLE provisioning doctor", ""]
    lines += _section("Adapter", _adapter_lines())
    lines += _section("rfkill", _cmd_lines(["rfkill", "list"]))
    lines += _section("Units", _unit_lines())
    lines += _section("Network", _network_lines())
    lines += _section("Wi-Fi", _wifi_lines())
    lines += _section("Services", _services_lines())
    lines += _section(
        "Advertisement budget",
        [
            "primary PDU (flags + 128-bit Improv UUID + 16-bit service data): "
            f"{legacy_advertisement_length(service_data=bytes(6))}/31 bytes "
            "(full — no room for a name or manufacturer data)",
            (
                "scan response (mfg "
                f"0x{COMPANY_ID_TEST:04X} {MANUFACTURER_PAYLOAD.decode('ascii')} "
                f"+ LocalName {SHORT_LOCAL_NAME}-XXXX): "
                f"{legacy_scan_response_length(local_name=SHORT_LOCAL_NAME + '-XXXX')}/31 bytes"
            ),
            "if LocalName=FreeSounder-XXXX were in the primary PDU: "
            f"{legacy_advertisement_length(service_data=bytes(6), local_name='FreeSounder-XXXX')}"
            "/31 bytes (does not fit)",
        ],
    )
    if STATUS_PATH.is_file():
        try:
            runtime = STATUS_PATH.read_text(encoding="utf-8").splitlines()
            lines += _section("Runtime status", runtime)
        except OSError:
            pass
    return "\n".join(lines) + "\n"


def main() -> None:
    print(collect_report(), end="")


def _section(title: str, body: list[str]) -> list[str]:
    return [f"== {title} ==", *body, ""]


def _adapter_lines() -> list[str]:
    lines: list[str] = []
    bt_dir = Path("/sys/class/bluetooth")
    if bt_dir.is_dir():
        entries = sorted(p.name for p in bt_dir.iterdir())
        lines.append("sysfs: " + (", ".join(entries) if entries else "(empty)"))
    else:
        lines.append("sysfs: /sys/class/bluetooth missing (no controller)")
    hci = Path("/sys/class/bluetooth/hci0")
    if hci.is_dir():
        for name in ("address", "name", "power", "manufacturer"):
            path = hci / name
            if path.exists():
                try:
                    lines.append(f"hci0 {name}: {path.read_text(encoding='utf-8').strip()}")
                except OSError:
                    pass
    if shutil.which("bluetoothctl"):
        lines.extend(_cmd_lines(["bluetoothctl", "show"], empty="bluetoothctl show: (no adapter)"))
    if shutil.which("busctl"):
        lines.extend(
            _cmd_lines(
                [
                    "busctl",
                    "--system",
                    "tree",
                    "org.bluez",
                ],
                empty="busctl: org.bluez not on the system bus",
            )
        )
    return lines or ["(no adapter tools available)"]


def _unit_lines() -> list[str]:
    lines: list[str] = []
    for unit in (
        "bluetooth.service",
        "hciuart.service",
        "NetworkManager.service",
        "freesounder-ble-provisioning.service",
        "freesounderd.service",
        "snapclient.service",
        "snapserver.service",
        "freesounder-spotify.service",
        "nqptp.service",
    ):
        enabled = _systemctl("is-enabled", unit)
        active = _systemctl("is-active", unit)
        lines.append(f"{unit}: enabled={enabled} active={active}")
    return lines


def _network_lines() -> list[str]:
    rows = inspect_ipv4()
    if not rows:
        return ["no IPv4 addresses; treated as offline (will advertise)"]
    lines = []
    for row in rows:
        reason = row.skip_reason or "usable"
        flags = f"up={row.up} carrier={row.carrier} virtual={row.virtual}"
        lines.append(f"{row.iface} {row.address} [{reason}; {flags}]")
    routes = inspect_default_routes()
    if not routes:
        lines.append("default route: none")
    for route in routes:
        tag = "gateway" if route["usable"] else "ignored"
        lines.append(
            f"default via {route['via'] or 'no-via'} dev {route['dev'] or '?'} ({tag})"
        )
    if has_lan_gateway():
        lines.append("verdict: online — default route to a LAN gateway; will not advertise")
    else:
        lines.append("verdict: offline — no LAN gateway; service should advertise")
    return lines


def _wifi_lines() -> list[str]:
    import json

    try:
        saved = json.loads(WIFI_STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    try:
        live = read_wifi_live()
    except Exception as exc:  # noqa: BLE001
        return [f"could not read Wi-Fi state: {exc}"]
    lines = [line.strip() for line in wifi_status_lines(live, saved)]
    lines += _cmd_lines(["rfkill", "list"], empty="rfkill: no devices")
    return lines


def lan_ip() -> str | None:
    for row in inspect_ipv4():
        if row.usable:
            return row.address
    return None


def _services_lines() -> list[str]:
    try:
        live = read_services_live()
    except Exception as exc:  # noqa: BLE001
        return [f"could not read service state: {exc}"]
    return [line.strip() for line in services_status_lines(live, lan_ip())]


def _systemctl(action: str, unit: str) -> str:
    try:
        proc = subprocess.run(
            ["systemctl", action, unit],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    out = (proc.stdout or proc.stderr or "").strip().splitlines()
    return out[0] if out else f"exit {proc.returncode}"


def _cmd_lines(argv: list[str], empty: str = "(no output)") -> list[str]:
    if not shutil.which(argv[0]):
        return [f"{argv[0]}: not installed"]
    try:
        proc = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [f"{' '.join(argv)}: {exc}"]
    text = (proc.stdout or proc.stderr or "").strip()
    if not text:
        return [empty]
    return text.splitlines()[:40]


if __name__ == "__main__":
    main()

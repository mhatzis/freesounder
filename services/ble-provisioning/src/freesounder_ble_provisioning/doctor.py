"""Diagnostic report for Bluetooth provisioning (no secrets)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from freesounder_ble_provisioning.network import inspect_ipv4
from freesounder_ble_provisioning.protocol import legacy_advertisement_length


def collect_report() -> str:
    lines: list[str] = ["FreeSounder BLE provisioning doctor", ""]
    lines += _section("Adapter", _adapter_lines())
    lines += _section("rfkill", _cmd_lines(["rfkill", "list"]))
    lines += _section("Units", _unit_lines())
    lines += _section("Network", _network_lines())
    lines += _section(
        "Advertisement budget",
        [
            "primary PDU without name: "
            f"{legacy_advertisement_length(service_data=bytes(6))}/31 bytes",
            "if LocalName=FreeSounder-XXXX were included: "
            f"{legacy_advertisement_length(service_data=bytes(6), local_name='FreeSounder-XXXX')}"
            "/31 bytes",
        ],
    )
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
    usable = [row for row in rows if row.usable]
    for row in rows:
        reason = row.skip_reason or "usable"
        flags = f"up={row.up} carrier={row.carrier} virtual={row.virtual}"
        lines.append(f"{row.iface} {row.address} [{reason}; {flags}]")
    if usable:
        lines.append("verdict: online — service will not advertise")
    else:
        lines.append("verdict: offline — service should advertise")
    return lines


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

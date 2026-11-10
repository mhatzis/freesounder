"""NetworkManager / nmcli helpers. Passwords are never logged."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from freesounder_ble_provisioning.protocol import ConnectResult, NetworkPort, WifiNetwork

logger = logging.getLogger(__name__)

# Patterns matched against nmcli stderr after the password has been stripped.
_WRONG_PASSWORD = re.compile(
    r"password|psk|802-11-wireless-security|secrets? were required|bad pass",
    re.I,
)
_SSID_NOT_FOUND = re.compile(
    r"no network with ssid|ssid .+ not found|not found|no such.*ssid|"
    r"the wifi network could not be found",
    re.I,
)
_SECRET_RE = re.compile(r"(password|psk|passwd|wifi-sec\.psk)\s+\S+", re.I)


def redact(text: str) -> str:
    """Remove likely secrets so the string is safe to log or classify."""

    return _SECRET_RE.sub(r"\1 <redacted>", text)


def classify_nmcli_error(text: str) -> str:
    """Map nmcli output (already redacted) to a short reason code."""

    if _SSID_NOT_FOUND.search(text):
        return "ssid_not_found"
    if _WRONG_PASSWORD.search(text):
        return "wrong_password"
    return "unable_to_connect"


class NetworkManagerPort(NetworkPort):
    """Real nmcli-backed port used on the Pi."""

    def __init__(
        self,
        nmcli: str = "nmcli",
        ifname: str | None = "wlan0",
        connections_dir: Path | None = None,
        addrs_fn: object | None = None,
        run_fn: object | None = None,
    ) -> None:
        self._nmcli = nmcli
        self._ifname = ifname
        self._connections_dir = connections_dir or Path(
            "/etc/NetworkManager/system-connections"
        )
        self._addrs_fn = addrs_fn
        self._run_fn = run_fn

    def has_usable_network(self) -> bool:
        try:
            return bool(self._addrs())
        except Exception:  # noqa: BLE001
            return False

    def current_ip(self) -> str | None:
        addrs = self._addrs()
        return addrs[0] if addrs else None

    def _addrs(self) -> list[str]:
        if self._addrs_fn is not None:
            return list(self._addrs_fn())  # type: ignore[operator]
        return _sync_global_addrs()

    async def _run(self, *args: str) -> _Completed:
        if self._run_fn is not None:
            return await self._run_fn(self._nmcli, *args)  # type: ignore[misc]
        return await _run_nmcli(self._nmcli, *args)

    async def scan_wifi(self) -> list[WifiNetwork]:
        args = ["-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list", "--rescan", "yes"]
        completed = await self._run(*args)
        if completed.returncode not in {0, None}:
            logger.info("Wi-Fi scan failed: %s", redact(completed.stderr))
        out_text = getattr(completed, "stdout", "")
        networks: list[WifiNetwork] = []
        seen: set[str] = set()
        for line in out_text.splitlines():
            parts = line.split(":")
            if len(parts) < 2:
                continue
            ssid = parts[0]
            if not ssid or ssid in seen:
                continue
            seen.add(ssid)
            try:
                rssi = int(parts[1])
            except ValueError:
                rssi = 0
            security = parts[2] if len(parts) > 2 else ""
            networks.append(
                WifiNetwork(ssid=ssid, rssi=_signal_to_rssi(rssi), auth=_auth_label(security))
            )
        return networks

    async def connect_wifi(self, ssid: str, password: str) -> ConnectResult:
        # Write a 0600 keyfile so the PSK is not passed on a logged command line
        # and is never printed. nmcli still needs the SSID (not a secret).
        from tempfile import TemporaryDirectory

        safe_ssid = ssid[:32]
        with TemporaryDirectory(prefix="freesounder-nm-") as tmp:
            keyfile = Path(tmp) / "freesounder-wifi.nmconnection"
            keyfile.write_text(_nm_connection(safe_ssid, password, self._ifname), encoding="utf-8")
            keyfile.chmod(0o600)
            dest = self._connections_dir / "freesounder-wifi.nmconnection"
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(keyfile.read_bytes())
                dest.chmod(0o600)
            except OSError as exc:
                logger.info("Could not write NetworkManager connection: %s", exc)
                return ConnectResult(ok=False, reason="unable_to_connect")

        reload = await self._run("connection", "reload")
        if reload.returncode not in {0, None}:
            logger.info("nmcli connection reload failed")
        up = await self._run(
            "--wait",
            "30",
            "connection",
            "up",
            "freesounder-wifi",
        )
        if up.returncode == 0:
            addrs = self._addrs()
            # Give DHCP a moment if the link is up without an address yet.
            if not addrs:
                await asyncio.sleep(2)
                addrs = self._addrs()
            ip = addrs[0] if addrs else None
            if ip:
                logger.info("Wi-Fi connected, address assigned")
                return ConnectResult(ok=True, ip=ip, reason="")
            logger.info("Wi-Fi up but no address yet")
            return ConnectResult(ok=False, reason="unable_to_connect")

        reason = classify_nmcli_error(redact(up.stderr))
        logger.info("Wi-Fi connect failed (%s)", reason)
        return ConnectResult(ok=False, reason=reason)


class _Completed:
    def __init__(self, returncode: int | None, stderr: str, stdout: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout


async def _run_nmcli(nmcli: str, *args: str) -> _Completed:
    proc = await asyncio.create_subprocess_exec(
        nmcli,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return _Completed(
        proc.returncode,
        err.decode("utf-8", errors="replace"),
        out.decode("utf-8", errors="replace"),
    )


def _sync_global_addrs() -> list[str]:
    import subprocess

    try:
        out = subprocess.check_output(
            ["ip", "-4", "-o", "addr", "show", "scope", "global"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    addrs: list[str] = []
    for line in out.splitlines():
        parts = line.split()
        if "inet" in parts:
            idx = parts.index("inet")
            if idx + 1 < len(parts):
                addrs.append(parts[idx + 1].split("/", 1)[0])
    return addrs


def _signal_to_rssi(signal: int) -> int:
    """nmcli SIGNAL is 0–100; Improv examples use dBm-like RSSI."""

    if signal <= 0:
        return -90
    if signal >= 100:
        return -30
    return int(-90 + (signal / 100) * 60)


def _auth_label(security: str) -> str:
    text = (security or "").strip()
    if not text:
        return "NO"
    text = text.replace(" ", "/").replace(":", "/")
    # nmcli uses WPA2 WPA3 WEP 802.1X etc.
    return text.replace("802.1X", "WPA2 EAP") or "NO"


def _nm_connection(ssid: str, password: str, ifname: str | None) -> str:
    lines = [
        "[connection]",
        "id=freesounder-wifi",
        "type=wifi",
        "autoconnect=true",
    ]
    if ifname:
        lines.append(f"interface-name={ifname}")
    lines += [
        "",
        "[wifi]",
        "mode=infrastructure",
        f"ssid={ssid}",
        "",
    ]
    if password:
        lines += [
            "[wifi-security]",
            "key-mgmt=wpa-psk",
            f"psk={password}",
            "",
        ]
    lines += [
        "[ipv4]",
        "method=auto",
        "",
        "[ipv6]",
        "method=auto",
        "",
    ]
    return "\n".join(lines)

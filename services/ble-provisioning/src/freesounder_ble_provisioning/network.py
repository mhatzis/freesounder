"""NetworkManager / nmcli helpers. Passwords are never logged."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from freesounder_ble_provisioning.protocol import ConnectResult, NetworkPort, WifiNetwork

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LinkAddress:
    """One IPv4 address on a named interface, with uplink classification."""

    iface: str
    address: str
    virtual: bool = False
    up: bool = False
    carrier: bool = False
    link_local: bool = False
    loopback: bool = False

    @property
    def usable(self) -> bool:
        if self.virtual or self.loopback or self.link_local:
            return False
        if not self.up or not self.carrier:
            return False
        return True

    @property
    def skip_reason(self) -> str | None:
        if self.loopback:
            return "loopback"
        if self.link_local:
            return "link-local"
        if self.virtual:
            return "virtual"
        if not self.up:
            return "down"
        if not self.carrier:
            return "no-carrier"
        return None

# Patterns matched against nmcli stderr after the password has been stripped.
# Order matters: the first match wins.
_REASON_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("wifi_unmanaged", re.compile(r"unmanaged", re.I)),
    (
        "radio_off",
        re.compile(
            r"rfkill|radio (is )?(disabled|off)|wi-?fi is disabled|wireless is disabled|"
            r"no suitable device found|device is not available|not available because",
            re.I,
        ),
    ),
    (
        "ssid_not_found",
        re.compile(
            r"no network with ssid|ssid .{0,40} not found|could not be found|"
            r"no such.*ssid|network .{0,40} not found",
            re.I,
        ),
    ),
    (
        "wrong_password",
        re.compile(
            r"secrets? were required|802-11-wireless-security|bad pass|"
            r"password|psk|4-way handshake|ssid-not-found-or-wrong-password",
            re.I,
        ),
    ),
    ("no_address", re.compile(r"ip configuration could not be reserved|dhcp|no address", re.I)),
    ("timeout", re.compile(r"timeout|timed out|activation was not completed", re.I)),
)
_SECRET_RE = re.compile(r"(password|psk|passwd|wifi-sec\.psk)\s+\S+", re.I)

WIFI_STATUS_PATH = Path("/run/freesounder/wifi-status.json")
COUNTRY_FILE = Path("/etc/freesounder/wifi-country")
DEFAULT_COUNTRY = "AU"
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")


def redact(text: str) -> str:
    """Remove likely secrets so the string is safe to log or classify."""

    return _SECRET_RE.sub(r"\1 <redacted>", text)


def classify_nmcli_error(text: str) -> str:
    """Map nmcli output (already redacted) to a short reason code."""

    for reason, pattern in _REASON_PATTERNS:
        if pattern.search(text):
            return reason
    return "unable_to_connect"


def short_detail(text: str, limit: int = 120) -> str:
    """One redacted line of nmcli output, safe to show in the app."""

    lines = [line.strip() for line in redact(text).splitlines() if line.strip()]
    if not lines:
        return ""
    line = lines[-1]
    for prefix in ("Error: ", "error: "):
        if line.startswith(prefix):
            line = line[len(prefix) :]
    return line[:limit]


def valid_country(code: str) -> bool:
    """ISO 3166-1 alpha-2, checked against tzdata's list when present."""

    code = (code or "").strip().upper()
    if not _COUNTRY_RE.match(code) or code == "00":
        return False
    table = Path("/usr/share/zoneinfo/iso3166.tab")
    try:
        text = table.read_text(encoding="utf-8")
    except OSError:
        return True
    return any(line.startswith(code + "\t") for line in text.splitlines())


def valid_wpa_password(password: str) -> bool:
    """WPA-PSK passphrase: 8–63 ASCII characters, or 64 hex digits."""

    if not password:
        return True
    raw = password.encode("utf-8")
    if len(raw) == 64:
        return all(ch in "0123456789abcdefABCDEF" for ch in password)
    return 8 <= len(raw) <= 63


def split_nmcli_terse(line: str) -> list[str]:
    """Split one `nmcli -t -e yes` line on unescaped ':' and unescape."""

    fields: list[str] = []
    current: list[str] = []
    escaped = False
    for ch in line:
        if escaped:
            current.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(ch)
    fields.append("".join(current))
    return fields


def key_mgmt_for(security: str, password: str) -> str | None:
    """NetworkManager key-mgmt for a scanned network's SECURITY field."""

    if not password:
        return None
    sec = (security or "").upper()
    if "WPA3" in sec and "WPA2" not in sec and "WPA1" not in sec:
        return "sae"
    return "wpa-psk"


def persist_regdom(cmdline: Path, country: str) -> bool:
    """Set cfg80211.ieee80211_regdom=XX on the kernel command line (one line)."""

    try:
        text = cmdline.read_text(encoding="utf-8")
    except OSError:
        return False
    line = text.strip().splitlines()[0] if text.strip() else ""
    parts = [p for p in line.split() if not p.startswith("cfg80211.ieee80211_regdom=")]
    parts.append(f"cfg80211.ieee80211_regdom={country}")
    new = " ".join(parts) + "\n"
    if new == text:
        return True
    tmp = cmdline.with_name(cmdline.name + ".freesounder-tmp")
    try:
        tmp.write_text(new, encoding="utf-8")
        tmp.replace(cmdline)
    except OSError:
        try:
            cmdline.write_text(new, encoding="utf-8")
        except OSError:
            return False
    return True


class NetworkManagerPort(NetworkPort):
    """Real nmcli-backed port used on the Pi (runs as root under systemd)."""

    def __init__(
        self,
        nmcli: str = "nmcli",
        ifname: str | None = "wlan0",
        connections_dir: Path | None = None,
        addrs_fn: object | None = None,
        run_fn: object | None = None,
        cmd_fn: object | None = None,
        status_path: Path | None = None,
        country_file: Path | None = None,
        cmdline_path: Path | None = None,
        radio_wait: float = 8.0,
        address_wait: float = 15.0,
        connect_wait: int = 40,
        sleep_fn: object | None = None,
    ) -> None:
        self._nmcli = nmcli
        self._ifname = ifname
        self._connections_dir = connections_dir or Path(
            "/etc/NetworkManager/system-connections"
        )
        self._addrs_fn = addrs_fn
        self._run_fn = run_fn
        self._cmd_fn = cmd_fn
        self._status_path = status_path or WIFI_STATUS_PATH
        self._country_file = country_file or COUNTRY_FILE
        self._cmdline_path = cmdline_path or Path("/boot/firmware/cmdline.txt")
        self._radio_wait = radio_wait
        self._address_wait = address_wait
        self._connect_wait = connect_wait
        self._sleep = sleep_fn or asyncio.sleep
        self._last_scan: dict[str, str] = {}
        self._country = self._load_country()

    # -- helpers -------------------------------------------------------------

    def _load_country(self) -> str:
        try:
            code = self._country_file.read_text(encoding="utf-8").strip().upper()
        except OSError:
            code = ""
        return code if valid_country(code) else DEFAULT_COUNTRY

    @property
    def country(self) -> str:
        return self._country

    def has_usable_network(self) -> bool:
        """True only when there is a default IPv4 route on a real uplink.

        Link-local addresses, docker0, and NetworkManager "connected
        (local only)" (an address with no LAN gateway) must not count as
        online — the player should keep advertising Improv setup.
        """

        try:
            return has_lan_gateway()
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

    async def _cmd(self, *argv: str) -> _Completed:
        if self._cmd_fn is not None:
            return await self._cmd_fn(*argv)  # type: ignore[misc]
        return await _run_nmcli(argv[0], *argv[1:])

    def _status(self, update: dict[str, object]) -> None:
        try:
            from freesounder_ble_provisioning.status import write_status

            write_status(update, path=self._status_path)
        except Exception:  # noqa: BLE001
            logger.debug("could not write wifi status", exc_info=True)

    async def device_state(self) -> str:
        """NetworkManager state of the Wi-Fi device ('' if not listed)."""

        completed = await self._run("-t", "-e", "yes", "-f", "DEVICE,TYPE,STATE", "device")
        for line in (completed.stdout or "").splitlines():
            fields = split_nmcli_terse(line)
            if len(fields) < 3:
                continue
            if (self._ifname and fields[0] == self._ifname) or (
                not self._ifname and fields[1] == "wifi"
            ):
                return fields[2]
        return ""

    async def ensure_radio(self) -> str:
        """Unblock wlan, set the regulatory country, turn NM's radio on.

        Raspberry Pi OS boots every radio soft-blocked (rfkill
        default_state=0) until a Wi-Fi country is set, and pi-gen writes
        WirelessEnabled=false when no country is configured. Without this
        the scan list is empty and every join fails.
        """

        await self._cmd("rfkill", "unblock", "wifi")
        await self._cmd("iw", "reg", "set", self._country)
        await self._run("radio", "wifi", "on")
        state = await self.device_state()
        waited = 0.0
        while state in {"", "unavailable"} and waited < self._radio_wait:
            await self._sleep(0.5)
            waited += 0.5
            state = await self.device_state()
        self._status({"nm_state": state or "missing", "country": self._country})
        return state

    async def set_country(self, country: str) -> bool:
        code = (country or "").strip().upper()
        if not valid_country(code):
            return False
        self._country = code
        try:
            self._country_file.parent.mkdir(parents=True, exist_ok=True)
            self._country_file.write_text(code + "\n", encoding="utf-8")
        except OSError as exc:
            logger.info("Could not save Wi-Fi country: %s", exc)
        if not persist_regdom(self._cmdline_path, code):
            logger.info("Could not set regulatory domain on the kernel command line")
        await self._cmd("iw", "reg", "set", code)
        self._status({"country": code})
        logger.info("Wi-Fi country set to %s", code)
        return True

    # -- Improv operations ----------------------------------------------------

    async def scan_wifi(self) -> list[WifiNetwork]:
        state = await self.ensure_radio()
        networks = await self._scan_once()
        if not networks:
            # The first rescan right after the radio comes up is often empty.
            await self._sleep(3)
            networks = await self._scan_once()
        self._status(
            {
                "scan_count": len(networks),
                "scan_at": _now_iso(),
                "nm_state": state or "missing",
            }
        )
        return networks

    async def _scan_once(self) -> list[WifiNetwork]:
        args = [
            "-t",
            "-e",
            "yes",
            "-f",
            "SSID,SIGNAL,SECURITY",
            "dev",
            "wifi",
            "list",
            "--rescan",
            "yes",
        ]
        if self._ifname:
            args += ["ifname", self._ifname]
        completed = await self._run(*args)
        if completed.returncode not in {0, None}:
            logger.info("Wi-Fi scan failed: %s", short_detail(completed.stderr))
        best: dict[str, WifiNetwork] = {}
        order: list[str] = []
        for line in (getattr(completed, "stdout", "") or "").splitlines():
            parts = split_nmcli_terse(line)
            if len(parts) < 2:
                continue
            ssid = parts[0]
            if not ssid:
                continue
            try:
                signal = int(parts[1])
            except ValueError:
                signal = 0
            security = parts[2] if len(parts) > 2 else ""
            net = WifiNetwork(ssid=ssid, rssi=_signal_to_rssi(signal), auth=_auth_label(security))
            if ssid not in best:
                order.append(ssid)
                best[ssid] = net
                self._last_scan[ssid] = security
            elif net.rssi > best[ssid].rssi:
                best[ssid] = net
        return [best[s] for s in order]

    async def connect_wifi(self, ssid: str, password: str) -> ConnectResult:
        safe_ssid = ssid[:32]
        attempt: dict[str, object] = {"ssid": safe_ssid, "at": _now_iso()}

        def done(result: ConnectResult) -> ConnectResult:
            attempt.update(
                {"ok": result.ok, "reason": result.reason, "detail": result.detail, "ip": result.ip}
            )
            self._status({"last_attempt": attempt})
            return result

        if not valid_wpa_password(password):
            return done(
                ConnectResult(
                    ok=False,
                    reason="invalid_password",
                    detail="Wi-Fi passwords are 8 to 63 characters",
                )
            )
        state = await self.ensure_radio()
        if state in {"", "unavailable"}:
            rfkill = await self._cmd("rfkill", "list", "wifi")
            blocked = "yes" in (rfkill.stdout or "").lower()
            detail = f"wlan0 is {state or 'missing'} in NetworkManager"
            if blocked:
                detail += " (rfkill blocked)"
            return done(ConnectResult(ok=False, reason="radio_off", detail=detail))
        if state == "unmanaged":
            return done(
                ConnectResult(
                    ok=False, reason="wifi_unmanaged", detail="wlan0 is unmanaged in NetworkManager"
                )
            )
        if safe_ssid not in self._last_scan:
            await self._scan_once()
        hidden = safe_ssid not in self._last_scan
        key_mgmt = key_mgmt_for(self._last_scan.get(safe_ssid, ""), password)

        dest = self._connections_dir / "freesounder-wifi.nmconnection"
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Create 0600 first so the PSK is never world-readable.
            dest.touch(mode=0o600, exist_ok=True)
            dest.chmod(0o600)
            dest.write_text(
                _nm_connection(safe_ssid, password, self._ifname, key_mgmt=key_mgmt, hidden=hidden),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.info("Could not write NetworkManager connection: %s", exc)
            return done(
                ConnectResult(ok=False, reason="unable_to_connect", detail="could not save network")
            )

        reload = await self._run("connection", "reload")
        if reload.returncode not in {0, None}:
            logger.info("nmcli connection reload failed: %s", short_detail(reload.stderr))
        up = await self._run(
            "--wait", str(self._connect_wait), "connection", "up", "freesounder-wifi"
        )
        if up.returncode == 0:
            addrs = self._addrs()
            waited = 0.0
            while not addrs and waited < self._address_wait:
                await self._sleep(1)
                waited += 1
                addrs = self._addrs()
            ip = addrs[0] if addrs else None
            if ip:
                logger.info("Wi-Fi connected, address assigned")
                return done(ConnectResult(ok=True, ip=ip, reason=""))
            logger.info("Wi-Fi up but no address yet")
            return done(
                ConnectResult(
                    ok=False, reason="no_address", detail="joined but DHCP gave no address"
                )
            )

        # Do not leave a failed profile behind: NM would keep retrying it in
        # the background and hold wlan0 while the user tries again.
        try:
            dest.unlink()
        except OSError:
            pass
        await self._run("connection", "reload")
        detail = short_detail(up.stderr) or short_detail(up.stdout)
        reason = classify_nmcli_error(redact(up.stderr + "\n" + up.stdout))
        if reason == "unable_to_connect" and hidden:
            reason = "ssid_not_found"
        logger.info("Wi-Fi connect failed (%s): %s", reason, detail)
        return done(ConnectResult(ok=False, reason=reason, detail=detail))


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
    """Usable IPv4 addresses on a real uplink (not docker0, not no-carrier eth)."""

    return [row.address for row in inspect_ipv4() if row.usable]


def inspect_ipv4(
    ip_text: str | None = None, sys_class_net: Path | None = None
) -> list[LinkAddress]:
    """Parse IPv4 addresses and classify which ones mean the player is online."""

    if ip_text is None:
        ip_text = _ip_addr_text()
    rows: list[LinkAddress] = []
    for iface, address in parse_ip_ipv4(ip_text):
        virtual = is_virtual_iface(iface)
        up, carrier = link_flags(iface, sys_class_net=sys_class_net)
        rows.append(
            LinkAddress(
                iface=iface,
                address=address,
                virtual=virtual,
                up=up,
                carrier=carrier,
                link_local=_is_link_local(address),
                loopback=_is_loopback(address),
            )
        )
    return rows


def parse_ip_ipv4(text: str) -> list[tuple[str, str]]:
    """Parse `ip -4 -o addr` lines into (iface, ipv4) pairs."""

    found: list[tuple[str, str]] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4 or "inet" not in parts:
            continue
        # 2: eth0    inet 192.168.1.4/24 ...
        iface = parts[1].rstrip(":")
        idx = parts.index("inet")
        if idx + 1 >= len(parts):
            continue
        address = parts[idx + 1].split("/", 1)[0]
        found.append((iface, address))
    return found


def is_virtual_iface(name: str) -> bool:
    if name in _VIRTUAL_EXACT or name == "lo":
        return True
    return name.startswith(_VIRTUAL_PREFIXES)


def link_flags(iface: str, sys_class_net: Path | None = None) -> tuple[bool, bool]:
    """Return (operstate-up, has-carrier). Missing sysfs is treated as down."""

    base = (sys_class_net or Path("/sys/class/net")) / iface
    try:
        oper = (base / "operstate").read_text(encoding="utf-8").strip().lower()
    except OSError:
        oper = ""
    try:
        carrier_raw = (base / "carrier").read_text(encoding="utf-8").strip()
    except OSError:
        carrier_raw = ""
    up = oper in {"up", "unknown"}
    carrier = carrier_raw == "1" or (oper == "up" and carrier_raw == "")
    return up, carrier


def _ip_addr_text() -> str:
    import subprocess

    try:
        return subprocess.check_output(
            ["ip", "-4", "-o", "addr", "show"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return ""


def _is_link_local(address: str) -> bool:
    parts = address.split(".")
    return len(parts) == 4 and parts[0] == "169" and parts[1] == "254"


def _is_loopback(address: str) -> bool:
    return address.startswith("127.")


_VIRTUAL_PREFIXES = (
    "docker",
    "br-",
    "veth",
    "virbr",
    "cni",
    "flannel",
    "lxc",
    "lxd",
    "incus",
    "nodocker",
    "tunl",
    "sit",
    "dummy",
    "vmnet",
    "kube",
)
_VIRTUAL_EXACT = frozenset({"lo", "docker0", "cni0", "flannel.1", "kube-ipvs0"})



def has_lan_gateway(
    route_text: str | None = None,
) -> bool:
    """A default IPv4 route whose device is a real (non-virtual) interface."""

    return any(row["usable"] for row in inspect_default_routes(route_text))


def inspect_default_routes(route_text: str | None = None) -> list[dict[str, object]]:
    """Parse `ip -4 route show default` into classified default routes."""

    if route_text is None:
        route_text = _ip_route_text()
    rows: list[dict[str, object]] = []
    for line in route_text.splitlines():
        parts = line.split()
        if not parts or parts[0] != "default":
            continue
        dev = ""
        via = ""
        if "dev" in parts:
            dev = parts[parts.index("dev") + 1] if parts.index("dev") + 1 < len(parts) else ""
        if "via" in parts:
            via = parts[parts.index("via") + 1] if parts.index("via") + 1 < len(parts) else ""
        virtual = is_virtual_iface(dev) if dev else True
        usable = bool(dev) and not virtual
        rows.append({"dev": dev, "via": via, "virtual": virtual, "usable": usable, "line": line})
    return rows


def _ip_route_text() -> str:
    import subprocess

    try:
        return subprocess.check_output(
            ["ip", "-4", "route", "show", "default"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return ""


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


def _keyfile_ssid(ssid: str) -> str:
    """Plain SSID when it is simple; otherwise NM's ';'-separated byte list."""

    simple = ssid and ssid.strip() == ssid and all(ch.isalnum() or ch in "_-. " for ch in ssid)
    if simple and not ssid.isdigit():
        return ssid
    return "".join(f"{b};" for b in ssid.encode("utf-8"))


def _keyfile_escape(value: str) -> str:
    """GKeyFile string escaping (NetworkManager reads psk with it)."""

    out = (
        value.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
        .replace("\r", "\\r")
    )
    if out.startswith(" "):
        out = "\\s" + out[1:]
    return out


def _nm_connection(
    ssid: str,
    password: str,
    ifname: str | None,
    *,
    key_mgmt: str | None = "wpa-psk",
    hidden: bool = False,
) -> str:
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
        f"ssid={_keyfile_ssid(ssid)}",
    ]
    if hidden:
        lines.append("hidden=true")
    lines.append("")
    if password:
        lines += [
            "[wifi-security]",
            f"key-mgmt={key_mgmt or 'wpa-psk'}",
            f"psk={_keyfile_escape(password)}",
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


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

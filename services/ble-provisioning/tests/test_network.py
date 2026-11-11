"""NetworkManager join path with nmcli, rfkill and iw faked at the process boundary."""

from __future__ import annotations

from pathlib import Path

import pytest

from freesounder_ble_provisioning.network import (
    NetworkManagerPort,
    _Completed,
    _keyfile_escape,
    _keyfile_ssid,
    classify_nmcli_error,
    key_mgmt_for,
    persist_regdom,
    redact,
    split_nmcli_terse,
    valid_wpa_password,
)
from freesounder_ble_provisioning.status import (
    parse_iw_reg,
    parse_nm_device_state,
    read_rfkill_wlan,
    wifi_status_lines,
)

SCAN = "Home:80:WPA2\nHome:40:WPA2\nCafe:30:\n:10:WPA2\nMy\\:Net:55:WPA2 WPA3\nNew:60:WPA3\n"


class FakePi:
    """A Pi as shipped by pi-gen without a country: wlan soft-blocked, NM radio off."""

    def __init__(self, *, up_rc: int = 0, up_err: str = "", unblockable: bool = True) -> None:
        self.rfkill_blocked = True
        self.radio_on = False
        self.unblockable = unblockable
        self.up_rc = up_rc
        self.up_err = up_err
        self.calls: list[tuple[str, ...]] = []
        self.country = ""

    @property
    def available(self) -> bool:
        return not self.rfkill_blocked and self.radio_on

    async def nmcli(self, nmcli: str, *args: str) -> _Completed:
        del nmcli
        self.calls.append(("nmcli", *args))
        if args[:2] == ("radio", "wifi"):
            self.radio_on = True
            return _Completed(0, "")
        if "device" in args and "DEVICE,TYPE,STATE" in args:
            state = "disconnected" if self.available else "unavailable"
            return _Completed(0, "", f"eth0:ethernet:unavailable\nwlan0:wifi:{state}\n")
        if "list" in args:
            return _Completed(0, "", SCAN if self.available else "")
        if args[-2:] == ("up", "freesounder-wifi"):
            return _Completed(self.up_rc, self.up_err)
        return _Completed(0, "")

    async def cmd(self, *argv: str) -> _Completed:
        self.calls.append(argv)
        if argv[:3] == ("rfkill", "unblock", "wifi") and self.unblockable:
            self.rfkill_blocked = False
        if argv[:3] == ("iw", "reg", "set"):
            self.country = argv[3]
        if argv[:3] == ("rfkill", "list", "wifi"):
            soft = "yes" if self.rfkill_blocked else "no"
            return _Completed(0, "", f"1: phy0: Wireless LAN\n\tSoft blocked: {soft}\n")
        return _Completed(0, "")


async def _no_sleep(_: float) -> None:
    return None


def _port(tmp_path: Path, pi: FakePi, addrs: list[str] | None = None) -> NetworkManagerPort:
    return NetworkManagerPort(
        connections_dir=tmp_path / "nm",
        run_fn=pi.nmcli,
        cmd_fn=pi.cmd,
        addrs_fn=lambda: list(addrs or []),
        status_path=tmp_path / "wifi-status.json",
        country_file=tmp_path / "wifi-country",
        cmdline_path=tmp_path / "cmdline.txt",
        sleep_fn=_no_sleep,
    )


def test_redact_strips_password_material() -> None:
    raw = "nmcli wifi-sec.psk hunter2-secret failed"
    cleaned = redact(raw)
    assert "hunter2-secret" not in cleaned
    assert "<redacted>" in cleaned
    assert classify_nmcli_error(cleaned) == "wrong_password"


def test_classify_reasons() -> None:
    assert classify_nmcli_error("The WiFi network could not be found") == "ssid_not_found"
    assert classify_nmcli_error("secrets were required") == "wrong_password"
    assert classify_nmcli_error("device busy") == "unable_to_connect"
    assert (
        classify_nmcli_error(
            "Error: Connection activation failed: No suitable device found for this "
            "connection (device wlan0 not available because device is not available)."
        )
        == "radio_off"
    )
    assert classify_nmcli_error("device is strictly unmanaged") == "wifi_unmanaged"
    assert classify_nmcli_error("Error: Timeout expired (40 seconds)") == "timeout"
    assert classify_nmcli_error("IP configuration could not be reserved") == "no_address"


@pytest.mark.asyncio
async def test_scan_unblocks_radio_sets_country_and_parses(tmp_path: Path) -> None:
    pi = FakePi()
    port = _port(tmp_path, pi)
    nets = await port.scan_wifi()
    assert ("rfkill", "unblock", "wifi") in pi.calls
    assert ("nmcli", "radio", "wifi", "on") in pi.calls
    assert pi.country == "AU"  # default until the app sends one
    assert [n.ssid for n in nets] == ["Home", "Cafe", "My:Net", "New"]
    assert nets[0].auth == "WPA2"
    assert nets[1].auth == "NO"
    assert nets[0].rssi > nets[1].rssi
    status = (tmp_path / "wifi-status.json").read_text(encoding="utf-8")
    assert '"scan_count": 4' in status


@pytest.mark.asyncio
async def test_blocked_radio_reports_radio_off(tmp_path: Path) -> None:
    pi = FakePi(unblockable=False)
    port = _port(tmp_path, pi)
    assert await port.scan_wifi() == []
    result = await port.connect_wifi("Home", "super-secret-psk")
    assert result.ok is False
    assert result.reason == "radio_off"
    assert "rfkill blocked" in result.detail
    assert not (tmp_path / "nm" / "freesounder-wifi.nmconnection").exists()


@pytest.mark.asyncio
async def test_connect_writes_keyfile_without_logging_secret(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    pi = FakePi()
    port = _port(tmp_path, pi, addrs=["10.0.0.8"])
    with caplog.at_level("INFO"):
        result = await port.connect_wifi("Home", "super-secret-psk")
    assert result.ok is True
    assert result.ip == "10.0.0.8"
    keyfile = tmp_path / "nm" / "freesounder-wifi.nmconnection"
    assert keyfile.stat().st_mode & 0o777 == 0o600
    text = keyfile.read_text(encoding="utf-8")
    assert "psk=super-secret-psk" in text
    assert "ssid=Home" in text
    assert "key-mgmt=wpa-psk" in text
    assert "hidden=true" not in text
    flat = " ".join(" ".join(c) for c in pi.calls)
    assert "super-secret-psk" not in flat
    assert "super-secret-psk" not in caplog.text
    assert ("nmcli", "connection", "reload") in pi.calls
    status = (tmp_path / "wifi-status.json").read_text(encoding="utf-8")
    assert "super-secret-psk" not in status
    assert '"ssid": "Home"' in status


@pytest.mark.asyncio
async def test_wpa3_only_uses_sae_and_unseen_ssid_is_hidden(tmp_path: Path) -> None:
    pi = FakePi()
    port = _port(tmp_path, pi, addrs=["10.0.0.9"])
    await port.scan_wifi()
    await port.connect_wifi("New", "wpa3-password")
    text = (tmp_path / "nm" / "freesounder-wifi.nmconnection").read_text(encoding="utf-8")
    assert "key-mgmt=sae" in text
    await port.connect_wifi("Attic Hidden", "password123")
    text = (tmp_path / "nm" / "freesounder-wifi.nmconnection").read_text(encoding="utf-8")
    assert "hidden=true" in text
    assert "key-mgmt=wpa-psk" in text


@pytest.mark.asyncio
async def test_connect_maps_nmcli_failure_with_detail(tmp_path: Path) -> None:
    pi = FakePi(
        up_rc=4,
        up_err="Error: Connection activation failed: Secrets were required, but not provided.\n",
    )
    port = _port(tmp_path, pi)
    result = await port.connect_wifi("Home", "wrongpass")
    assert result.ok is False
    assert result.reason == "wrong_password"
    assert result.detail.startswith("Connection activation failed")
    assert not (tmp_path / "nm" / "freesounder-wifi.nmconnection").exists()


@pytest.mark.asyncio
async def test_short_password_rejected_before_nm(tmp_path: Path) -> None:
    pi = FakePi()
    port = _port(tmp_path, pi)
    result = await port.connect_wifi("Home", "short")
    assert result.reason == "invalid_password"
    assert not any(c[-2:] == ("up", "freesounder-wifi") for c in pi.calls)


@pytest.mark.asyncio
async def test_set_country_persists_regdom(tmp_path: Path) -> None:
    pi = FakePi()
    port = _port(tmp_path, pi)
    (tmp_path / "cmdline.txt").write_text(
        "console=tty1 root=PARTUUID=1-02 cfg80211.ieee80211_regdom=US rootwait\n", encoding="utf-8"
    )
    assert await port.set_country("nz") is True
    assert pi.country == "NZ"
    cmdline = (tmp_path / "cmdline.txt").read_text(encoding="utf-8")
    assert cmdline.count("cfg80211.ieee80211_regdom=") == 1
    assert "cfg80211.ieee80211_regdom=NZ" in cmdline
    assert cmdline.count("\n") == 1
    assert (tmp_path / "wifi-country").read_text(encoding="utf-8").strip() == "NZ"
    assert await port.set_country("ZZZ") is False
    assert await port.set_country("00") is False


def test_special_characters_in_keyfile() -> None:
    assert _keyfile_ssid("Home") == "Home"
    assert _keyfile_ssid("Café;#=") == "".join(f"{b};" for b in "Café;#=".encode())
    assert _keyfile_ssid(" lead").endswith(";")
    assert _keyfile_ssid("12345") == "49;50;51;52;53;"
    assert _keyfile_escape("a\\b") == "a\\\\b"
    assert _keyfile_escape(" pass;#=") == "\\spass;#="
    assert persist_regdom(Path("/nonexistent/cmdline.txt"), "AU") is False


def test_password_and_key_mgmt_rules() -> None:
    assert valid_wpa_password("12345678")
    assert not valid_wpa_password("1234567")
    assert valid_wpa_password("a" * 63)
    assert not valid_wpa_password("g" * 64)
    assert valid_wpa_password("0123456789abcdef" * 4)
    assert valid_wpa_password("")
    assert key_mgmt_for("WPA2 WPA3", "x") == "wpa-psk"
    assert key_mgmt_for("WPA3", "x") == "sae"
    assert key_mgmt_for("", "") is None
    assert split_nmcli_terse("My\\:Net:55:WPA2") == ["My:Net", "55", "WPA2"]


def test_status_parsers(tmp_path: Path) -> None:
    rf = tmp_path / "rfkill"
    for name, typ, soft in (("rfkill0", "bluetooth", "0"), ("rfkill1", "wlan", "1")):
        d = rf / name
        d.mkdir(parents=True)
        (d / "type").write_text(typ + "\n")
        (d / "soft").write_text(soft + "\n")
        (d / "hard").write_text("0\n")
    assert read_rfkill_wlan(rf) == "soft-blocked"
    assert parse_iw_reg("global\ncountry AU: DFS-ETSI\n\t(2400 - 2483 @ 40)\n") == "AU"
    assert parse_iw_reg("global\ncountry 00: DFS-UNSET\n") == "00"
    assert parse_nm_device_state("eth0:ethernet:unavailable\nwlan0:wifi:unavailable\n") == (
        "unavailable"
    )
    lines = wifi_status_lines(
        {
            "wlan0": "present",
            "nm_state": "disconnected",
            "nm_radio": "enabled",
            "rfkill_wlan": "unblocked",
            "country": "AU",
        },
        {
            "scan_count": 7,
            "scan_at": "t",
            "last_attempt": {
                "ssid": "Home",
                "ok": False,
                "reason": "wrong_password",
                "detail": "Secrets",
                "at": "t",
            },
        },
    )
    joined = "\n".join(lines)
    assert "7 networks" in joined
    assert "'Home' wrong_password: Secrets" in joined


def test_services_status_lines_show_daemon_and_group(tmp_path, monkeypatch) -> None:
    from freesounder_ble_provisioning import status

    st = tmp_path / "status.json"
    st.write_text(
        '{"room": "Kitchen", "household": "Home", "household_id": "abcd1234", '
        '"role": "member", "leader": "Living", "group": "Living + 1", "source": "radio", '
        '"play_state": "playing", "peers": 1, "airplay": 2}'
    )
    monkeypatch.setattr(status, "DAEMON_STATUS_PATH", st)

    def run(argv: list[str]) -> str:
        return "active" if argv[0] == "systemctl" else ""

    def http(url: str):
        return {"version": "0.6.0", "state": "ready"}

    live = status.read_services_live(
        run=run, http=http, port_open=lambda port, host="127.0.0.1": False
    )
    text = "\n".join(status.services_status_lines(live, "192.168.1.24"))
    assert "LAN IP      : 192.168.1.24" in text
    assert "player API  : up :8080 (v0.6.0, ready)" in text
    assert "room        : Kitchen  household: Home (abcd1234)" in text
    assert "group       : Living + 1 [member of Living]" in text
    assert "AirPlay 2" in text


def test_services_status_lines_when_nothing_answers(tmp_path, monkeypatch) -> None:
    from freesounder_ble_provisioning import status

    monkeypatch.setattr(status, "DAEMON_STATUS_PATH", tmp_path / "missing.json")
    live = status.read_services_live(
        run=lambda argv: "", http=lambda url: None, port_open=lambda p, h="127.0.0.1": False
    )
    text = "\n".join(status.services_status_lines(live, None))
    assert "LAN IP      : none yet" in text
    assert "player API  : not answering" in text


def test_services_status_lines_show_snapclient_and_snapserver(tmp_path, monkeypatch) -> None:
    from freesounder_ble_provisioning import status

    defaults = tmp_path / "snapclient"
    defaults.write_text('FS_PLAYER_ID=x\nSNAPCLIENT_URL="tcp://192.168.1.24:1704"\n')
    output = tmp_path / "audio-output"
    output.write_text("pcm=default:CARD=vc4hdmi1\ncard=1\nreason=HDMI1 (display connected)\n")
    monkeypatch.setattr(status, "SNAPCLIENT_DEFAULTS", defaults)
    monkeypatch.setattr(status, "AUDIO_OUTPUT_PATH", output)

    def run(argv: list[str]) -> str:
        if argv[0] == "journalctl":
            return "Connected to 192.168.1.24\nError: Failed to open PCM device freesounder"
        return "active" if argv[0] == "systemctl" else ""

    seen: list[tuple[int, str]] = []

    def port_open(port: int, host: str = "127.0.0.1") -> bool:
        seen.append((port, host))
        return port == 1705

    live = status.read_services_live(run=run, http=lambda url: None, port_open=port_open)
    text = "\n".join(status.services_status_lines(live, "192.168.1.30"))
    assert "snapclient  : unit active -> 192.168.1.24" in text
    assert "snapserver  : :1704 closed, :1705 open" in text
    assert "audio out   : default:CARD=vc4hdmi1 (HDMI1 (display connected))" in text
    assert "snap error  : Error: Failed to open PCM device freesounder" in text
    assert (1704, "192.168.1.24") in seen

"""NetworkManager helpers: redaction and nmcli mocked at the process boundary."""

from __future__ import annotations

from pathlib import Path

import pytest

from freesounder_ble_provisioning.network import (
    NetworkManagerPort,
    _Completed,
    classify_nmcli_error,
    redact,
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


@pytest.mark.asyncio
async def test_scan_parses_nmcli_table(tmp_path: Path) -> None:
    async def run(nmcli: str, *args: str) -> _Completed:
        del nmcli, args
        return _Completed(0, "", "Home:80:WPA2\nHome:40:WPA2\nCafe:30:\n:10:WPA2\n")

    port = NetworkManagerPort(connections_dir=tmp_path, run_fn=run, addrs_fn=lambda: [])
    nets = await port.scan_wifi()
    assert [n.ssid for n in nets] == ["Home", "Cafe"]
    assert nets[0].auth == "WPA2"
    assert nets[1].auth == "NO"
    assert nets[0].rssi > nets[1].rssi


@pytest.mark.asyncio
async def test_connect_writes_keyfile_without_logging_secret(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[tuple[str, ...]] = []

    async def run(nmcli: str, *args: str) -> _Completed:
        del nmcli
        calls.append(args)
        return _Completed(0, "")

    port = NetworkManagerPort(
        connections_dir=tmp_path,
        run_fn=run,
        addrs_fn=lambda: ["10.0.0.8"],
    )
    with caplog.at_level("INFO"):
        result = await port.connect_wifi("Home", "super-secret-psk")
    assert result.ok is True
    assert result.ip == "10.0.0.8"
    keyfile = tmp_path / "freesounder-wifi.nmconnection"
    assert keyfile.exists()
    text = keyfile.read_text(encoding="utf-8")
    assert "psk=super-secret-psk" in text
    assert "ssid=Home" in text
    # The PSK must not appear on the nmcli argv or in logs.
    flat = " ".join(" ".join(c) for c in calls)
    assert "super-secret-psk" not in flat
    assert "super-secret-psk" not in caplog.text
    assert ("connection", "reload") in calls
    assert "freesounder-wifi" in calls[-1]


@pytest.mark.asyncio
async def test_connect_maps_nmcli_failure(tmp_path: Path) -> None:
    async def run(nmcli: str, *args: str) -> _Completed:
        del nmcli
        if args[:1] == ("connection",) and "reload" in args:
            return _Completed(0, "")
        return _Completed(1, "Error: secrets were required for this connection")

    port = NetworkManagerPort(connections_dir=tmp_path, run_fn=run, addrs_fn=lambda: [])
    result = await port.connect_wifi("Home", "wrong")
    assert result.ok is False
    assert result.reason == "wrong_password"

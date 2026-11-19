"""select-audio-output: DAC/USB first, then the connected HDMI port, else null."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "images/player/pi-gen/stage-freesounder/01-filesystem/files/usr/lib/freesounder"
    / "select-audio-output"
)

PI5_HDMI_ONLY = (
    " 0 [vc4hdmi0       ]: vc4-hdmi - vc4-hdmi-0\n"
    "                      vc4-hdmi-0\n"
    " 1 [vc4hdmi1       ]: vc4-hdmi - vc4-hdmi-1\n"
    "                      vc4-hdmi-1\n"
)


def _run(
    tmp_path: Path, cards: str, hdmi: dict[int, str], extra: dict[str, str] | None = None
) -> dict[str, str]:
    drm = tmp_path / "drm"
    for port, status in hdmi.items():
        connector = drm / f"card1-HDMI-A-{port}"
        connector.mkdir(parents=True, exist_ok=True)
        (connector / "status").write_text(status + "\n")
    (tmp_path / "cards").write_text(cards)
    out = tmp_path / "audio-output"
    script = SCRIPT.read_text().replace("OUT=/etc/freesounder/audio-output", f"OUT={out}")
    env = {
        **os.environ,
        "FREESOUNDER_NO_RENDER": "1",
        "FREESOUNDER_ASOUND_CARDS": str(tmp_path / "cards"),
        "FREESOUNDER_DRM_DIR": str(drm),
        "FREESOUNDER_UPTIME": "999",
        "FREESOUNDER_AUDIO_DETECT": str(tmp_path / "detect.json"),
        "FREESOUNDER_BOOT_CONFIG": str(tmp_path / "config.txt"),
        "FREESOUNDER_PLAYER_ENV": str(tmp_path / "player.env"),
        **(extra or {}),
    }
    subprocess.run(["bash", "-c", script], check=True, env=env, capture_output=True)
    return dict(line.split("=", 1) for line in out.read_text().splitlines())


pytestmark = pytest.mark.skipif(
    not SCRIPT.is_file() or shutil.which("bash") is None, reason="needs repo + bash"
)


def test_pi5_hdmi1_connected(tmp_path):
    got = _run(tmp_path, PI5_HDMI_ONLY, {1: "disconnected", 2: "connected"})
    assert got["pcm"] == "hdmi:CARD=vc4hdmi1,DEV=0"
    assert got["kind"] == "hdmi"
    assert got["card"] == "1"


def test_pi5_hdmi0_connected(tmp_path):
    got = _run(tmp_path, PI5_HDMI_ONLY, {1: "connected", 2: "disconnected"})
    assert got["pcm"] == "hdmi:CARD=vc4hdmi0,DEV=0"


def test_no_display_falls_back_to_null_sink(tmp_path):
    got = _run(tmp_path, PI5_HDMI_ONLY, {1: "disconnected", 2: "disconnected"})
    assert got["pcm"] == "null"


def test_dac_wins_over_hdmi(tmp_path):
    cards = PI5_HDMI_ONLY + " 2 [sndrpihifiberry]: RPi-simple - snd_rpi_hifiberry_dacplus\n"
    got = _run(tmp_path, cards, {1: "connected"})
    assert got["pcm"] == "hw:CARD=sndrpihifiberry,DEV=0"
    assert got["card"] == "2"
    assert got["kind"] == "card"


def test_no_cards_at_all(tmp_path):
    assert _run(tmp_path, "--- no soundcards ---\n", {})["pcm"] == "null"


PI4 = (
    " 0 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones\n"
    "                      bcm2835 Headphones\n"
    " 1 [vc4hdmi0       ]: vc4-hdmi - vc4-hdmi-0\n"
    "                      vc4-hdmi-0\n"
    " 2 [vc4hdmi1       ]: vc4-hdmi - vc4-hdmi-1\n"
    "                      vc4-hdmi-1\n"
)
PIFI = " 3 [sndrpihifiberry]: RPi-simple - snd_rpi_hifiberry_dacplus\n"


def test_pi4_pifi_dac_wins_and_reports_detection(tmp_path):
    (tmp_path / "detect.json").write_text(
        '{\n  "detected": "PCM512x/TAS5756 at 0x4d (auto-detected, dtoverlay=hifiberry-dacplus)",\n'
        '  "expect_card": true,\n  "cards": ""\n}\n'
    )
    got = _run(tmp_path, PI4 + PIFI, {1: "disconnected", 2: "disconnected"})
    assert got["pcm"] == "hw:CARD=sndrpihifiberry,DEV=0"
    assert got["cards"] == "Headphones, vc4hdmi0, vc4hdmi1, sndrpihifiberry"
    assert got["detected"].startswith("PCM512x/TAS5756 at 0x4d")


def test_pi4_without_dac_uses_headphone_jack_not_null(tmp_path):
    got = _run(tmp_path, PI4, {1: "disconnected", 2: "disconnected"})
    assert got["pcm"] == "hw:CARD=Headphones,DEV=0"
    assert got["kind"] == "analog"


def test_usb_card_is_picked(tmp_path):
    usb = " 3 [Device         ]: USB-Audio - USB Audio Device\n"
    got = _run(tmp_path, PI4 + usb, {1: "connected"})
    assert got["pcm"] == "hw:CARD=Device,DEV=0"


def test_waits_for_an_expected_dac_early_in_boot(tmp_path):
    import time

    (tmp_path / "config.txt").write_text(
        "# --- freesounder-dac-start ---\n[all]\ndtoverlay=hifiberry-dacplus\n"
        "# --- freesounder-dac-end ---\n"
    )
    start = time.monotonic()
    got = _run(
        tmp_path,
        PI4,
        {1: "disconnected"},
        {"FREESOUNDER_UPTIME": "12", "FREESOUNDER_CARD_WAIT": "2"},
    )
    assert time.monotonic() - start >= 1.8
    # The DAC never came: the headphone jack, not the null sink.
    assert got["pcm"] == "hw:CARD=Headphones,DEV=0"


def test_no_cards_reason_says_kernel_listed_none(tmp_path):
    got = _run(tmp_path, "--- no soundcards ---\n", {}, {"FREESOUNDER_CARD_WAIT": "0"})
    assert got["pcm"] == "null"
    assert "kernel listed none" in got["reason"]


PIFI_CARDS = (
    " 0 [Headphones     ]: bcm2835_headphonbcm2835 Headphones - bcm2835 Headphones\n"
    " 1 [sndrpihifiberry]: HifiberryDacp - snd_rpi_hifiberry_dacplus\n"
)


def test_check_ok_is_recorded(tmp_path):
    got = _run(tmp_path, PIFI_CARDS, {}, {"FREESOUNDER_PROBE_RESULT": "ok"})
    assert got["pcm"] == "hw:CARD=sndrpihifiberry,DEV=0"
    assert got["check"] == "ok"
    assert "direct" not in got


def test_failed_open_records_the_exact_aplay_error(tmp_path):
    err = (
        "aplay: pcm_params.c:170: snd1_pcm_hw_param_get_min: "
        "Assertion `!snd_interval_empty(i)' failed."
    )
    got = _run(tmp_path, PIFI_CARDS, {}, {"FREESOUNDER_PROBE_RESULT": "fail:" + err})
    assert got["check"] == "failed: " + err
    assert "direct" not in got

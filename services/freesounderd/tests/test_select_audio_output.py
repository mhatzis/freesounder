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


def _run(tmp_path: Path, cards: str, hdmi: dict[int, str]) -> dict[str, str]:
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
    }
    subprocess.run(["bash", "-c", script], check=True, env=env, capture_output=True)
    return dict(line.split("=", 1) for line in out.read_text().splitlines())


pytestmark = pytest.mark.skipif(
    not SCRIPT.is_file() or shutil.which("bash") is None, reason="needs repo + bash"
)


def test_pi5_hdmi1_connected(tmp_path):
    got = _run(tmp_path, PI5_HDMI_ONLY, {1: "disconnected", 2: "connected"})
    assert got["pcm"] == "default:CARD=vc4hdmi1"
    assert got["card"] == "1"


def test_pi5_hdmi0_connected(tmp_path):
    got = _run(tmp_path, PI5_HDMI_ONLY, {1: "connected", 2: "disconnected"})
    assert got["pcm"] == "default:CARD=vc4hdmi0"


def test_no_display_falls_back_to_null_sink(tmp_path):
    got = _run(tmp_path, PI5_HDMI_ONLY, {1: "disconnected", 2: "disconnected"})
    assert got["pcm"] == "null"


def test_dac_wins_over_hdmi(tmp_path):
    cards = PI5_HDMI_ONLY + " 2 [sndrpihifiberry]: RPi-simple - snd_rpi_hifiberry_dacplus\n"
    got = _run(tmp_path, cards, {1: "connected"})
    assert got["pcm"] == "default:CARD=sndrpihifiberry"
    assert got["card"] == "2"


def test_no_cards_at_all(tmp_path):
    assert _run(tmp_path, "--- no soundcards ---\n", {})["pcm"] == "null"

"""DSP config generation."""

from __future__ import annotations

from freesounder_player_agent.dsp import (
    balance_gains,
    band_gains,
    render_alsa_conf,
    render_camilla_yaml,
    write_dsp_configs,
)
from freesounder_player_agent.models import PlayerSettings
from freesounder_player_agent.store import hostname_friendly


def test_band_gains_map_bass_treble_and_loudness() -> None:
    flat = band_gains(PlayerSettings())
    assert flat == [0.0] * 10

    bass = band_gains(PlayerSettings(bass=5, treble=-3))
    assert bass[0] == bass[1] == bass[2] == 5
    assert bass[7] == bass[8] == bass[9] == -3
    assert bass[4] == 0

    loud = band_gains(PlayerSettings(bass=2, loudness=True))
    assert loud[0] == 8  # 2 + 6
    assert loud[1] == 6
    assert loud[2] == 4
    assert loud[9] == 1.5


def test_balance_gains() -> None:
    assert balance_gains(0) == (1.0, 1.0)
    assert balance_gains(-10) == (1.0, 0.0)
    assert balance_gains(10) == (0.0, 1.0)
    left, right = balance_gains(5)
    assert left == 0.5
    assert right == 1.0


def test_alsa_and_camilla_render_and_write(tmp_path) -> None:
    settings = PlayerSettings(name="Kitchen", bass=3, treble=-2, balance=-4, loudness=True)
    alsa = render_alsa_conf(settings)
    assert "pcm.freesounder" in alsa
    assert "label Eq10X2" in alsa
    assert "ttable.0.0 1.0" in alsa
    camilla = render_camilla_yaml(settings)
    assert "type: Lowshelf" in camilla
    assert "type: Highshelf" in camilla

    alsa_path = tmp_path / "alsa" / "99-freesounder.conf"
    camilla_path = tmp_path / "camilla" / "camilladsp.yml"
    write_dsp_configs(settings, alsa_path=alsa_path, camilla_path=camilla_path)
    assert alsa_path.is_file()
    assert camilla_path.is_file()
    assert "Eq10X2" in alsa_path.read_text()


def test_hostname_friendly() -> None:
    assert hostname_friendly("Living Room") == "living-room"
    assert hostname_friendly("  Kitchen!  ") == "kitchen"
    assert hostname_friendly("") == "freesounder"

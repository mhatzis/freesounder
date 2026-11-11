"""DSP config generation."""

from __future__ import annotations

from freesounderd.dsp import (
    balance_gains,
    band_gains,
    render_alsa_conf,
    render_camilla_yaml,
    write_dsp_configs,
)
from freesounderd.models import PlayerSettings
from freesounderd.store import hostname_friendly


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
    # Balance -4 (left 1.0, right 0.6) times the EQ make-up gain.
    assert "ttable.0.0 0.66" in alsa
    assert "ttable.1.1 0.396" in alsa
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


def test_selected_audio_output_feeds_alsa_chain(tmp_path):
    from freesounderd.dsp import read_audio_output

    output = tmp_path / "audio-output"
    assert read_audio_output(output)["pcm"] == "default"
    output.write_text(
        "pcm=hdmi:CARD=vc4hdmi1,DEV=0\ncard=1\nkind=hdmi\nreason=HDMI1 (display connected)\n"
    )
    got = read_audio_output(output)
    assert (got["pcm"], got["card"], got["kind"]) == ("hdmi:CARD=vc4hdmi1,DEV=0", "1", "hdmi")

    alsa_path = tmp_path / "99-freesounder.conf"
    write_dsp_configs(
        PlayerSettings(),
        alsa_path=alsa_path,
        camilla_path=tmp_path / "camilla.yml",
        output_file=output,
    )
    text = alsa_path.read_text()
    assert 'pcm "hdmi:CARD=vc4hdmi1,DEV=0"' in text
    assert "rate 48000" in text and "format S16_LE" in text
    assert "card 1" in text

    output.write_text("pcm=null\ncard=0\n")
    write_dsp_configs(
        PlayerSettings(), alsa_path=alsa_path, camilla_path=tmp_path / "c.yml", output_file=output
    )
    assert 'slave.pcm "null"' in alsa_path.read_text()


def _block(text: str, name: str) -> str:
    start = text.index(f"pcm.{name} {{")
    return text[start : text.index("\n}", start)]


def test_ladspa_is_wrapped_in_plug_on_both_sides() -> None:
    """Regression (v0.6.0 silence): ladspa -> route directly cannot be opened."""

    text = render_alsa_conf(PlayerSettings(bass=3), slave_pcm="default:CARD=x")
    assert 'slave.pcm "freesounder_post"' in _block(text, "freesounder_eq")
    post = _block(text, "freesounder_post")
    assert "type plug" in post and 'slave.pcm "freesounder_bal"' in post
    assert "pcm.freesounder_selftest_eq" in text


def test_flat_tone_controls_skip_the_eq() -> None:
    text = render_alsa_conf(PlayerSettings(), slave_pcm="default:CARD=x")
    assert "ladspa" not in text
    assert "ttable.0.0 1.0" in text
    off = render_alsa_conf(PlayerSettings(bass=2), slave_pcm="default:CARD=x", eq=False)
    assert "ladspa" not in off
    assert "self-test" in off


def test_apply_falls_back_without_eq_when_the_chain_fails(tmp_path) -> None:
    from freesounderd.dsp import apply

    calls = []

    def fake_test():
        calls.append(1)
        return (len(calls) > 1, "Broken configuration" if len(calls) == 1 else "ok")

    alsa = tmp_path / "a.conf"
    status = apply(
        PlayerSettings(bass=4),
        alsa_path=alsa,
        camilla_path=tmp_path / "c.yml",
        status_path=tmp_path / "chain.json",
        test=fake_test,
    )
    assert status["eq"] == "bypassed" and status["ok"] is True
    assert status["eq_error"] == "Broken configuration"
    assert "ladspa" not in alsa.read_text()
    assert (tmp_path / "chain.json").is_file()


def test_real_alsa_chain_opens(tmp_path) -> None:
    """With alsa-utils and caps installed, the rendered chain really opens."""

    import os
    import shutil
    import subprocess

    import pytest

    if shutil.which("aplay") is None or not os.path.exists("/usr/lib/ladspa/caps.so"):
        pytest.skip("needs aplay and caps")
    for kind, pcm in (("card", "null"), ("hdmi", "null")):
        conf = render_alsa_conf(PlayerSettings(bass=4, balance=2), slave_pcm=pcm, kind=kind)
        home = tmp_path / kind
        home.mkdir()
        (home / ".asoundrc").write_text(conf)
        for device in ("freesounder", "freesounder_selftest"):
            proc = subprocess.run(
                [
                    "aplay",
                    "-q",
                    "-D",
                    device,
                    "-t",
                    "raw",
                    "-f",
                    "S16_LE",
                    "-r",
                    "44100",
                    "-c",
                    "2",
                ],
                input=b"\0" * 17640,
                env={**os.environ, "HOME": str(home)},
                capture_output=True,
                check=False,
            )
            assert proc.returncode == 0, proc.stderr.decode()

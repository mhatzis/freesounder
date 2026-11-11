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
    assert "pcm.freesounder {" in alsa
    assert "type fsdsp" in alsa
    assert "ladspa" not in alsa
    # The live chain does not depend on the tone settings.
    assert alsa == render_alsa_conf(PlayerSettings())
    camilla = render_camilla_yaml(settings)
    assert "type: Lowshelf" in camilla

    alsa_path = tmp_path / "alsa" / "99-freesounder.conf"
    camilla_path = tmp_path / "camilla" / "camilladsp.yml"
    params = tmp_path / "run" / "dsp-params"
    status = tmp_path / "run" / "chain.json"
    kw = {"alsa_path": alsa_path, "camilla_path": camilla_path, "params_path": params}
    assert write_dsp_configs(settings, status_path=status, **kw) is True
    assert params.read_text() == "fsdsp1 3 -2 -4 1\n"
    # A tone change only rewrites the params file, not the ALSA config.
    assert (
        write_dsp_configs(settings.model_copy(update={"bass": -5}), status_path=status, **kw)
        is False
    )
    assert params.read_text() == "fsdsp1 -5 -2 -4 1\n"


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
    kw = {
        "camilla_path": tmp_path / "camilla.yml",
        "params_path": tmp_path / "p",
        "status_path": tmp_path / "s.json",
    }
    write_dsp_configs(PlayerSettings(), alsa_path=alsa_path, output_file=output, **kw)
    text = alsa_path.read_text()
    assert 'pcm "hdmi:CARD=vc4hdmi1,DEV=0"' in text
    assert "rate 48000" in text and "format S16_LE" in text
    assert "card 1" in text

    output.write_text("pcm=null\ncard=0\n")
    assert write_dsp_configs(PlayerSettings(), alsa_path=alsa_path, output_file=output, **kw)
    assert 'slave.pcm "null"' in alsa_path.read_text()


def test_fallback_chain_bakes_balance() -> None:
    text = render_alsa_conf(PlayerSettings(balance=5), slave_pcm="default:CARD=x", live=False)
    assert "type fsdsp" not in text
    assert "ttable.0.0 0.5" in text
    assert "FALLBACK" in text


def test_apply_falls_back_when_the_live_chain_fails(tmp_path) -> None:
    from freesounderd.dsp import apply, chain_is_live

    calls = []

    def fake_test():
        calls.append(1)
        return (len(calls) > 1, "Unknown PCM type fsdsp" if len(calls) == 1 else "ok")

    alsa = tmp_path / "a.conf"
    status_path = tmp_path / "chain.json"
    status = apply(
        PlayerSettings(bass=4),
        alsa_path=alsa,
        camilla_path=tmp_path / "c.yml",
        status_path=status_path,
        params_path=tmp_path / "p",
        output_file=tmp_path / "none",
        test=fake_test,
    )
    assert status["dsp"] == "fallback" and status["ok"] is True
    assert status["dsp_error"] == "Unknown PCM type fsdsp"
    assert "type fsdsp" not in alsa.read_text()
    assert chain_is_live(status_path) is False

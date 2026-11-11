"""Audio output state and the test tone."""

from __future__ import annotations

import io
import wave

from freesounderd.audio import audio_state, hw_state, tone_wav

from .conftest import Lan, settle
from .test_daemon import setup_household


def test_tone_wav_is_three_seconds_of_stereo() -> None:
    with wave.open(io.BytesIO(tone_wav(3.0)), "rb") as w:
        assert w.getnchannels() == 2
        assert w.getframerate() == 44100
        assert w.getnframes() == 3 * 44100
        frames = w.readframes(w.getnframes())
    assert max(frames) > 0


def test_hw_state_reads_proc_asound(tmp_path) -> None:
    sub = tmp_path / "card1" / "pcm0p" / "sub0"
    sub.mkdir(parents=True)
    (sub / "status").write_text("closed\n")
    (sub / "hw_params").write_text("closed\n")
    assert hw_state("1", root=tmp_path)["state"] == "closed"
    (sub / "status").write_text("state: RUNNING\nowner_pid   : 42\n")
    (sub / "hw_params").write_text(
        "access: MMAP_INTERLEAVED\nformat: IEC958_SUBFRAME_LE\nrate: 48000 (48000/1)\n"
    )
    got = hw_state("1", root=tmp_path)
    assert got["state"] == "RUNNING"
    assert got["format"] == "IEC958_SUBFRAME_LE"
    assert hw_state("7", root=tmp_path)["state"] == "missing"


def test_audio_state_combines_output_and_chain(tmp_path) -> None:
    out = tmp_path / "audio-output"
    out.write_text("pcm=hdmi:CARD=vc4hdmi0,DEV=0\ncard=0\nkind=hdmi\nreason=HDMI0\n")
    chain = tmp_path / "chain.json"
    chain.write_text('{"eq": "on", "ok": true, "detail": "ok"}')
    state = audio_state(out, chain, root=tmp_path)
    assert state["output"]["kind"] == "hdmi"
    assert state["chain"]["ok"] is True
    assert state["hw"]["state"] == "missing"


async def test_test_tone_is_forwarded_to_the_room(lan: Lan, make_player) -> None:
    a, b = await setup_household(make_player, ("10.0.0.1", "Pi 5"), ("10.0.0.2", "Pi 4"))
    await settle(lan, [a, b])
    res = await a.client.post(f"/api/v2/rooms/{b.id}/test-tone")
    assert res.status_code == 200, res.text
    assert res.json()["ok"] is True
    assert ("test-tone", "3.0") in b.engine.system.calls
    assert ("test-tone", "3.0") not in a.engine.system.calls
    res = await a.client.get(f"/api/v2/rooms/{b.id}/audio")
    assert res.status_code == 200

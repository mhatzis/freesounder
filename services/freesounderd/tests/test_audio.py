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


async def test_reboot_is_forwarded_to_the_room(lan: Lan, make_player) -> None:
    import asyncio

    a, b = await setup_household(make_player, ("10.0.0.1", "Pi 5"), ("10.0.0.2", "Pi 4"))
    await settle(lan, [a, b])
    res = await a.client.post(f"/api/v2/rooms/{b.id}/reboot")
    assert res.status_code == 200, res.text
    await asyncio.sleep(1.2)
    assert ("systemctl", "reboot") in b.engine.system.calls
    assert ("systemctl", "reboot") not in a.engine.system.calls


async def test_factory_reset_is_forwarded_and_needs_confirm(lan: Lan, make_player) -> None:
    a, b = await setup_household(make_player, ("10.0.0.1", "Pi 5"), ("10.0.0.2", "Pi 4"))
    await settle(lan, [a, b])
    res = await a.client.post(f"/api/v2/rooms/{b.id}/factory-reset", json={})
    assert res.status_code == 400
    res = await a.client.post(f"/api/v2/rooms/{b.id}/factory-reset", json={"confirm": True})
    assert res.status_code == 200, res.text
    assert ("factory-reset", "app") in b.engine.system.calls
    assert ("factory-reset", "app") not in a.engine.system.calls


def test_audio_output_carries_detection(tmp_path) -> None:
    out = tmp_path / "audio-output"
    out.write_text(
        "pcm=default:CARD=sndrpihifiberry\ncard=3\nkind=card\nreason=sound card sndrpihifiberry\n"
        "cards=Headphones, vc4hdmi0, sndrpihifiberry\ndetected=PCM512x at 0x4d\n"
        "check=failed: aplay: boom\ndirect=1\n"
    )
    got = audio_state(out, tmp_path / "none.json", root=tmp_path)["output"]
    assert got["cards"] == "Headphones, vc4hdmi0, sndrpihifiberry"
    assert got["detected"] == "PCM512x at 0x4d"
    assert got["check"] == "failed: aplay: boom"
    assert got["direct"] == "1"

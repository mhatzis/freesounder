"""Tone changes apply live: no snapclient restart, and (with real ALSA) no re-open."""

from __future__ import annotations

import math
import os
import shutil
import struct
import subprocess
import time
from pathlib import Path

import pytest

from freesounderd.dsp import render_alsa_conf, write_params
from freesounderd.models import PlayerSettings

from .conftest import Lan, settle
from .test_daemon import setup_household

FSDSP_C = Path(__file__).resolve().parents[2] / "fsdsp" / "fsdsp.c"


async def test_tone_change_does_not_restart_snapclient(lan: Lan, make_player) -> None:
    (a,) = await setup_household(make_player, ("10.0.0.1", "Pi 5"))
    await settle(lan, [a])
    before = [c for c in a.engine.system.calls if "snapclient.service" in c]
    for value in (2, 4, -3):
        res = await a.client.patch(
            f"/api/v2/rooms/{a.id}/settings", json={"bass": value, "balance": value / 2}
        )
        assert res.status_code == 200, res.text
    await settle(lan, [a])
    after = [c for c in a.engine.system.calls if "snapclient.service" in c]
    assert not [c for c in after[len(before) :] if c[1] == "restart"]
    params = a.engine.settings.run_dir / "dsp-params"
    assert params.read_text() == "fsdsp1 -3 0 -1.5 0\n"


def _alsa_headers() -> bool:
    return Path("/usr/include/alsa/asoundlib.h").is_file() or bool(os.environ.get("ALSA_CFLAGS"))


@pytest.fixture(scope="module")
def fsdsp_so(tmp_path_factory) -> Path:
    if shutil.which("gcc") is None or shutil.which("aplay") is None or not _alsa_headers():
        pytest.skip("needs gcc, ALSA headers and aplay")
    out = tmp_path_factory.mktemp("fsdsp") / "libasound_module_pcm_fsdsp.so"
    cflags = os.environ.get("ALSA_CFLAGS", "").split()
    libs = os.environ.get("ALSA_LIBS", "-lasound").split()
    cmd = ["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-fPIC", "-DPIC", "-shared", *cflags]
    subprocess.run([*cmd, "-o", str(out), str(FSDSP_C), *libs, "-lm"], check=True)
    return out


def _tone(freq: float, start: int, frames: int, rate: int = 44100) -> bytes:
    return b"".join(
        struct.pack("<hh", *(int(10000 * math.sin(2 * math.pi * freq * (start + i) / rate)),) * 2)
        for i in range(frames)
    )


def _rms(values) -> float:
    return math.sqrt(sum(v * v for v in values) / max(1, len(values))) / 10000 * math.sqrt(2)


@pytest.mark.parametrize("kind", ["card", "hdmi"])
def test_params_change_live_in_one_alsa_session(tmp_path, fsdsp_so, kind) -> None:
    """One aplay process plays through the real chain while settings change."""

    params = tmp_path / "dsp-params"
    raw = tmp_path / "out.raw"
    conf = render_alsa_conf(slave_pcm="capture", kind=kind, params_path=params)
    conf += f"""
pcm_type.fsdsp {{ lib "{fsdsp_so}" }}
pcm.capture {{ type plug slave {{ pcm "capture_file" format S16_LE }} }}
pcm.capture_file {{ type file slave.pcm "null" file "{raw}" format "raw" }}
"""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".asoundrc").write_text(conf)
    env = {**os.environ, "HOME": str(home)}
    write_params(PlayerSettings(), params)
    selftest = subprocess.run(
        ["aplay", "-q", "-D", "freesounder_selftest", "-t", "raw", "-f", "S16_LE"]
        + ["-r", "44100", "-c", "2"],
        input=b"\0" * 17640,
        env=env,
        capture_output=True,
        check=False,
    )
    assert selftest.returncode == 0, selftest.stderr.decode()

    proc = subprocess.Popen(
        ["aplay", "-D", "freesounder", "-t", "raw", "-f", "S16_LE", "-r", "44100", "-c", "2"],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    assert proc.stdin is not None
    chunk, chunks = 4410, 30
    for c in range(chunks):
        if c == chunks // 2:
            write_params(PlayerSettings(balance=5), params)  # left at half level
        proc.stdin.write(_tone(1000, c * chunk, chunk))
        proc.stdin.flush()
        time.sleep(0.01)
    proc.stdin.close()
    err = proc.stderr.read().decode() if proc.stderr else ""
    assert proc.wait(timeout=20) == 0, err
    assert "underrun" not in err.lower()

    data = raw.read_bytes()
    samples = struct.unpack(f"<{len(data) // 2}h", data[: len(data) // 2 * 2])
    left, right = samples[0::2], samples[1::2]
    half = len(left) // 2
    rate_scale = 48000 / 44100 if kind == "hdmi" else 1.0
    guard = int(6000 * rate_scale)
    first_l = _rms(left[1000 : half - guard])
    second_l = _rms(left[half + guard : -guard])
    second_r = _rms(right[half + guard : -guard])
    assert abs(first_l - 1.0) < 0.03  # flat is unity
    assert abs(second_l - 0.5) < 0.03  # balance applied without re-opening
    assert abs(second_r - 1.0) < 0.03
    # No click: the largest sample step stays at the sine's own maximum.
    steps = max(abs(left[i] - left[i - 1]) for i in range(1000, len(left) - guard))
    assert steps <= 10000 * 2 * math.sin(math.pi * 1000 / (44100 * rate_scale)) * 1.05 + 2


def test_loopback_realtime_no_underrun(tmp_path, fsdsp_so) -> None:
    """Real-time device (snd-aloop) when available: params change, no xrun."""

    if not Path("/proc/asound/Loopback").exists():
        pytest.skip("snd-aloop not loaded")
    params = tmp_path / "dsp-params"
    conf = render_alsa_conf(slave_pcm="hw:Loopback,0,0", kind="card", params_path=params)
    conf += f'\npcm_type.fsdsp {{ lib "{fsdsp_so}" }}\n'
    home = tmp_path / "home"
    home.mkdir()
    (home / ".asoundrc").write_text(conf)
    env = {**os.environ, "HOME": str(home)}
    write_params(PlayerSettings(), params)
    proc = subprocess.Popen(
        ["aplay", "-D", "freesounder", "-t", "raw", "-f", "S16_LE", "-r", "44100", "-c", "2"],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    assert proc.stdin is not None
    chunk = 4410
    for c in range(30):
        if c % 5 == 4:
            write_params(PlayerSettings(bass=c % 10, treble=-(c % 7), balance=c % 3), params)
        proc.stdin.write(_tone(440, c * chunk, chunk))
    proc.stdin.close()
    err = proc.stderr.read().decode() if proc.stderr else ""
    assert proc.wait(timeout=30) == 0, err
    assert "underrun" not in err.lower(), err

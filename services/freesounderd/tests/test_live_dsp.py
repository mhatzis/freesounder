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


FSDSP_BENCH = FSDSP_C.parent / "bench" / "fsdsp_bench.c"


@pytest.fixture(scope="module")
def bench_bin(tmp_path_factory, fsdsp_so) -> Path:
    out = tmp_path_factory.mktemp("bench") / "fsdsp_bench"
    cflags = os.environ.get("ALSA_CFLAGS", "").split()
    libs = os.environ.get("ALSA_LIBS", "-lasound").split()
    cmd = ["gcc", "-O2", "-Wall", "-Wextra", "-Werror", *cflags, "-o", str(out), str(FSDSP_BENCH)]
    subprocess.run([*cmd, *libs, "-lm", "-lpthread"], check=True)
    return out


def _chain_home(tmp_path: Path, fsdsp_so: Path, kind: str, sink: str) -> tuple[Path, dict]:
    params = tmp_path / "dsp-params"
    raw = tmp_path / "out.raw"
    conf = render_alsa_conf(slave_pcm=sink, kind=kind, params_path=params)
    conf += f"""
pcm_type.fsdsp {{ lib "{fsdsp_so}" }}
pcm.capture {{ type plug slave {{ pcm "capture_file" format S16_LE }} }}
pcm.capture_file {{ type file slave.pcm "null" file "{raw}" format "raw" }}
"""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / ".asoundrc").write_text(conf)
    return params, {**os.environ, "HOME": str(home)}


@pytest.mark.parametrize("kind", ["card", "hdmi"])
def test_parameter_sweep_stays_far_inside_the_realtime_budget(tmp_path, fsdsp_so, bench_bin, kind):
    """Rewrite the params every 5 ms for 20 s of audio: no xrun, tiny per-period cost."""

    params, env = _chain_home(tmp_path, fsdsp_so, kind, "null")
    write_params(PlayerSettings(), params)
    proc = subprocess.run(
        [str(bench_bin), "freesounder", str(params), "20", "5"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    stats = dict(item.split("=") for item in proc.stdout.split())
    assert int(stats["xruns"]) == 0 and int(stats["errors"]) == 0
    # A 1024-frame period has ~23 ms; the chain must use a small fraction of it.
    assert float(stats["p99_ms"]) < 0.1 * float(stats["budget_ms"]), stats
    print(kind, proc.stdout.strip())


def test_sweep_has_no_clicks_and_limiter_prevents_clipping(tmp_path, fsdsp_so, bench_bin):
    params, env = _chain_home(tmp_path, fsdsp_so, "card", "capture")
    write_params(PlayerSettings(), params)
    proc = subprocess.run(
        [str(bench_bin), "freesounder", str(params), "10", "5"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = (tmp_path / "out.raw").read_bytes()
    samples = struct.unpack(f"<{len(data) // 2}h", data[: len(data) // 2 * 2])
    left = samples[0::2]
    # Input: 12000 * sin(1 kHz) + 6000 * sin(60 Hz); its own largest step is ~1761.
    worst = max(abs(left[i] - left[i - 1]) for i in range(1, len(left)))
    assert worst < 1761 * 1.2, worst

    # Full-scale input with every boost on: the limiter keeps it below 0 dBFS.
    (tmp_path / "out.raw").unlink()
    write_params(PlayerSettings(bass=10, treble=10, loudness=True), params)
    loud = b"".join(
        struct.pack("<hh", *(int(32000 * math.sin(2 * math.pi * 80 * i / 44100)),) * 2)
        for i in range(44100 * 2)
    )
    play = subprocess.run(
        ["aplay", "-q", "-D", "freesounder", "-t", "raw", "-f", "S16_LE", "-r", "44100", "-c", "2"],
        input=loud,
        env=env,
        capture_output=True,
        check=False,
    )
    assert play.returncode == 0, play.stderr.decode()
    data = (tmp_path / "out.raw").read_bytes()
    out = struct.unpack(f"<{len(data) // 2}h", data[: len(data) // 2 * 2])
    assert max(abs(v) for v in out) <= int(32767 * 0.95)


def _play_capture(
    tmp_path: Path, env: dict, settings: PlayerSettings, params: Path, freq: float
) -> float:
    """RMS gain (dB) of a -10 dBFS tone through the live chain, last second."""

    raw = tmp_path / "out.raw"
    raw.unlink(missing_ok=True)
    write_params(settings, params)
    frames = 44100 * 2
    play = subprocess.run(
        ["aplay", "-q", "-D", "freesounder", "-t", "raw", "-f", "S16_LE", "-r", "44100", "-c", "2"],
        input=_tone(freq, 0, frames),
        env=env,
        capture_output=True,
        check=False,
    )
    assert play.returncode == 0, play.stderr.decode()
    data = raw.read_bytes()
    out = struct.unpack(f"<{len(data) // 2}h", data[: len(data) // 2 * 2])
    return 20 * math.log10(_rms(out[0::2][-44100:]))


def test_bass_and_loudness_boost_bass_and_leave_mids_alone(tmp_path, fsdsp_so) -> None:
    """Bass +6 / Bass boost raise 60-100 Hz and do not turn the mids down.

    Before, a fixed pre-gain of half the boost made 1 kHz 3 dB quieter and left
    100 Hz lower than flat with Bass boost on, so the controls sounded reversed.
    """

    params, env = _chain_home(tmp_path, fsdsp_so, "card", "capture")
    cases = {
        "flat": PlayerSettings(),
        "bass": PlayerSettings(bass=6),
        "loud": PlayerSettings(loudness=True),
        "cut": PlayerSettings(bass=-6),
    }
    raw = {
        (name, freq): _play_capture(tmp_path, env, settings, params, freq)
        for name, settings in cases.items()
        for freq in (60, 100, 1000)
    }
    # Relative to flat at the same frequency.
    gain = {key: value - raw[("flat", key[1])] for key, value in raw.items()}
    print({f"{k[0]}@{k[1]}": round(v, 1) for k, v in gain.items() if k[0] != "flat"})
    for name in ("bass", "loud", "cut"):
        assert abs(gain[(name, 1000)]) < 0.5, gain
    assert gain[("bass", 60)] > 4.5 and gain[("bass", 100)] > 3.5, gain
    assert gain[("loud", 60)] > 4.0 and gain[("loud", 100)] > 2.0, gain
    assert gain[("cut", 60)] < -4.5, gain

"""Local audio output: what the ALSA chain plays to, its live state, and a test tone."""

from __future__ import annotations

import asyncio
import io
import json
import math
import shutil
import struct
import wave
from pathlib import Path
from typing import Any

from freesounderd.dsp import AUDIO_OUTPUT_FILE, CHAIN_STATUS_FILE, read_audio_output

PROC_ASOUND = Path("/proc/asound")


def tone_wav(seconds: float = 3.0, rate: int = 44100) -> bytes:
    """Left 440 Hz, then right 660 Hz, then both 880 Hz, at -12 dBFS with soft edges."""

    frames = int(seconds * rate)
    third = frames // 3
    amp = 32767 * 0.25
    buf = bytearray()
    for i in range(frames):
        edges = (i / 2000, (frames - i) / 2000, abs(i - third) / 400, abs(i - 2 * third) / 400)
        edge = min(1.0, *edges)
        t = i / rate
        if i < third:
            left, right = math.sin(2 * math.pi * 440 * t), 0.0
        elif i < 2 * third:
            left, right = 0.0, math.sin(2 * math.pi * 660 * t)
        else:
            left = right = math.sin(2 * math.pi * 880 * t)
        gain = amp * max(0.0, min(1.0, edge))
        buf += struct.pack("<hh", int(left * gain), int(right * gain))
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(buf))
    return out.getvalue()


def hw_state(card: str, device: int = 0, root: Path = PROC_ASOUND) -> dict[str, Any]:
    """Kernel view of the output: closed / RUNNING, and the negotiated format."""

    base = root / f"card{card}" / f"pcm{device}p" / "sub0"
    out: dict[str, Any] = {"device": f"card{card} pcm{device}p"}
    try:
        status = (base / "status").read_text(encoding="utf-8").strip()
    except OSError:
        out["state"] = "missing"
        return out
    if status == "closed":
        out["state"] = "closed"
    else:
        for line in status.splitlines():
            key, _, value = line.partition(":")
            if key.strip() == "state":
                out["state"] = value.strip()
    try:
        params = (base / "hw_params").read_text(encoding="utf-8").strip()
    except OSError:
        params = ""
    if params and params != "closed":
        for line in params.splitlines():
            key, _, value = line.partition(":")
            if key.strip() in {"format", "rate", "channels"}:
                out[key.strip()] = value.strip()
    return out


def audio_state(
    output_file: Path = AUDIO_OUTPUT_FILE,
    chain_file: Path = CHAIN_STATUS_FILE,
    root: Path = PROC_ASOUND,
) -> dict[str, Any]:
    out: dict[str, Any] = {"output": read_audio_output(output_file)}
    try:
        chain = json.loads(chain_file.read_text(encoding="utf-8"))
        out["chain"] = {k: chain.get(k) for k in ("dsp", "ok", "detail", "dsp_error")}
    except (OSError, ValueError):
        out["chain"] = None
    if out["output"]["kind"] != "null":
        out["hw"] = hw_state(out["output"]["card"], root=root)
    return out


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


async def play_tone(
    path: Path, *, device: str = "freesounder", aplay: str = "aplay", seconds: float = 3.0
) -> tuple[bool, str]:
    """Play the tone through the full FreeSounder chain (EQ, balance, output)."""

    if shutil.which(aplay) is None:
        return False, "aplay is not installed"
    data = await asyncio.to_thread(tone_wav, seconds)
    await asyncio.to_thread(_write, path, data)
    try:
        proc = await asyncio.create_subprocess_exec(
            aplay,
            "-q",
            "-D",
            device,
            str(path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await asyncio.wait_for(proc.communicate(), timeout=seconds + 10)
    except (OSError, TimeoutError) as exc:
        return False, f"aplay failed: {exc}"
    text = (err or b"").decode(errors="replace").strip().splitlines()
    if proc.returncode:
        return False, text[-1] if text else f"aplay exited {proc.returncode}"
    return True, "played"

"""ALSA output chain and live tone settings.

snapclient plays to the ALSA PCM `freesounder`:

    plug (-> 48 kHz on HDMI) -> fsdsp (live bass/treble/loudness/balance)
        -> plug -> output device (DAC, USB, hdmi:CARD=vc4hdmiN, or null)

`fsdsp` is our own small ALSA filter plugin (services/fsdsp). It re-reads
/run/freesounder/dsp-params while audio plays, so tone changes never
re-open the device or restart snapclient. The ALSA config itself only
changes when the output device changes. If the plugin is missing or fails
its self-test, a fallback chain with static balance is used (that one does
need a snapclient restart to change).
"""

from __future__ import annotations

from pathlib import Path

from freesounderd.models import PlayerSettings

# Eq10 / Eq10X2 centre frequencies (Hz).
EQ_BANDS_HZ = (31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
BASS_BANDS = (0, 1, 2)  # 31, 63, 125 Hz
TREBLE_BANDS = (7, 8, 9)  # 4k, 8k, 16k Hz
# Extra dB applied to low bands (and a little air at 16 kHz) when loudness is on.
LOUDNESS_LOW = (6.0, 4.0, 2.0)
LOUDNESS_AIR = 1.5


def band_gains(settings: PlayerSettings) -> list[float]:
    """Ten EQ band gains in dB for Eq10X2."""

    gains = [0.0] * 10
    for index in BASS_BANDS:
        gains[index] += settings.bass
    for index in TREBLE_BANDS:
        gains[index] += settings.treble
    if settings.loudness:
        for index, extra in zip(BASS_BANDS, LOUDNESS_LOW, strict=True):
            gains[index] += extra
        gains[9] += LOUDNESS_AIR
    return [round(value, 2) for value in gains]


def balance_gains(balance: float) -> tuple[float, float]:
    """Linear left/right gains for a balance of -10 (left) to +10 (right)."""

    clamped = max(-10.0, min(10.0, balance))
    left = 1.0 if clamped <= 0 else (10.0 - clamped) / 10.0
    right = 1.0 if clamped >= 0 else (10.0 + clamped) / 10.0
    return (round(left, 4), round(right, 4))


AUDIO_OUTPUT_FILE = Path("/etc/freesounder/audio-output")
CHAIN_STATUS_FILE = Path("/run/freesounder/audio-chain.json")
PARAMS_FILE = Path("/run/freesounder/dsp-params")
# vc4-hdmi only takes IEC958 subframes; the `hdmi:` PCM (iec958 plugin)
# accepts S16_LE/S32_LE, and 48 kHz is the one rate every HDMI sink takes.
HDMI_RATE = 48000
HDMI_FORMAT = "S16_LE"


def read_audio_output(path: Path = AUDIO_OUTPUT_FILE) -> dict[str, str]:
    """What select-audio-output chose: pcm, card, kind (card/hdmi/analog/null), reason.

    Without the file the chain uses ALSA's default device and card 0.
    """

    out = {"pcm": "default", "card": "0", "kind": "", "reason": ""}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() in out and value.strip():
                out[key.strip()] = value.strip()
    except OSError:
        pass
    if not out["kind"]:
        out["kind"] = "hdmi" if out["pcm"].startswith("hdmi:") else "card"
    return out


def render_params(settings: PlayerSettings) -> str:
    """The line fsdsp reads: tag, bass dB, treble dB, balance, loudness."""

    return (
        f"fsdsp1 {float(settings.bass):g} {float(settings.treble):g} "
        f"{float(settings.balance):g} {1 if settings.loudness else 0}\n"
    )


def write_params(settings: PlayerSettings, path: Path = PARAMS_FILE) -> bool:
    """Atomically replace the live params file. Returns True if it changed."""

    text = render_params(settings)
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return True


def _hw_block(name: str, slave_pcm: str, kind: str) -> str:
    if kind == "hdmi" or slave_pcm.startswith("hdmi:"):
        return f"""pcm.{name} {{
    type plug
    slave {{
        pcm "{slave_pcm}"
        format {HDMI_FORMAT}
        rate {HDMI_RATE}
        channels 2
    }}
}}"""
    return f"""pcm.{name} {{
    type plug
    slave.pcm "{slave_pcm}"
}}"""


def render_alsa_conf(
    settings: PlayerSettings | None = None,
    *,
    slave_pcm: str = "default",
    ctl_card: str = "0",
    kind: str = "",
    live: bool = True,
    params_path: Path = PARAMS_FILE,
) -> str:
    """ALSA PCM `freesounder` plus `freesounder_selftest` (same chain into null).

    With live=True the text does not depend on the tone settings at all.
    """

    hdmi = kind == "hdmi" or slave_pcm.startswith("hdmi:")

    def entry(prefix: str, target: str) -> str:
        # HDMI: resample once at the top so the chain runs at 48 kHz.
        if hdmi and prefix == "freesounder":
            return f"""pcm.{prefix} {{
    type plug
    rate_converter [ "speexrate" "linear" ]
    slave {{
        pcm "{target}"
        rate {HDMI_RATE}
        channels 2
    }}
}}"""
        return f"""pcm.{prefix} {{
    type plug
    rate_converter [ "speexrate" "linear" ]
    slave.pcm "{target}"
}}"""

    def chain(prefix: str, out: str) -> str:
        if live:
            return f"""{entry(prefix, prefix + "_dsp")}

pcm.{prefix}_dsp {{
    type fsdsp
    params "{params_path}"
    slave.pcm "{out}"
}}"""
        left, right = balance_gains(settings.balance if settings else 0)
        return f"""{entry(prefix, prefix + "_bal")}

pcm.{prefix}_bal {{
    type route
    slave.pcm "{out}"
    ttable.0.0 {left}
    ttable.1.1 {right}
}}"""

    mode = (
        "live (fsdsp, changes apply while playing)"
        if live
        else "FALLBACK: static balance, no tone controls (fsdsp failed its self-test)"
    )
    return f"""# Generated by freesounder. Do not edit — use the FreeSounder app or
# freesounder-setup. snapclient should use --soundcard freesounder.
# Output: {slave_pcm} ({kind or "card"}). DSP: {mode}.
{chain("freesounder", "freesounder_hw")}

{_hw_block("freesounder_hw", slave_pcm, kind)}

ctl.freesounder {{
    type hw
    card {ctl_card}
}}

{chain("freesounder_selftest", "freesounder_selftest_hw")}

pcm.freesounder_selftest_hw {{
    type plug
    slave.pcm "null"
}}
"""


def chain_is_live(status_path: Path = CHAIN_STATUS_FILE) -> bool:
    """False when the last self-test fell back to the static chain."""

    import json

    try:
        return json.loads(status_path.read_text(encoding="utf-8")).get("dsp") != "fallback"
    except (OSError, ValueError):
        return True


def render_camilla_yaml(settings: PlayerSettings) -> str:
    """CamillaDSP 2-channel config matching the ALSA tone controls."""

    left, right = balance_gains(settings.balance)
    bass = settings.bass + (4.0 if settings.loudness else 0.0)
    treble = settings.treble + (1.0 if settings.loudness else 0.0)
    # A high-pass-safe shelf pair is enough to mirror bass/treble sliders.
    return f"""# Generated by freesounder. Loaded only when camilladsp is installed.
devices:
  samplerate: 48000
  chunksize: 1024
  queuelimit: 4
  capture:
    type: Alsa
    channels: 2
    device: "hw:Loopback,1"
    format: S16LE
  playback:
    type: Alsa
    channels: 2
    device: "default"
    format: S16LE
filters:
  bass:
    type: Biquad
    parameters:
      type: Lowshelf
      freq: 120
      slope: 6
      gain: {bass}
  treble:
    type: Biquad
    parameters:
      type: Highshelf
      freq: 4000
      slope: 6
      gain: {treble}
  left_gain:
    type: Gain
    parameters:
      gain: {round(_linear_to_db(left), 2)}
      inverted: false
  right_gain:
    type: Gain
    parameters:
      gain: {round(_linear_to_db(right), 2)}
      inverted: false
pipeline:
  - type: Filter
    channel: 0
    names: [bass, treble, left_gain]
  - type: Filter
    channel: 1
    names: [bass, treble, right_gain]
"""


def _linear_to_db(linear: float) -> float:
    if linear <= 0:
        return -120.0
    import math

    return 20.0 * math.log10(linear)


def write_dsp_configs(
    settings: PlayerSettings,
    *,
    alsa_path,
    camilla_path,
    slave_pcm: str | None = None,
    output_file: Path = AUDIO_OUTPUT_FILE,
    live: bool | None = None,
    params_path: Path = PARAMS_FILE,
    status_path: Path = CHAIN_STATUS_FILE,
) -> bool:
    """Write the live params, and the ALSA config only if its text changed.

    Returns True when the ALSA config changed (snapclient must re-open the PCM).
    """

    if live is None:
        live = chain_is_live(status_path)
    try:
        write_params(settings, params_path)
    except OSError:
        pass
    output = read_audio_output(output_file)
    chosen_pcm = slave_pcm if slave_pcm is not None else output["pcm"]
    text = render_alsa_conf(
        settings,
        slave_pcm=chosen_pcm,
        ctl_card=output["card"],
        kind=output["kind"],
        live=live,
        params_path=params_path,
    )
    camilla_path.parent.mkdir(parents=True, exist_ok=True)
    camilla_path.write_text(render_camilla_yaml(settings))
    try:
        if alsa_path.read_text() == text:
            return False
    except OSError:
        pass
    alsa_path.parent.mkdir(parents=True, exist_ok=True)
    alsa_path.write_text(text)
    return True


def selftest_chain(aplay: str = "aplay", timeout: float = 10) -> tuple[bool, str]:
    """Open `freesounder_selftest` (the real chain into the null sink) and push 0.2 s of silence."""

    import shutil
    import subprocess

    if shutil.which(aplay) is None:
        return True, "aplay not installed; not checked"
    silence = b"\0" * (44100 * 4 // 5)
    try:
        proc = subprocess.run(
            [aplay, "-q", "-D", "freesounder_selftest", "-t", "raw"]
            + ["-f", "S16_LE", "-r", "44100", "-c", "2"],
            input=silence,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    err = proc.stderr.decode(errors="replace").strip().splitlines()
    return proc.returncode == 0, (err[-1] if err else "ok")


def apply(
    settings: PlayerSettings,
    *,
    alsa_path: Path,
    camilla_path: Path,
    status_path: Path = CHAIN_STATUS_FILE,
    params_path: Path = PARAMS_FILE,
    output_file: Path = AUDIO_OUTPUT_FILE,
    test=selftest_chain,
) -> dict[str, object]:
    """Render the live chain, self-test it, fall back to the static chain if it cannot open."""

    import json
    import time

    common = {
        "alsa_path": alsa_path,
        "camilla_path": camilla_path,
        "params_path": params_path,
        "output_file": output_file,
        "status_path": status_path,
    }
    write_dsp_configs(settings, live=True, **common)
    ok, detail = test()
    status: dict[str, object] = {"dsp": "live", "ok": ok, "detail": detail}
    if not ok:
        write_dsp_configs(settings, live=False, **common)
        ok2, detail2 = test()
        status = {"dsp": "fallback", "ok": ok2, "detail": detail2, "dsp_error": detail}
    status.update(read_audio_output(output_file))
    status["updated"] = time.time()
    try:
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(json.dumps(status, indent=2) + "\n")
    except OSError:
        pass
    return status


def main() -> None:
    """Load stored settings, write the ALSA chain and live params, and self-test the chain."""

    from freesounderd.config import load_settings
    from freesounderd.store import Store

    settings = load_settings()
    store = Store(settings)
    current = store.load_settings()
    status = apply(current, alsa_path=settings.alsa_conf, camilla_path=settings.camilla_conf)
    print(f"freesounder ALSA chain: dsp={status['dsp']} ok={status['ok']} ({status['detail']})")


if __name__ == "__main__":
    main()

"""Audio board detection and the DAC overlay block in config.txt.

Run at boot by ``freesounder-audio-detect.service`` (``python -m
freesounderd.audiohw boot``), before snapclient, and
by ``apply-dac-overlay`` when the board is changed in the app (``set``).

* ``auto`` (the default):
  - A HAT with an ID EEPROM: the firmware loads its overlay itself.
  - A USB or other sound card that is already there: used as it is.
  - Otherwise the I2C bus on GPIO2/3 is probed for known codecs (PCM512x
    at 0x4d/0x4c, WM8960 at 0x1a, TAS5713 at 0x1b). If one answers, its
    overlay is written and the Pi reboots once. If the card still does not
    appear, the next candidate overlay is tried, at most ``MAX_REBOOTS``
    times per board.
  - Nothing found: the onboard outputs (HDMI, Pi 4 headphone jack).
* a board picked in the app: its overlay, no probing.

The managed block always starts with ``[all]`` (so it is never stuck under
a ``[pi4]``/``[cm5]`` section) and keeps ``dtparam=audio=on`` (the Pi 4
headphone jack).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

BLOCK_START = "# --- freesounder-dac-start ---"
BLOCK_END = "# --- freesounder-dac-end ---"
CONFIG_CANDIDATES = (Path("/boot/firmware/config.txt"), Path("/boot/config.txt"))
DAC_FILE = Path("/etc/freesounder/dac")
STATE_FILE = Path("/var/lib/freesounder/audio-detect.json")
STATUS_FILE = Path("/run/freesounder/audio-detect.json")
HAT_PRODUCT = Path("/proc/device-tree/hat/product")
CARDS_FILE = Path("/proc/asound/cards")
SHORT_BOOTS = Path("/var/lib/freesounder/short-boots")
I2C_BUS = Path("/dev/i2c-1")
MAX_REBOOTS = 2
I2C_SLAVE_FORCE = 0x0706

# App choice -> (dtoverlay, label). "auto" and "none" have no overlay.
BOARDS: dict[str, tuple[str, str]] = {
    "dacplus": ("rpi-dacplus", "Raspberry Pi DAC+"),
    "digiampplus": ("rpi-digiampplus,unmute_amp", "Raspberry Pi DigiAMP+"),
    "iqaudio-dacplus": ("iqaudio-dacplus", "IQaudio DAC+"),
    "iqaudio-digiampplus": ("iqaudio-dacplus,unmute_amp", "IQaudio DigiAMP+"),
    "hifiberry-dacplus": ("hifiberry-dacplus", "HiFiBerry DAC+"),
    # Clones and DAC+ Standard-style boards have no onboard clocks: the -std
    # overlay makes the Pi the I2S clock master (needed on the Pi 5, works on
    # every Pi). The plain overlay auto-detects Standard vs Pro, which clones
    # can fool into "Pro" mode (no clock, silent card).
    "pifi-dacplus": ("hifiberry-dacplus-std", "PiFi DAC+ 2.0 (HiFiBerry DAC+ compatible)"),
    "hifiberry-amp2": ("hifiberry-dacplus-std", "HiFiBerry Amp2"),
    "hifiberry-amp3": ("hifiberry-amp3", "HiFiBerry Amp3"),
    "hifiberry-amp4": ("hifiberry-dacplus-std", "HiFiBerry Amp4"),
    "hifiberry-amp4pro": ("hifiberry-amp4pro", "HiFiBerry Amp4 Pro"),
    "hifiberry-amp100": ("hifiberry-amp100", "HiFiBerry Amp100"),
    "hifiberry-dac": ("hifiberry-dac", "PCM5102A DAC"),
    "pcm5122-amp": ("hifiberry-dacplus-std", "PCM5122 amp HAT"),
    "max98357a": ("max98357a,no-sdmode", "MAX98357A I2S amp"),
    "justboom-dac": ("justboom-dac", "JustBoom DAC / Amp"),
    "wm8960": ("wm8960-soundcard", "Waveshare WM8960 audio HAT"),
}


@dataclass(frozen=True)
class Codec:
    addr: int
    label: str
    overlays: tuple[str, ...]


# Codecs that answer on I2C bus 1, in probe order. PCM5102A / MAX98357A have
# no I2C and cannot be detected: pick them in the app.
CODECS: tuple[Codec, ...] = (
    Codec(
        0x4D,
        "PCM512x/TAS5756 at 0x4d (HiFiBerry DAC+ compatible, e.g. PiFi DAC+ 2.0)",
        ("hifiberry-dacplus-std", "hifiberry-dacplus"),
    ),
    Codec(0x4C, "PCM512x at 0x4c (Raspberry Pi / IQaudio DAC+ compatible)", ("iqaudio-dacplus",)),
    Codec(0x1A, "WM8960 at 0x1a (Waveshare WM8960 audio HAT)", ("wm8960-soundcard",)),
    Codec(0x1B, "TAS5713 at 0x1b (HiFiBerry Amp+)", ("hifiberry-amp",)),
)

# Overlays an earlier auto-detection wrote that are now replaced (once).
LEGACY_OVERLAYS = {"hifiberry-dacplus": "hifiberry-dacplus-std"}

ONBOARD_IDS = re.compile(r"^(vc4hdmi\d*|Headphones|b1|HDMI|ALSA)$")
_CARD_RE = re.compile(r"^\s*(\d+) \[([A-Za-z0-9_]+) *\]: (\S+)(?: - (.*))?$")


# ---------------------------------------------------------------- config.txt


def find_config() -> Path | None:
    return next((p for p in CONFIG_CANDIDATES if p.is_file()), None)


def strip_block(text: str) -> str:
    """config.txt without the freesounder block (any old format)."""

    start = text.find(BLOCK_START)
    end = text.find(BLOCK_END)
    if start < 0 or end < start:
        return text
    tail = text[end + len(BLOCK_END) :]
    if tail.startswith("\n"):
        tail = tail[1:]
    return text[:start] + tail


def block_overlay(text: str) -> str | None:
    """The dtoverlay= in the freesounder block, if any."""

    start = text.find(BLOCK_START)
    end = text.find(BLOCK_END)
    if start < 0 or end < start:
        return None
    for line in text[start:end].splitlines():
        line = line.strip()
        if line.startswith("dtoverlay="):
            return line.split("=", 1)[1].strip() or None
    return None


def render_block(overlay: str | None, note: str) -> str:
    lines = [BLOCK_START, "[all]", f"# {note}"]
    lines.append("# Onboard audio (Pi 4 headphone jack, HDMI) stays on as a fallback.")
    lines.append("dtparam=audio=on")
    if overlay:
        lines.append(f"dtoverlay={overlay}")
    lines.append(BLOCK_END)
    return "\n".join(lines) + "\n"


def set_block(text: str, overlay: str | None, note: str) -> str:
    """Replace the freesounder block; turn an explicit ``dtparam=audio=off`` on."""

    base = strip_block(text)
    base = re.sub(r"(?m)^(\s*)dtparam=audio=off\s*$", r"\1dtparam=audio=on", base)
    if base and not base.endswith("\n"):
        base += "\n"
    if base and not base.endswith("\n\n"):
        base += "\n"
    return base + render_block(overlay, note)


def onboard_audio_on(text: str) -> bool:
    lines = [ln.strip() for ln in text.splitlines()]
    return "dtparam=audio=on" in lines and "dtparam=audio=off" not in lines


# ---------------------------------------------------------------- hardware


def read_cards(path: Path = CARDS_FILE) -> list[dict[str, str]]:
    out = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        m = _CARD_RE.match(line)
        if m:
            out.append(
                {
                    "index": m.group(1),
                    "id": m.group(2),
                    "driver": m.group(3),
                    "name": m.group(4) or "",
                }
            )
    return out


def external_cards(cards: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    """DAC/amp HATs and USB cards (not HDMI, not the headphone jack)."""

    return [
        c
        for c in cards
        if c["driver"] != "vc4-hdmi"
        and not ONBOARD_IDS.match(c["id"])
        and "bcm2835" not in c["driver"]
    ]


def card_names(cards: Iterable[dict[str, str]]) -> str:
    return ", ".join(c["id"] for c in cards) or "none"


def read_hat(path: Path = HAT_PRODUCT) -> str | None:
    try:
        raw = path.read_bytes().decode(errors="replace")
    except OSError:
        return None
    return raw.replace("\x00", "").strip() or None


def probe_i2c(addrs: Iterable[int], bus: Path = I2C_BUS) -> set[int]:
    """Addresses that ACK a one-byte read (what ``i2cdetect -r`` does)."""

    import fcntl

    found: set[int] = set()
    try:
        fd = os.open(bus, os.O_RDWR)
    except OSError:
        return found
    try:
        for addr in addrs:
            try:
                fcntl.ioctl(fd, I2C_SLAVE_FORCE, addr)
                os.read(fd, 1)
                found.add(addr)
            except OSError:
                pass
    finally:
        os.close(fd)
    return found


class I2cBus(contextlib.AbstractContextManager["I2cBus"]):
    """Make /dev/i2c-1 available for probing; undo a runtime dtparam afterwards."""

    def __init__(self, bus: Path = I2C_BUS) -> None:
        self.bus = bus
        self.added_param = False
        self.error = ""

    def __enter__(self) -> I2cBus:
        if not self.bus.exists():
            _run(["modprobe", "i2c-dev"])
        if not self.bus.exists():
            # Bus not enabled in config.txt: switch it on for this boot only.
            self.added_param = _run(["dtparam", "i2c_arm=on"]) == 0
            for _ in range(20):
                if self.bus.exists():
                    break
                time.sleep(0.1)
        if not self.bus.exists():
            self.error = "I2C bus 1 not available"
        return self

    def __exit__(self, *exc: object) -> None:
        if not self.added_param:
            return
        # Leave the bus as config.txt has it (the DAC overlay enables it itself).
        out = _output(["dtoverlay", "-l"])
        for line in out.splitlines():
            m = re.match(r"\s*(\d+):\s+dtparam\b.*i2c_arm=on", line)
            if m:
                _run(["dtoverlay", "-r", m.group(1)])
                break


def _run(cmd: list[str]) -> int:
    try:
        return subprocess.run(cmd, check=False, capture_output=True, timeout=20).returncode
    except (OSError, subprocess.SubprocessError):
        return 1


def _output(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


# ---------------------------------------------------------------- decisions


@dataclass
class Detection:
    detected: str
    overlay: str | None = None
    board: str = ""
    action: str = "none"  # none | wrote | reboot | gave-up
    expect_card: bool = False
    cards: str = ""
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "detected": self.detected,
            "overlay": self.overlay,
            "board": self.board,
            "action": self.action,
            "expect_card": self.expect_card,
            "cards": self.cards,
            "notes": self.notes,
            "updated": time.time(),
        }


def _is_usb(card: dict[str, str]) -> bool:
    return "usb" in (card["driver"] + " " + card["name"]).lower()


def _keep(config: str, overlay: str | None, note: str) -> str:
    """Rewrite the block only if it is missing [all] / audio=on or has another overlay."""

    if block_overlay(config) == overlay and onboard_audio_on(config) and "\n[all]\n" in config:
        return config
    return set_block(config, overlay, note)


def decide(
    *,
    choice: str,
    config: str,
    cards: list[dict[str, str]],
    hat: str | None,
    probe: Callable[[], tuple[set[int], str]],
    state: dict[str, object],
) -> tuple[str, Detection]:
    """New config.txt text and what was detected. Pure: no I/O besides ``probe``."""

    ext = external_cards(cards)
    names = card_names(cards)
    current = block_overlay(config)
    reboots = int(state.get("reboots", 0) or 0)

    if choice == "none":
        det = Detection("onboard/USB audio only (set in the app)", cards=names)
        return _keep(config, None, "DAC: none (onboard / USB audio only)."), det

    if choice in BOARDS:
        overlay, label = BOARDS[choice]
        det = Detection(
            f"{label} (set in the app, dtoverlay={overlay})",
            overlay=overlay,
            board=label,
            expect_card=True,
            cards=names,
        )
        new = _keep(config, overlay, f"Set from the FreeSounder app: {choice}.")
        if not ext:
            if new != config and reboots < 1:
                det.action = "reboot"
            else:
                det.notes.append(
                    "its sound card did not appear: check the board is seated, "
                    "or pick another board"
                )
        return new, det

    # ---- auto
    if hat:
        det = Detection(f"HAT with ID EEPROM: {hat}", board=hat, expect_card=True, cards=names)
        return _keep(config, None, f"DAC: auto. EEPROM HAT '{hat}' loads its own overlay."), det

    board = str(state.get("board") or "")
    non_usb = [c for c in ext if not _is_usb(c)]
    if non_usb:
        first = non_usb[0]
        tried_before = [str(x) for x in state.get("tried", []) or []]
        better = LEGACY_OVERLAYS.get(current or "")
        if better and board == CODECS[0].label and better not in tried_before and reboots < 1:
            # 0.6.9 auto-picked the plain overlay; its card can appear and stay
            # silent on clones. Switch once to the -std overlay.
            det = Detection(
                f"{board} (auto-detected, switching dtoverlay={current} -> {better})",
                overlay=better,
                board=board,
                action="reboot",
                expect_card=True,
                cards=names,
            )
            return set_block(config, better, f"DAC: auto-detected {board}."), det
        if current and board:
            det = Detection(
                f"{board} (auto-detected, dtoverlay={current}); card {first['id']}",
                overlay=current,
                board=board,
                expect_card=True,
                cards=names,
            )
        else:
            det = Detection(
                f"sound card {first['id']} ({first['name'] or first['driver']})", cards=names
            )
            det.expect_card = True
        return _keep(config, current, f"DAC: auto-detected {board or first['id']}."), det

    found, error = probe()
    codec = next((c for c in CODECS if c.addr in found), None)
    usb = [c for c in ext if _is_usb(c)]
    fallback = f"USB audio {usb[0]['id']}" if usb else "HDMI / headphone jack"
    if codec is None:
        msg = f"I2C not probed ({error})" if error else "no audio board found on I2C"
        det = Detection(f"{msg}; using {fallback}", cards=names, expect_card=bool(usb))
        return _keep(config, None, "DAC: auto (no board detected)."), det

    tried = [str(x) for x in state.get("tried", []) or []]
    if current in codec.overlays and current not in tried:
        tried.append(current)
    remaining = [o for o in codec.overlays if o not in tried]
    if not remaining or reboots >= MAX_REBOOTS:
        det = Detection(
            f"{codec.label}, but its sound card did not appear (tried {', '.join(tried)}); "
            f"using {fallback}",
            overlay=current,
            board=codec.label,
            action="gave-up",
            cards=names,
        )
        det.notes.append("pick the board in the app, or check that it is seated")
        return config, det
    overlay = remaining[0]
    det = Detection(
        f"{codec.label} (auto-detected, dtoverlay={overlay})",
        overlay=overlay,
        board=codec.label,
        action="reboot",
        expect_card=True,
        cards=names,
    )
    return set_block(config, overlay, f"DAC: auto-detected {codec.label}."), det


# ---------------------------------------------------------------- entry points


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _log(msg: str) -> None:
    print(f"audio-detect: {msg}", flush=True)


def run_boot(
    *,
    config_path: Path | None = None,
    dac_path: Path = DAC_FILE,
    state_path: Path = STATE_FILE,
    status_path: Path = STATUS_FILE,
    cards_path: Path = CARDS_FILE,
    hat_path: Path = HAT_PRODUCT,
    probe: Callable[[], tuple[set[int], str]] | None = None,
    reboot: Callable[[], None] | None = None,
    settle: float = 8.0,
    allow_reboot: bool = True,
) -> Detection:
    cfg = config_path or find_config()
    if cfg is None:
        det = Detection("no config.txt found")
        _write_atomic(status_path, json.dumps(det.as_dict(), indent=2) + "\n")
        return det
    choice = (_read(dac_path).strip() or "auto").lower()
    config = _read(cfg)
    try:
        state = json.loads(_read(state_path) or "{}")
    except ValueError:
        state = {}
    if not isinstance(state, dict):
        state = {}

    # Cards can take a few seconds to probe (I2C codecs, USB): wait for the
    # one our overlay should create before deciding it failed.
    deadline = time.monotonic() + settle
    cards = read_cards(cards_path)
    expecting = block_overlay(config) is not None or read_hat(hat_path) is not None
    while expecting and not external_cards(cards) and time.monotonic() < deadline:
        time.sleep(0.5)
        cards = read_cards(cards_path)

    def default_probe() -> tuple[set[int], str]:
        with I2cBus() as bus:
            if bus.error:
                return set(), bus.error
            return probe_i2c([c.addr for c in CODECS]), ""

    new_config, det = decide(
        choice=choice,
        config=config,
        cards=cards,
        hat=read_hat(hat_path),
        probe=probe or default_probe,
        state=state,
    )
    if new_config != config:
        _write_atomic(cfg, new_config)
        _log(f"wrote {cfg}: dtoverlay={det.overlay or '-'}")
        if det.action == "none":
            det.action = "wrote"
    if det.action == "reboot":
        tried = [str(x) for x in state.get("tried", []) or []]
        if det.overlay and det.overlay not in tried:
            tried.append(det.overlay)
        state = {
            "board": det.board,
            "pending": True,
            "tried": tried,
            "reboots": int(state.get("reboots", 0) or 0) + 1,
        }
    elif det.action == "gave-up":
        state = {**state, "pending": False}
    elif det.expect_card and det.overlay and choice == "auto":
        # Worked: remember the board, allow fresh attempts if it changes.
        state = {"board": det.board, "pending": False, "tried": [det.overlay], "reboots": 0}
    elif choice == "auto" or (det.expect_card and det.action != "reboot"):
        state = {}
    _write_atomic(state_path, json.dumps(state, indent=2) + "\n")
    _write_atomic(status_path, json.dumps(det.as_dict(), indent=2) + "\n")
    _log(f"{det.detected}; cards: {det.cards}; action: {det.action}")
    if det.action == "reboot" and allow_reboot:
        # An intentional reboot: do not count it as a short power cycle.
        with contextlib.suppress(OSError):
            _write_atomic(SHORT_BOOTS, "0\n")
        _log("rebooting once to load the overlay")
        (reboot or (lambda: subprocess.run(["systemctl", "reboot"], check=False)))()
    return det


def run_set(choice: str, *, config_path: Path | None = None, dac_path: Path = DAC_FILE) -> int:
    """Board picked in the app: write its overlay (or clear it for auto/none)."""

    choice = choice.strip().lower() or "auto"
    if choice not in BOARDS and choice not in ("auto", "none"):
        print(f"Unknown DAC '{choice}'", file=sys.stderr)
        return 1
    cfg = config_path or find_config()
    if cfg is None:
        print("No config.txt found under /boot/firmware or /boot", file=sys.stderr)
        return 1
    text = _read(cfg)
    if choice in BOARDS:
        overlay, _ = BOARDS[choice]
        new = set_block(text, overlay, f"Set from the FreeSounder app: {choice}.")
    elif choice == "none":
        new = set_block(text, None, "DAC: none (onboard / USB audio only).")
    else:
        # auto: probed again at the next boot.
        new = set_block(text, None, "DAC: auto (detected at boot).")
        with contextlib.suppress(OSError):
            STATE_FILE.unlink()
    if new != text:
        _write_atomic(cfg, new)
    _write_atomic(dac_path, choice + "\n")
    print(f"DAC: {choice} -> {cfg} (reboot to apply)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="freesounderd.audiohw")
    sub = parser.add_subparsers(dest="cmd", required=True)
    boot = sub.add_parser("boot", help="detect at boot, write the overlay, reboot once if needed")
    boot.add_argument("--no-reboot", action="store_true")
    s = sub.add_parser("set", help="write the overlay for a board picked in the app")
    s.add_argument("choice")
    sub.add_parser("status", help="print the last detection")
    args = parser.parse_args(argv)
    if args.cmd == "boot":
        run_boot(allow_reboot=not args.no_reboot)
        return 0
    if args.cmd == "set":
        return run_set(args.choice)
    print(_read(STATUS_FILE) or "{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

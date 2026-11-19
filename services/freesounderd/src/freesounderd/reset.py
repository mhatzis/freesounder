"""Factory reset triggers: Pi 5 power-button taps and rapid power cycles.

Ways to factory-reset a player (see docs/reset.md):

* In the app: Room settings -> Factory reset (``POST /api/v2/rooms/{id}/factory-reset``).
* Pi 5 power button: 5 quick presses. One press is a clean shutdown. The
  button is on the PMIC, not on the 40-pin header.
* There is no wired header button any more (0.6.11): a button on GPIO3 shared
  the I2C clock line with DAC/amp HATs. Nothing here touches header GPIOs.
* Power-cycle: cut power 5 boots in a row while the counter is armed
  (from when it is written, about 5-15 s after power-on, until 40 s of
  uptime: unplug about 20 s after plugging in), then power on and leave it.

The logic lives here as small pure classes so it can be unit-tested; the
``main`` entry points are run by systemd units in the image.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import select
import struct
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# ----------------------------------------------------------------- tap counting

TAPS_FOR_RESET = 5
TAP_WINDOW = 3.0  # all 5 presses within this many seconds
SINGLE_SETTLE = 1.2  # a lone press becomes "shutdown" after this quiet gap


@dataclass
class TapCounter:
    """Turns key presses into actions.

    * 5 presses within ``window`` seconds -> "reset" (at once, on the 5th).
    * 1 press followed by ``settle`` seconds of quiet -> "shutdown".
    * 2-4 presses then quiet -> "ignored" (a mis-count never shuts down).
    """

    taps: int = TAPS_FOR_RESET
    window: float = TAP_WINDOW
    settle: float = SINGLE_SETTLE
    presses: list[float] = field(default_factory=list)

    def press(self, now: float) -> str | None:
        # A press after a long gap starts a new sequence.
        if self.presses and now - self.presses[-1] > self.window:
            self.presses = []
        self.presses.append(now)
        self.presses = [t for t in self.presses if now - t <= self.window]
        if len(self.presses) >= self.taps:
            self.presses = []
            return "reset"
        return None

    def poll(self, now: float) -> str | None:
        if not self.presses or now - self.presses[-1] < self.settle:
            return None
        count = len(self.presses)
        self.presses = []
        return "shutdown" if count == 1 else "ignored"

    def deadline(self) -> float | None:
        return self.presses[-1] + self.settle if self.presses else None


# ----------------------------------------------------------------- boot counter

BOOTS_FOR_RESET = 5
ARMED_UNTIL_UPTIME = 40.0  # seconds since kernel start


def _fsync_write(path: Path, text: str) -> None:
    """Atomic, durable write: tmp file, fsync, rename, fsync the directory."""

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, text.encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    dfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


@dataclass
class BootCounter:
    """Counts boots that were cut short (power removed while armed).

    ``on_boot`` runs early in every boot: it increments the stored count
    (one tiny fsync'd write) and says "reset" when the previous
    ``threshold`` boots were all cut while armed. ``clear`` runs once
    uptime passes ``armed_until``: an ordinary boot therefore always ends
    with the count back at 0, and only consecutive short boots add up.
    """

    path: Path
    threshold: int = BOOTS_FOR_RESET
    armed_until: float = ARMED_UNTIL_UPTIME

    def count(self) -> int:
        try:
            return max(0, int(self.path.read_text().split()[0]))
        except (OSError, ValueError, IndexError):
            return 0

    def on_boot(self, *, power_ok: bool = True) -> str:
        current = self.count()
        if not power_ok:
            # Under-voltage reboots can loop quickly; never count them.
            self.clear()
            return "undervoltage"
        if current >= self.threshold:
            self.clear()
            return "reset"
        _fsync_write(self.path, f"{current + 1}\n")
        return f"armed {current + 1}/{self.threshold}"

    def clear(self) -> None:
        if self.path.exists():
            # Write-then-unlink keeps the "0" durable even if power goes now.
            with contextlib.suppress(OSError):
                _fsync_write(self.path, "0\n")
                self.path.unlink()


def uptime() -> float:
    try:
        return float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return time.monotonic()


def throttled() -> int | None:
    """`vcgencmd get_throttled` as an int (None where not available)."""

    try:
        out = subprocess.run(
            ["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=5, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"0x([0-9a-fA-F]+)", out)
    return int(match.group(1), 16) if match else None


THROTTLE_BITS = {
    0: "under-voltage now",
    1: "ARM frequency capped now",
    2: "throttled now",
    3: "soft temperature limit now",
    16: "under-voltage since boot",
    17: "ARM frequency capped since boot",
    18: "throttled since boot",
    19: "soft temperature limit since boot",
}


def decode_throttled(value: int | None) -> list[str]:
    if value is None:
        return []
    return [text for bit, text in THROTTLE_BITS.items() if value & (1 << bit)]


# ----------------------------------------------------------------- input events

KEY_POWER = 116
EV_KEY = 1
_EVENT = struct.Struct("llHHi")  # struct input_event on 64-bit Linux


def power_key_devices(proc_devices: str) -> list[str]:
    """event* nodes whose key bitmap includes KEY_POWER (/proc/bus/input/devices)."""

    found = []
    for block in proc_devices.strip().split("\n\n"):
        handlers = re.search(r"^H: Handlers=(.*)$", block, re.M)
        keys = re.search(r"^B: KEY=(.*)$", block, re.M)
        if not handlers or not keys:
            continue
        words = keys.group(1).split()
        # Highest word first; each word is a 64-bit (or 32-bit) hex long.
        bits = 0
        width = 64
        for word in words:
            bits = (bits << width) | int(word, 16)
        if bits >> KEY_POWER & 1:
            for name in handlers.group(1).split():
                if name.startswith("event"):
                    found.append(f"/dev/input/{name}")
    return found


def _read(path: str) -> str:
    try:
        return Path(path).read_text()
    except OSError:
        return ""


# ----------------------------------------------------------------- actions

RESET_SCRIPT = "/usr/lib/freesounder/factory-reset"
STATE_DIR = Path("/var/lib/freesounder")


def _log(msg: str) -> None:
    print(msg, flush=True)


def run_buttons(argv: list[str]) -> int:
    counter = TapCounter()
    open_fds: dict[str, int] = {}
    last_scan = 0.0
    while True:
        now = time.monotonic()
        if now - last_scan > 10:
            last_scan = now
            try:
                wanted = power_key_devices(Path("/proc/bus/input/devices").read_text())
            except OSError:
                wanted = []
            for dev in wanted:
                if dev not in open_fds:
                    with contextlib.suppress(OSError):
                        open_fds[dev] = os.open(dev, os.O_RDONLY | os.O_NONBLOCK)
                        _log(f"listening for the power key on {dev}")
        deadline = counter.deadline()
        timeout = 1.0 if deadline is None else max(0.0, min(1.0, deadline - time.monotonic()))
        ready = []
        if open_fds:
            with contextlib.suppress(OSError):
                ready, _, _ = select.select(list(open_fds.values()), [], [], timeout)
        else:
            time.sleep(timeout)
        for fd in ready:
            try:
                data = os.read(fd, _EVENT.size * 16)
            except BlockingIOError:
                continue
            except OSError:
                for dev, other in list(open_fds.items()):
                    if other == fd:
                        os.close(fd)
                        del open_fds[dev]
                continue
            for off in range(0, len(data) - _EVENT.size + 1, _EVENT.size):
                _s, _us, etype, code, value = _EVENT.unpack_from(data, off)
                if etype == EV_KEY and code == KEY_POWER and value == 1:
                    action = counter.press(time.monotonic())
                    count = len(counter.presses) or TAPS_FOR_RESET
                    _log(f"POWER BUTTON pressed ({count}/5 to reset)")
                    if action == "reset":
                        _log("5 presses: factory reset")
                        subprocess.run([RESET_SCRIPT, "--reason", "button"], check=False)
        action = counter.poll(time.monotonic())
        if action == "shutdown":
            _log("POWER BUTTON: clean shutdown requested by the power button (1 press)")
            subprocess.run(["systemctl", "poweroff"], check=False)
        elif action == "ignored":
            _log("power key: 2-4 presses, ignored (press 5 times quickly to reset)")


def run_boot_count(argv: list[str]) -> int:
    counter = BootCounter(STATE_DIR / "short-boots")
    flags = throttled()
    power_ok = flags is None or not flags & (1 << 16 | 1 << 0)
    result = counter.on_boot(power_ok=power_ok)
    _log(f"power-cycle reset counter: {result} (uptime {uptime():.1f} s)")
    if result == "reset":
        _fsync_write(STATE_DIR / "reset-pending", "power-cycle\n")
    return 0


def blink_act(times: int) -> None:
    """Blink the green ACT LED ``times`` times, then give it back to the SD card."""

    for led in (Path("/sys/class/leds/ACT"), Path("/sys/class/leds/led0")):
        trigger = led / "trigger"
        if not trigger.exists():
            continue
        match = re.search(r"\[([^\]]+)\]", _read(str(trigger)))
        before = match.group(1) if match else "mmc0"
        try:
            trigger.write_text("none")
            for _ in range(times):
                (led / "brightness").write_text("1")
                time.sleep(0.25)
                (led / "brightness").write_text("0")
                time.sleep(0.35)
            trigger.write_text(before)
        except OSError:
            pass
        return


def run_boot_clear(argv: list[str]) -> int:
    counter = BootCounter(STATE_DIR / "short-boots")
    # Show progress: the LED blinks once per short boot counted so far.
    pending = counter.count()
    if pending >= 2:
        blink_act(pending)
    wait = counter.armed_until - uptime()
    if wait > 0:
        time.sleep(wait)
    counter.clear()
    _log("power-cycle reset counter cleared (normal boot)")
    return 0


def run_power_log(argv: list[str]) -> int:
    """Log throttle/under-voltage flags (at boot and from a timer) to the journal."""

    flags = throttled()
    if flags is None:
        return 0
    last_path = Path("/run/freesounder/throttled")
    previous = None
    with contextlib.suppress(OSError, ValueError):
        previous = int(last_path.read_text().strip(), 16)
    boot = "--boot" in argv
    if boot or flags != previous:
        words = ", ".join(decode_throttled(flags)) or "ok"
        _log(f"power: throttled=0x{flags:x} ({words})")
    with contextlib.suppress(OSError):
        last_path.parent.mkdir(parents=True, exist_ok=True)
        last_path.write_text(f"{flags:x}\n")
    return 0


def _heartbeat(argv: list[str]) -> int:
    from freesounderd.power import run_heartbeat

    return run_heartbeat(argv)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="freesounder-reset-triggers")
    parser.add_argument(
        "command", choices=["buttons", "boot-count", "boot-clear", "power-log", "heartbeat"]
    )
    args, rest = parser.parse_known_args(argv)
    return {
        "buttons": run_buttons,
        "boot-count": run_boot_count,
        "boot-clear": run_boot_clear,
        "power-log": run_power_log,
        "heartbeat": _heartbeat,
    }[args.command](rest)


if __name__ == "__main__":
    sys.exit(main())

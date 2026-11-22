"""Power watch: a heartbeat every 15 s, under-voltage/thermal events, and
what the Pi 5 PMIC says about the last reset.

``freesounder-reset-triggers heartbeat`` runs as freesounder-heartbeat.service.
Every ``HEARTBEAT`` seconds it rewrites /var/lib/freesounder/heartbeat.json
(atomic + fsync; about 200 bytes) with the time, uptime, input voltage and
throttle flags. After a crash, the next boot moves that file to
last-boot.json, so the diagnostics can say roughly when the player died and how
long it had been up. A clean shutdown marks the file ``"clean": true``,
records which target was requested (poweroff / reboot / halt), and logs it.
"""

from __future__ import annotations

import contextlib
import json
import re
import signal
import struct
import subprocess
import time
from pathlib import Path
from typing import Any

from freesounderd.cpu import TOP_LOG_AT, ProcSampler, ThermalGuard, format_top, freq_info, loadavg
from freesounderd.reset import _fsync_write, decode_throttled, throttled, uptime

HEARTBEAT = 15.0
STATE = Path("/var/lib/freesounder")
DT_POWER = Path("/proc/device-tree/chosen/power")
DT_BOOTLOADER = Path("/proc/device-tree/chosen/bootloader")
HISTORY_MAX = 100
CPU_LOG_EVERY = 20  # beats: a "cpu:" journal line every 5 min (every beat while hot)
LOW_EXT5V = 4.75  # Pi 5 starts to brown out a little below this

# Raspberry Pi 5 PMIC reset reason bits (chosen/power/power_reset).
PMIC_RESET_BITS = {
    0: "over-voltage",
    1: "under-voltage",
    2: "over-temperature",
    3: "enable signal (power button / GLOBAL_EN)",
    4: "watchdog",
}


def _be32(path: Path) -> int | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) < 4:
        return None
    return struct.unpack(">I", raw[:4])[0]


def decode_pmic_reset(value: int | None) -> list[str]:
    if value is None:
        return []
    return [text for bit, text in PMIC_RESET_BITS.items() if value & (1 << bit)]


def pi5_power_info() -> dict[str, Any]:
    """Firmware/PMIC facts from the device tree (Pi 5; mostly empty elsewhere)."""

    reset = _be32(DT_POWER / "power_reset")
    info: dict[str, Any] = {
        "pmic_reset": None if reset is None else f"0x{reset:x}",
        "pmic_reset_reason": decode_pmic_reset(reset) or (["none"] if reset == 0 else []),
        "psu_max_current_ma": _be32(DT_POWER / "max_current"),
        "usb_over_current_at_boot": bool(_be32(DT_POWER / "usb_over_current_detected") or 0),
    }
    rsts = _be32(DT_BOOTLOADER / "rsts")
    if rsts is not None:
        info["bootloader_rsts"] = f"0x{rsts:x}"
    return info


def ext5v() -> float | None:
    """Pi 5 input voltage (vcgencmd pmic_read_adc EXT5V_V)."""

    try:
        out = subprocess.run(
            ["vcgencmd", "pmic_read_adc", "EXT5V_V"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"=\s*([0-9.]+)V", out)
    return round(float(match.group(1)), 3) if match else None


def cpu_temp() -> float | None:
    try:
        return int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000
    except (OSError, ValueError):
        return None


def boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return "unknown"


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _log(msg: str) -> None:
    print(msg, flush=True)


def _warn(msg: str) -> None:
    # "<4>" = warning priority for the journal (systemd's SyslogLevelPrefix).
    print(f"<4>{msg}", flush=True)


class Watch:
    """One heartbeat process per boot. Paths and probes are injectable for tests."""

    def __init__(self, state: Path = STATE) -> None:
        self.state = state
        self.boot = boot_id()
        self.started = time.time()
        self.min_ext5v: float | None = None
        self.last_flags: int | None = None
        self.hot = False
        self.low_logged: float | None = None
        self.read_flags = throttled
        self.read_ext5v = ext5v
        self.read_temp = cpu_temp
        self.sampler = ProcSampler()
        self.guard = ThermalGuard()
        self.read_load = loadavg
        self.read_freq = freq_info
        self.beats = 0
        self.next_cpu_log = 0

    @property
    def heartbeat_path(self) -> Path:
        return self.state / "heartbeat.json"

    @property
    def history_path(self) -> Path:
        return self.state / "power-history.json"

    def archive_previous(self) -> dict[str, Any] | None:
        """At start: the previous boot's last heartbeat becomes last-boot.json."""

        prev = read_json(self.heartbeat_path)
        if not isinstance(prev, dict) or prev.get("boot_id") == self.boot:
            return None
        _fsync_write(self.state / "last-boot.json", json.dumps(prev) + "\n")
        when = time.strftime("%Y-%m-%d %H:%M:%S %z", time.localtime(prev.get("time", 0)))
        up = prev.get("uptime", 0)
        if prev.get("clean"):
            _log(f"previous boot ended cleanly ({prev.get('shutdown') or 'shutdown'}) at {when}")
        else:
            _warn(
                f"PREVIOUS BOOT DID NOT SHUT DOWN CLEANLY: last heartbeat {when}, "
                f"after {up / 60:.1f} min up, input {prev.get('ext5v')} V "
                f"(min {prev.get('min_ext5v')} V), throttled {prev.get('throttled')}"
            )
        return prev

    def _history(self, entry: dict[str, Any]) -> None:
        items = read_json(self.history_path)
        items = items if isinstance(items, list) else []
        items = [*items, entry][-HISTORY_MAX:]
        _fsync_write(self.history_path, json.dumps(items) + "\n")

    def beat(self, *, clean: bool = False, shutdown: str | None = None) -> dict[str, Any]:
        flags = self.read_flags()
        volts = self.read_ext5v()
        temp = self.read_temp()
        now = time.time()
        load = self.read_load()
        usage = self.sampler.sample()
        top = (usage or {}).get("top", [])
        freq = self.read_freq()
        if volts is not None:
            self.min_ext5v = volts if self.min_ext5v is None else min(self.min_ext5v, volts)
        if flags is not None and flags != self.last_flags:
            words = decode_throttled(flags)
            new_uv = flags & 0x1 or (flags & 0x10000 and not (self.last_flags or 0) & 0x10000)
            if new_uv:
                _warn(f"UNDER-VOLTAGE: throttled=0x{flags:x} ({', '.join(words)}), input {volts} V")
            elif flags & 0xE:
                _warn(
                    f"THROTTLING: throttled=0x{flags:x} ({', '.join(words)}), {temp} C, "
                    f"{freq.get('cur_mhz')} MHz, load {load}, top: {format_top(top)}"
                )
            else:
                _log(f"power flags: throttled=0x{flags:x} ({', '.join(words) or 'ok'})")
            self._history(
                {
                    "time": now,
                    "uptime": uptime(),
                    "throttled": f"0x{flags:x}",
                    "ext5v": volts,
                    "temp": temp,
                    "load": load,
                    "cpu_mhz": freq.get("cur_mhz"),
                    "top": top[:3],
                }
            )
            self.last_flags = flags
        if (
            volts is not None
            and volts < LOW_EXT5V
            and (self.low_logged is None or volts < self.low_logged - 0.05)
        ):
            _warn(f"LOW INPUT VOLTAGE: {volts} V (a Pi 5 needs about 5.0 V)")
            self.low_logged = volts
        if temp is not None:
            if temp >= TOP_LOG_AT and not self.hot:
                _warn(
                    f"HOT: CPU {temp:.1f} C (firmware soft limit at 80 C), load {load}, "
                    f"top: {format_top(top)}"
                )
                self.hot = True
            elif temp < 75:
                self.hot = False
        change = self.guard.update(temp)
        if change:
            _warn(f"{change}; load {load}, top: {format_top(top)}")
        record = {
            "boot_id": self.boot,
            "time": now,
            "uptime": round(uptime(), 1),
            "started": self.started,
            "ext5v": volts,
            "min_ext5v": self.min_ext5v,
            "temp": temp,
            "throttled": None if flags is None else f"0x{flags:x}",
            "load": load,
            "cpu_busy": (usage or {}).get("cpu_busy"),
            "cpu_mhz": freq.get("cur_mhz"),
            "cpu_max_mhz": freq.get("max_mhz"),
            "governor": freq.get("governor"),
            "clock_capped": self.guard.capped,
            "top": top,
            "clean": clean,
        }
        if shutdown:
            record["shutdown"] = shutdown
        self.beats += 1
        if usage is not None and (self.hot or self.beats >= self.next_cpu_log):
            _log(
                f"cpu: {temp} C, {freq.get('cur_mhz')}/{freq.get('max_mhz')} MHz "
                f"({freq.get('governor')}{', capped' if self.guard.capped else ''}), "
                f"load {load}, busy {usage.get('cpu_busy')}%, top: {format_top(top)}"
            )
            self.next_cpu_log = self.beats + CPU_LOG_EVERY
        _fsync_write(self.heartbeat_path, json.dumps(record) + "\n")
        return record


def shutdown_kind() -> str:
    """Which shutdown is running (from systemd's job queue)."""

    try:
        jobs = subprocess.run(
            ["systemctl", "list-jobs", "--no-legend", "--no-pager"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        jobs = ""
    for target in ("poweroff.target", "reboot.target", "halt.target", "kexec.target"):
        if target in jobs:
            return target.removesuffix(".target")
    return "shutdown"


def run_heartbeat(argv: list[str]) -> int:
    watch = Watch()
    watch.archive_previous()
    info = pi5_power_info()
    if info.get("pmic_reset") is not None:
        _log(
            f"Pi 5 PMIC reset reason {info['pmic_reset']} "
            f"({', '.join(info['pmic_reset_reason']) or 'none'}), "
            f"PSU max current {info.get('psu_max_current_ma')} mA"
        )
    # SIGTERM is taken synchronously: one wake-up per beat (the old loop
    # woke 10 times a second to check a flag) and still a prompt stop.
    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM, signal.SIGINT})
    while True:
        with contextlib.suppress(OSError):
            watch.beat()
        if signal.sigtimedwait({signal.SIGTERM, signal.SIGINT}, HEARTBEAT) is not None:
            break
    watch.guard.release()
    kind = shutdown_kind()
    _log(f"CLEAN SHUTDOWN started: {kind} (after {uptime() / 60:.1f} min up)")
    with contextlib.suppress(OSError):
        watch.beat(clean=True, shutdown=kind)
    return 0


# A Pi 5 needs about 5.0 V; below this the radios and the SD card misbehave.
LOW_VOLTS = 4.8


def power_warning(state: Path = STATE) -> str | None:
    """A plain warning for the app when this player's supply is too weak
    (0.6.19): under-voltage now or since boot, or the input dipped low.
    From the power watch's heartbeat; None when all is well or unknown."""

    beat = read_json(state / "heartbeat.json")
    if not isinstance(beat, dict):
        return None
    flags = beat.get("throttled")
    try:
        value = int(str(flags), 16) if isinstance(flags, str) else int(flags or 0)
    except ValueError:
        value = 0
    low = beat.get("min_ext5v")
    dipped = isinstance(low, int | float) and 0 < low < LOW_VOLTS
    if not (value & 0x1 or value & 0x10000 or dipped):
        return None
    where = f" (it dropped to {low:.2f} V)" if isinstance(low, int | float) and low > 0 else ""
    return (
        f"This player's power supply is too weak{where}. Audio and Bluetooth may drop "
        "out. Use a 5 V supply with a short, thick cable (for a Pi 5, the official "
        "27 W supply)."
    )

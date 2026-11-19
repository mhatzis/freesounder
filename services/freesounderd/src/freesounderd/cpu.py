"""CPU load, clock, top processes and a gentle thermal clock cap.

Everything reads /proc and /sys only (no subprocesses), so the heartbeat can
sample it every 15 s for free. Paths are injectable for tests.

Thermal safeguard (``ThermalGuard``): when the SoC gets hot it lowers the
cpufreq ``scaling_max_freq`` to ``CAP_KHZ`` (1.5 GHz, the Pi 5's lowest
clock and still far more than snapserver/snapclient/ffmpeg need, which is a
few percent of one core), and restores the full clock once it has cooled.
The firmware's own soft limit only starts at 80 C; capping a little earlier
keeps the Pi out of it. ``/etc/freesounder/cpu`` can say ``off`` (never
touch clocks), ``auto`` (default) or ``cool`` (always capped).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROC = Path("/proc")
CPUFREQ = Path("/sys/devices/system/cpu/cpufreq/policy0")
MODE_FILE = Path("/etc/freesounder/cpu")
CAP_KHZ = 1_500_000  # never cap below this (nor below the hardware minimum)
CAP_AT = 75.0  # C: cap the clock from here
RESTORE_AT = 65.0  # C: full clock again below this
TOP_LOG_AT = 80.0  # C: log the top processes from here (once per hot spell)


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _int(path: Path) -> int | None:
    raw = _read(path)
    try:
        return int(raw) if raw is not None else None
    except ValueError:
        return None


def loadavg(proc: Path = PROC) -> list[float] | None:
    raw = _read(proc / "loadavg")
    try:
        return [float(x) for x in raw.split()[:3]] if raw else None
    except ValueError:
        return None


def freq_info(policy: Path = CPUFREQ) -> dict[str, Any]:
    """Current/min/max clock in MHz and the governor (empty where no cpufreq)."""

    out: dict[str, Any] = {}
    for key, name in (
        ("cur_mhz", "scaling_cur_freq"),
        ("max_mhz", "scaling_max_freq"),
        ("hw_min_mhz", "cpuinfo_min_freq"),
        ("hw_max_mhz", "cpuinfo_max_freq"),
    ):
        value = _int(policy / name)
        if value is not None:
            out[key] = round(value / 1000)
    governor = _read(policy / "scaling_governor")
    if governor:
        out["governor"] = governor
    return out


def _cpu_ticks(proc: Path) -> tuple[int, int] | None:
    """(all ticks, busy ticks) summed over every core, from /proc/stat."""

    raw = _read(proc / "stat")
    if not raw or not raw.startswith("cpu "):
        return None
    try:
        v = [int(x) for x in raw.splitlines()[0].split()[1:8]]
    except ValueError:
        return None
    return sum(v), sum(v) - v[3] - v[4]  # busy = minus idle and iowait


def _ncpu() -> int:
    return os.cpu_count() or 1


@dataclass
class ProcSampler:
    """Per-process CPU between two samples (percent of one core, like top)."""

    proc: Path = PROC
    ncpu: int = field(default_factory=_ncpu)
    _last: dict[int, tuple[str, int]] = field(default_factory=dict)
    _last_ticks: tuple[int, int] | None = None

    def _snapshot(self) -> dict[int, tuple[str, int]]:
        snap: dict[int, tuple[str, int]] = {}
        try:
            entries = list(os.scandir(self.proc))
        except OSError:
            return snap
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                with open(os.path.join(entry.path, "stat"), "rb") as f:
                    raw = f.read().decode(errors="replace")
            except OSError:
                continue
            head, _, rest = raw.rpartition(")")
            name = head.partition("(")[2]
            parts = rest.split()
            try:
                ticks = int(parts[11]) + int(parts[12])  # utime + stime
            except (IndexError, ValueError):
                continue
            snap[int(entry.name)] = (name, ticks)
        return snap

    def sample(self, top: int = 5) -> dict[str, Any] | None:
        """Top processes and total CPU use since the previous call.

        ``cpu`` per process is percent of one core (like top); ``cpu_busy``
        is percent of the whole CPU. None on the first call.
        """

        snap = self._snapshot()
        ticks = _cpu_ticks(self.proc)
        last, last_ticks = self._last, self._last_ticks
        self._last, self._last_ticks = snap, ticks
        if last_ticks is None or ticks is None or ticks[0] <= last_ticks[0]:
            return None
        all_delta = ticks[0] - last_ticks[0]
        wall = all_delta / self.ncpu  # one core's ticks in the interval
        rows = []
        for pid, (name, used_now) in snap.items():
            before = last.get(pid)
            used = used_now - before[1] if before and before[0] == name else used_now
            if used > 0:
                rows.append((used, pid, name))
        rows.sort(reverse=True)
        return {
            "cpu_busy": round(100 * (ticks[1] - last_ticks[1]) / all_delta, 1),
            "top": [
                {"pid": pid, "name": name, "cpu": round(100 * used / wall, 1)}
                for used, pid, name in rows[:top]
            ],
        }


def format_top(top: list[dict[str, Any]]) -> str:
    return ", ".join(f"{p['name']} {p['cpu']}%" for p in top) or "none"


@dataclass
class ThermalGuard:
    policy: Path = CPUFREQ
    mode_file: Path = MODE_FILE
    capped: bool = False

    def __post_init__(self) -> None:
        # A restart (or an earlier cap) leaves the clock lowered: own it, so
        # it is restored once cool.
        now = _int(self.policy / "scaling_max_freq")
        hw_max = _int(self.policy / "cpuinfo_max_freq")
        self.capped = bool(now and hw_max and now < hw_max)

    def mode(self) -> str:
        raw = (_read(self.mode_file) or "auto").split("=")[-1].strip().lower()
        return raw if raw in ("auto", "off", "cool") else "auto"

    def cap_khz(self) -> int | None:
        hw_min = _int(self.policy / "cpuinfo_min_freq")
        hw_max = _int(self.policy / "cpuinfo_max_freq")
        if hw_max is None:
            return None
        cap = max(CAP_KHZ, hw_min or 0)
        return cap if cap < hw_max else None

    def _set_max(self, khz: int) -> bool:
        try:
            (self.policy / "scaling_max_freq").write_text(f"{khz}\n")
        except OSError:
            return False
        return True

    def update(self, temp: float | None) -> str | None:
        """Cap or restore the clock for ``temp``; returns a log line on change."""

        mode = self.mode()
        cap = self.cap_khz()
        hw_max = _int(self.policy / "cpuinfo_max_freq")
        if cap is None or hw_max is None:
            return None
        want = self.capped
        if mode == "off":
            want = False
        elif mode == "cool":
            want = True
        elif temp is not None:
            if temp >= CAP_AT:
                want = True
            elif temp < RESTORE_AT:
                want = False
        if want == self.capped:
            return None
        if not self._set_max(cap if want else hw_max):
            return None
        self.capped = want
        t = f"{temp:.1f} C" if temp is not None else "unknown temperature"
        if want:
            return f"THERMAL: CPU clock capped at {cap // 1000} MHz ({t}, mode {mode})"
        return f"THERMAL: CPU clock back to {hw_max // 1000} MHz ({t}, mode {mode})"

    def release(self) -> None:
        hw_max = _int(self.policy / "cpuinfo_max_freq")
        if self.capped and hw_max is not None and self._set_max(hw_max):
            self.capped = False

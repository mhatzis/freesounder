"""Player diagnostics: why did it stop last time, and is its power OK?

Read-only. Uses the persistent journal (Storage=persistent in the image),
``vcgencmd get_throttled`` and the Pi 5 PMIC reset reason where present.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from freesounderd.reset import decode_throttled


async def _run(*cmd: str, wait: float = 6.0) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=wait)
    except (OSError, TimeoutError):
        return ""
    return out.decode(errors="replace")


# How the previous boot ended, from its last journal lines (most specific first).
_ENDINGS = [
    (
        re.compile(r"Power key pressed|power key: clean shutdown"),
        "power button pressed (clean shutdown)",
    ),
    (re.compile(r"factory reset", re.I), "factory reset"),
    (
        re.compile(r"Critical temperature|critical temperature reached", re.I),
        "overheated (critical temperature)",
    ),
    (re.compile(r"Out of memory|oom-kill", re.I), "ran out of memory"),
    (
        re.compile(r"reboot: Power down|Reached target.*Power-Off|System is powering down"),
        "clean power-off",
    ),
    (re.compile(r"reboot: Restarting|Reached target.*Reboot|System is rebooting"), "clean reboot"),
    (re.compile(r"reboot: System halted|Reached target.*System Halt"), "halted"),
]


def classify_ending(tail: str) -> str:
    for pattern, text in _ENDINGS:
        if pattern.search(tail):
            return text
    if not tail.strip():
        return "unknown (no log of the previous boot)"
    return "power lost or hard reset (no clean shutdown was logged)"


def pmic_reset_reason() -> str | None:
    """Pi 5: /proc/device-tree/chosen/power/power_reset (raw flags)."""

    path = Path("/proc/device-tree/chosen/power/power_reset")
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    return "0x" + raw.hex() if raw else None


async def collect() -> dict[str, Any]:
    throttled_raw = await _run("vcgencmd", "get_throttled")
    match = re.search(r"0x([0-9a-fA-F]+)", throttled_raw)
    flags = int(match.group(1), 16) if match else None
    temp = await _run("vcgencmd", "measure_temp")
    volts = await _run("vcgencmd", "pmic_read_adc", "EXT5V_V")
    boots = await _run("journalctl", "--list-boots", "--no-pager", "-q")
    prev_tail = await _run(
        "journalctl", "-b", "-1", "-n", "60", "--no-pager", "-q", "-o", "short-iso"
    )
    prev_errors = await _run(
        "journalctl", "-b", "-1", "-p", "err", "-n", "40", "--no-pager", "-q", "-o", "short-iso"
    )
    power_log = await _run(
        "journalctl", "-t", "freesounder-power", "-n", "20", "--no-pager", "-q", "-o", "short-iso"
    )
    ext5v = re.search(r"([0-9.]+)V", volts)
    return {
        "throttled": f"0x{flags:x}" if flags is not None else None,
        "throttled_flags": decode_throttled(flags),
        "temperature": temp.strip().removeprefix("temp=") or None,
        "input_voltage": f"{ext5v.group(1)} V" if ext5v else None,
        "pmic_reset_reason": pmic_reset_reason(),
        "boots": [line for line in boots.splitlines() if line.strip()][-5:],
        "previous_boot_ended": classify_ending(prev_tail),
        "previous_boot_tail": prev_tail.splitlines()[-25:],
        "previous_boot_errors": prev_errors.splitlines()[-40:],
        "power_log": power_log.splitlines(),
    }

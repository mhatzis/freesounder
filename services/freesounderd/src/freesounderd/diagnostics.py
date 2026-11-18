"""Player diagnostics: why did it stop last time, and is its power OK?

Read-only. Sources:
* the persistent journal (all machine ids merged, so a machine-id change on
  first boot or a reflash does not hide the previous boot),
* last-boot.json: the previous boot's final heartbeat (freesounderd.power),
* power-history.json: throttle flag changes with input voltage,
* vcgencmd and the Pi 5 device tree (PMIC reset reason, PSU current).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

from freesounderd.power import STATE, boot_id, cpu_temp, ext5v, pi5_power_info, read_json
from freesounderd.reset import decode_throttled


async def _run(*cmd: str, wait: float = 8.0) -> str:
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
        re.compile(r"POWER BUTTON: clean shutdown|Power key pressed|power key: clean shutdown"),
        "power button pressed (clean shutdown)",
    ),
    (re.compile(r"factory reset", re.I), "factory reset"),
    (
        re.compile(r"Critical temperature|critical temperature reached", re.I),
        "overheated (critical temperature)",
    ),
    (re.compile(r"Out of memory|oom-kill", re.I), "ran out of memory"),
    (
        re.compile(r"CLEAN SHUTDOWN started: poweroff|reboot: Power down|System is powering down"),
        "clean power-off",
    ),
    (
        re.compile(r"CLEAN SHUTDOWN started: reboot|reboot: Restarting|System is rebooting"),
        "clean reboot",
    ),
    (
        re.compile(
            r"CLEAN SHUTDOWN started: halt|reboot: System halted|Reached target.*System Halt"
        ),
        "halted",
    ),
]

NO_EARLIER_BOOT = "unknown (no earlier boot in the journal yet)"


def classify_ending(tail: str) -> str:
    for pattern, text in _ENDINGS:
        if pattern.search(tail):
            return text
    if not tail.strip():
        return NO_EARLIER_BOOT
    return "power lost or hard reset (no clean shutdown was logged)"


def previous_boot(list_boots_json: str, current: str) -> str | None:
    """Boot id of the boot before ``current`` from `journalctl --list-boots -o json`.

    Works across machine-id changes (the list is taken with --merge) and on
    the first boot (returns None).
    """

    try:
        boots = json.loads(list_boots_json or "[]")
    except ValueError:
        return None
    if not isinstance(boots, list):
        return None
    ordered = sorted(
        (b for b in boots if isinstance(b, dict) and b.get("boot_id")),
        key=lambda b: b.get("first_entry", 0),
    )
    ids = [b["boot_id"].replace("-", "") for b in ordered]
    me = current.replace("-", "")
    if me in ids:
        idx = ids.index(me)
        return ids[idx - 1] if idx > 0 else None
    return ids[-1] if ids else None


def describe_last_boot(last: Any) -> dict[str, Any] | None:
    if not isinstance(last, dict):
        return None
    when = last.get("time")
    return {
        "clean_shutdown": bool(last.get("clean")),
        "shutdown": last.get("shutdown"),
        "last_heartbeat": time.strftime("%Y-%m-%d %H:%M:%S %z", time.localtime(when))
        if when
        else None,
        "uptime_at_end_min": round(float(last.get("uptime") or 0) / 60, 1),
        "ext5v_last": last.get("ext5v"),
        "ext5v_min": last.get("min_ext5v"),
        "throttled": last.get("throttled"),
        "throttled_flags": decode_throttled(int(last["throttled"], 16))
        if last.get("throttled")
        else [],
        "temp": last.get("temp"),
    }


async def collect(state: Path = STATE) -> dict[str, Any]:
    throttled_raw = await _run("vcgencmd", "get_throttled")
    match = re.search(r"0x([0-9a-fA-F]+)", throttled_raw)
    flags = int(match.group(1), 16) if match else None
    boots_json = await _run(
        "journalctl", "--merge", "--list-boots", "-o", "json", "--no-pager", "-q"
    )
    prev = previous_boot(boots_json, boot_id())
    if prev:
        sel = ["-b", prev]
        prev_tail = await _run(
            "journalctl", "--merge", *sel, "-n", "60", "--no-pager", "-q", "-o", "short-iso"
        )
        prev_errors = await _run(
            "journalctl",
            "--merge",
            *sel,
            "-p",
            "warning",
            "-n",
            "40",
            "--no-pager",
            "-q",
            "-o",
            "short-iso",
        )
    else:
        prev_tail = prev_errors = ""
    power_log = await _run(
        "journalctl",
        "--merge",
        "-t",
        "freesounder-power",
        "-t",
        "freesounder-buttons",
        "-n",
        "30",
        "--no-pager",
        "-q",
        "-o",
        "short-iso",
    )
    last = describe_last_boot(read_json(state / "last-boot.json"))
    ended = classify_ending(prev_tail)
    if last is not None and ended in (
        NO_EARLIER_BOOT,
        "power lost or hard reset (no clean shutdown was logged)",
    ):
        # The heartbeat file survives even if the journal lost its last minutes.
        ended = (
            f"clean {last['shutdown'] or 'shutdown'}"
            if last["clean_shutdown"]
            else f"stopped without a clean shutdown around {last['last_heartbeat']} "
            f"(up {last['uptime_at_end_min']} min)"
        )
    history = read_json(state / "power-history.json")
    hb = read_json(state / "heartbeat.json")
    volts = ext5v()
    return {
        "previous_boot_ended": ended,
        "previous_boot": last,
        "throttled": f"0x{flags:x}" if flags is not None else None,
        "throttled_flags": decode_throttled(flags),
        "temperature": f"{t:.1f}'C" if (t := cpu_temp()) is not None else None,
        "input_voltage": f"{volts} V" if volts is not None else None,
        "input_voltage_min_this_boot": (hb or {}).get("min_ext5v")
        if isinstance(hb, dict)
        else None,
        **pi5_power_info(),
        "power_history": (history if isinstance(history, list) else [])[-20:],
        "boots_in_journal": len(json.loads(boots_json))
        if boots_json.strip().startswith("[")
        else 0,
        "previous_boot_tail": prev_tail.splitlines()[-25:],
        "previous_boot_errors": prev_errors.splitlines()[-40:],
        "power_log": power_log.splitlines(),
    }

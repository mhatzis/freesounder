"""Identify the board without playing audio (activity LED only)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_LED_CANDIDATES = (
    Path("/sys/class/leds/ACT/brightness"),
    Path("/sys/class/leds/led0/brightness"),
    Path("/sys/class/leds/PWR/brightness"),
)


async def blink_activity_led(*, times: int = 6, interval: float = 0.15) -> None:
    """Blink an onboard LED if sysfs allows it. Never plays a sound."""

    led = next((path for path in _LED_CANDIDATES if path.exists()), None)
    if led is None:
        logger.info("Identify: no activity LED available")
        return
    try:
        original = led.read_text(encoding="utf-8")
    except OSError:
        original = "0"
    try:
        for _ in range(times):
            _write(led, "1")
            await asyncio.sleep(interval)
            _write(led, "0")
            await asyncio.sleep(interval)
    finally:
        _write(led, original.strip() or "0")


def _write(path: Path, value: str) -> None:
    try:
        path.write_text(value, encoding="utf-8")
    except OSError:
        pass

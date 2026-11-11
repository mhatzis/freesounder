"""Thin wrapper around systemd and files, so tests can run without a Pi."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class System:
    """Manage units and generated config files.

    With ``managed=False`` nothing is executed; calls are recorded in
    ``self.calls`` so tests can assert on them.
    """

    def __init__(self, managed: bool = True) -> None:
        self.managed = managed
        self.calls: list[tuple[str, ...]] = []
        self.active: dict[str, bool] = {}

    async def systemctl(self, *args: str) -> int:
        self.calls.append(("systemctl", *args))
        if args and args[0] in {"start", "restart"}:
            for unit in args[1:]:
                self.active[unit] = True
        if args and args[0] == "stop":
            for unit in args[1:]:
                self.active[unit] = False
        if not self.managed:
            return 0
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl",
                *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, err = await asyncio.wait_for(proc.communicate(), timeout=30)
        except (OSError, TimeoutError):
            logger.warning("systemctl %s failed", " ".join(args), exc_info=True)
            return 1
        if proc.returncode:
            logger.warning("systemctl %s: %s", " ".join(args), (err or b"").decode()[:300])
        return proc.returncode or 0

    async def is_active(self, unit: str) -> bool:
        if not self.managed:
            return self.active.get(unit, False)
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl",
                "is-active",
                "--quiet",
                unit,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=10)
        except (OSError, TimeoutError):
            return False
        return proc.returncode == 0

    async def ensure_active(self, unit: str) -> None:
        if not await self.is_active(unit):
            await self.systemctl("start", unit)

    async def ensure_stopped(self, unit: str) -> None:
        if await self.is_active(unit):
            await self.systemctl("stop", unit)

    def write_if_changed(self, path: Path, text: str, mode: int = 0o644) -> bool:
        try:
            if path.read_text(encoding="utf-8") == text:
                return False
        except OSError:
            pass
        self.calls.append(("write", str(path)))
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.chmod(tmp, mode)
        os.replace(tmp, path)
        return True

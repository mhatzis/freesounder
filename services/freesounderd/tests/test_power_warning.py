"""The app is told when a player's supply is too weak (0.6.19)."""

from __future__ import annotations

import json
from pathlib import Path

from freesounderd.power import power_warning

from .conftest import Lan, settle
from .test_daemon import setup_household


def _beat(tmp_path: Path, **beat) -> Path:
    (tmp_path / "heartbeat.json").write_text(json.dumps(beat))
    return tmp_path


def test_under_voltage_since_boot_warns_with_the_lowest_voltage(tmp_path: Path) -> None:
    msg = power_warning(_beat(tmp_path, throttled="0x50000", min_ext5v=4.726))
    assert msg and "4.73 V" in msg and "27 W" in msg


def test_a_dip_alone_warns_and_a_good_supply_does_not(tmp_path: Path) -> None:
    assert power_warning(_beat(tmp_path, throttled="0x0", min_ext5v=4.7))
    assert power_warning(_beat(tmp_path, throttled="0x0", min_ext5v=5.01)) is None
    assert power_warning(tmp_path / "missing") is None


async def test_rooms_carry_the_warning(lan: Lan, make_player) -> None:
    (a,) = await setup_household(make_player, ("10.0.0.1", "Pi 5"))
    _beat(a.settings.state_dir, throttled="0x50000", min_ext5v=4.726)
    await settle(lan, [a])
    rooms = (await a.client.get("/api/v2/rooms")).json()
    assert "too weak" in rooms[0]["power_warning"]

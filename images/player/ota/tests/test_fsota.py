"""Unit + scenario tests for the A/B slot logic (RAUC custom backend, trial boot, health, rollback).

FakePi models the Raspberry Pi firmware: it reads autoboot.txt the documented way, honours the one-shot
tryboot flag, and boots the selected slot (rauc.slot= on the command line).
"""
from __future__ import annotations

import io
import json
import os
import subprocess
from pathlib import Path

import pytest

import fsota


class FakePi:
    def __init__(self, tmp: Path):
        self.bootsel = tmp / "bootsel"
        self.bootsel.mkdir()
        (self.bootsel / "autoboot.txt").write_text(fsota.render_autoboot("A"))
        self.state = tmp / "data-ota"
        self.cmdline = tmp / "cmdline"
        self.dt = tmp / "dt"
        self.dt.mkdir()
        self.tryboot_flag = False
        self.reboots: list[str] = []
        self.env = fsota.Env(
            state_dir=self.state, cmdline=self.cmdline, dt_bootloader=self.dt, bootsel_dir=self.bootsel,
            bootsel_dev="unused", run=self._run,
        )
        self.sys = fsota.System(self.env)
        self.boot()

    # what systemctl/rauc would do
    def _run(self, args, **kw):
        if args[:2] == ["systemctl", "reboot"]:
            self.reboots.append(" ".join(args))
            self.tryboot_flag = any("tryboot" in a for a in args)
        return subprocess.CompletedProcess(args, 0, "", "")

    def boot(self) -> str:
        """Power on: the firmware picks the partition, then clears the one-shot flag."""
        part = fsota.firmware_select((self.bootsel / "autoboot.txt").read_text(), self.tryboot_flag)
        (self.dt / "tryboot").write_bytes(int(self.tryboot_flag).to_bytes(4, "big"))
        (self.dt / "partition").write_bytes(part.to_bytes(4, "big"))
        self.tryboot_flag = False
        slot = fsota.PART_TO_SLOT[part]
        self.cmdline.write_text(f"root=PARTUUID=46534f54-0{fsota.SLOTS[slot]['root']} rw rauc.slot={slot}\n")
        fsota.cmd_boot_check(self.sys)
        return slot

    def backend(self, *args) -> tuple[int, str]:
        out = io.StringIO()
        rc = fsota.backend(list(args), self.sys, out=out)
        return rc, out.getvalue().strip()

    def rauc_install(self) -> None:
        """The backend calls RAUC makes around writing the other slot group."""
        target = fsota.other(self.sys.booted())
        assert self.backend("set-state", target, "bad")[0] == 0
        # ... slot images written here ...
        assert self.backend("set-state", target, "good")[0] == 0
        assert self.backend("set-primary", target)[0] == 0

    def health(self, ok: bool) -> bool:
        check = fsota.Check("fake", lambda: (ok, "fine" if ok else "freesounderd did not answer"))
        os.environ["FSOTA_NO_RAUC"] = "1"
        try:
            return fsota.cmd_health(self.sys, timeout=0, checks=[check],
                                    reboot=lambda: self._run(["systemctl", "reboot"]))
        finally:
            del os.environ["FSOTA_NO_RAUC"]

    def state_json(self) -> dict:
        return json.loads((self.state / "state.json").read_text())


@pytest.fixture
def pi(tmp_path):
    return FakePi(tmp_path)


# ----------------------------------------------------------------------------- autoboot.txt
def test_autoboot_variants_have_equal_length_and_swap_partitions():
    a, b = fsota.render_autoboot("A"), fsota.render_autoboot("B")
    assert len(a) == len(b)
    assert fsota.firmware_select(a, tryboot=False) == 2
    assert fsota.firmware_select(a, tryboot=True) == 3
    assert fsota.firmware_select(b, tryboot=False) == 3
    assert fsota.firmware_select(b, tryboot=True) == 2
    assert "tryboot_a_b=1" in a


def test_firmware_select_rules():
    assert fsota.firmware_select("", tryboot=False) == 0
    text = "[all]\nboot_partition=2\n[none]\nboot_partition=9\n[tryboot]\nboot_partition=3\n"
    assert fsota.firmware_select(text, tryboot=False) == 2
    assert fsota.firmware_select(text, tryboot=True) == 3
    with pytest.raises(fsota.OtaError):
        fsota.default_slot_from_autoboot("[all]\nboot_partition=1\n")


def test_commit_rewrites_autoboot_in_place(pi):
    path = pi.bootsel / "autoboot.txt"
    ino, size = path.stat().st_ino, path.stat().st_size
    pi.sys.bootsel.write_default("B")
    assert path.stat().st_ino == ino and path.stat().st_size == size
    assert fsota.default_slot_from_autoboot(path.read_text()) == "B"


# ----------------------------------------------------------------------------- backend
def test_backend_defaults_on_a_fresh_card(pi):
    assert pi.backend("get-current") == (0, "A")
    assert pi.backend("get-primary") == (0, "A")
    assert pi.backend("get-state", "A") == (0, "good")
    assert pi.backend("get-state", "B") == (0, "bad")
    assert pi.backend("set-state", "C", "good")[0] == 1
    assert pi.backend("frobnicate")[0] == 2


def test_marking_the_other_slot_good_does_not_commit(pi):
    pi.rauc_install()
    assert pi.backend("get-primary") == (0, "B")
    assert pi.sys.default() == "A", "install alone must never change the normal boot slot"


def test_missing_rauc_slot_on_cmdline(pi):
    pi.cmdline.write_text("root=/dev/mmcblk0p5\n")
    with pytest.raises(fsota.OtaError):
        pi.sys.booted()


# ----------------------------------------------------------------------------- scenarios
def test_update_success_commits_after_health(pi):
    pi.rauc_install()
    fsota.trial_reboot(pi.sys)
    assert pi.boot() == "B"
    st = pi.state_json()
    assert st["trial"]["slot"] == "B"
    assert pi.sys.in_trial()
    assert pi.health(ok=True)
    assert pi.sys.default() == "B"
    st = pi.state_json()
    assert st["pending"] is None and st["trial"] is None
    assert st["slots"] == {"A": "good", "B": "good"}
    assert st["last_result"]["result"] == "committed"
    # Next ordinary power cycle stays on B.
    assert pi.boot() == "B"
    assert not pi.sys.in_trial()


def test_failed_health_check_rolls_back(pi):
    pi.rauc_install()
    fsota.trial_reboot(pi.sys)
    assert pi.boot() == "B"
    assert not pi.health(ok=False)
    assert pi.reboots[-1] == "systemctl reboot"  # a normal reboot: tryboot flag not set
    assert pi.boot() == "A"
    st = pi.state_json()
    assert st["slots"]["B"] == "bad"
    assert st["pending"] is None
    assert st["last_result"]["result"] == "rolled_back"
    assert "freesounderd did not answer" in st["last_result"]["reason"]
    assert pi.sys.default() == "A"


def test_crash_or_power_cut_during_trial_rolls_back(pi):
    pi.rauc_install()
    fsota.trial_reboot(pi.sys)
    assert pi.boot() == "B"
    # Kernel hang -> watchdog reset (or power cut): no health check ran, the flag is gone.
    assert pi.boot() == "A"
    st = pi.state_json()
    assert st["last_result"]["result"] == "rolled_back"
    assert st["slots"]["B"] == "bad"


def test_power_cut_between_install_and_trial_reboot_keeps_old_version(pi):
    pi.rauc_install()
    assert pi.boot() == "A"  # never trial-booted
    assert pi.sys.default() == "A"
    assert pi.state_json()["pending"] == "B"  # can still be trial-booted later


def test_manual_rollback_after_commit(pi):
    pi.rauc_install()
    fsota.trial_reboot(pi.sys)
    pi.boot()
    pi.health(ok=True)
    assert fsota.cmd_rollback(pi.sys, reboot=True) == 0
    assert "tryboot" in pi.reboots[-1]
    assert pi.boot() == "A"
    assert pi.health(ok=True)
    assert pi.sys.default() == "A"


def test_rollback_refused_without_a_known_good_other_slot(pi):
    assert fsota.cmd_rollback(pi.sys, reboot=False) == 1


def test_two_updates_in_a_row_alternate_slots(pi):
    for expected in ("B", "A", "B"):
        pi.rauc_install()
        fsota.trial_reboot(pi.sys)
        assert pi.boot() == expected
        assert pi.health(ok=True)
        assert pi.sys.default() == expected


def test_health_is_a_no_op_on_a_normal_boot(pi):
    assert pi.health(ok=False)  # nothing to judge, no reboot
    assert pi.reboots == []


def test_install_refused_during_trial(pi):
    pi.rauc_install()
    fsota.trial_reboot(pi.sys)
    pi.boot()
    assert fsota.cmd_install(pi.sys, "/x.raucb", reboot=False) == 1


# ----------------------------------------------------------------------------- health loop
def test_run_checks_retries_until_pass_then_times_out():
    t = [0.0]
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        return calls["n"] >= 3, f"try {calls['n']}"

    ok, res = fsota.run_checks([fsota.Check("x", flaky)], timeout=100, interval=5,
                               clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + s))
    assert ok and calls["n"] == 3

    ok, res = fsota.run_checks([fsota.Check("x", lambda: (False, "down"))], timeout=12, interval=5,
                               clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + s))
    assert not ok and res["x"] == "FAIL: down"


def test_status_reports_slots(pi):
    info = fsota.cmd_status(pi.sys)
    assert info["booted"] == "A" and info["default"] == "A" and info["in_trial"] is False
    assert info["tryboot_flag"] is False

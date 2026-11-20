"""FreeSounder OTA: A/B slot bookkeeping for RAUC on Raspberry Pi tryboot.

Partition layout (MBR, fixed disk id 0x46534f54 so PARTUUIDs are the same on every player):

    p1  FAT   bootsel   autoboot.txt (+ bootcode.bin for Pi 3 / Zero 2 W)
    p2  FAT   boot A    firmware, kernel, cmdline_a.txt / cmdline_b.txt
    p3  FAT   boot B
    p4  extended
    p5  ext4  root A
    p6  ext4  root B
    p7  ext4  data      settings, household, Wi-Fi, pairings, OTA state (created on first boot)

The firmware boots ``[all] boot_partition`` normally and ``[tryboot] boot_partition`` once after
``reboot '0 tryboot'``. An update is written to the other slot, trial-booted with tryboot, and committed
(autoboot.txt default swapped) only when the health check passes. Any crash, hang (watchdog) or
failed check before the commit falls back to the old slot on the next boot, because the tryboot flag is
one-shot.

Used as:
  * RAUC custom bootloader backend: ``rauc-backend get-primary|set-primary|get-state|set-state|get-current``
  * ``freesounder-ota status|install|boot-check|health|rollback``

Python 3 standard library only (the system python3, not the freesounderd venv).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

DISK_ID = "46534f54"
SLOTS = {"A": {"boot": 2, "root": 5}, "B": {"boot": 3, "root": 6}}
PART_TO_SLOT = {v["boot"]: k for k, v in SLOTS.items()}
BOOTSEL_PART = 1
DATA_PART = 7


def other(slot: str) -> str:
    return "B" if slot == "A" else "A"


def partuuid(part: int) -> str:
    return f"{DISK_ID}-{part:02d}"


# ----------------------------------------------------------------------------- autoboot.txt
AUTOBOOT_HEADER = "# FreeSounder A/B boot selection. Written by freesounder-ota; do not edit.\n"


def render_autoboot(default: str) -> str:
    """Both variants have the same length, so a commit is one in-place sector rewrite."""
    return (
        AUTOBOOT_HEADER
        + "[all]\n"
        + "tryboot_a_b=1\n"
        + f"boot_partition={SLOTS[default]['boot']}\n"
        + "[tryboot]\n"
        + f"boot_partition={SLOTS[other(default)]['boot']}\n"
    )


def firmware_select(text: str, tryboot: bool) -> int:
    """What the Raspberry Pi bootloader picks from autoboot.txt (model of the documented behaviour).

    Sections: ``[all]`` always applies, ``[tryboot]`` only when the tryboot flag is set, ``[none]`` never.
    Later assignments win. No (valid) boot_partition -> 0, i.e. "first bootable FAT partition".
    """
    section = "all"
    chosen = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() != "boot_partition":
            continue
        active = section == "all" or (section == "tryboot" and tryboot)
        if active:
            try:
                chosen = int(value.strip())
            except ValueError:
                pass
    return chosen


def default_slot_from_autoboot(text: str) -> str:
    part = firmware_select(text, tryboot=False)
    if part not in PART_TO_SLOT:
        raise OtaError(f"autoboot.txt selects partition {part}, which is not a FreeSounder boot slot")
    return PART_TO_SLOT[part]


class OtaError(RuntimeError):
    pass


# ----------------------------------------------------------------------------- environment
@dataclass
class Env:
    """Everything that touches the real system, overridable for tests (FSOTA_* variables)."""

    state_dir: Path
    cmdline: Path
    dt_bootloader: Path
    bootsel_dir: Path | None  # tests / simulation: a directory holding autoboot.txt
    bootsel_dev: str
    run: Callable[..., subprocess.CompletedProcess]
    bootsel_mtools: bool = False  # simulation: edit p1 with mtools instead of mounting it

    @classmethod
    def from_environ(cls) -> "Env":
        e = os.environ
        bootsel_dir = e.get("FSOTA_BOOTSEL_DIR")
        return cls(
            state_dir=Path(e.get("FSOTA_STATE_DIR", "/data/ota")),
            cmdline=Path(e.get("FSOTA_CMDLINE", "/proc/cmdline")),
            dt_bootloader=Path(e.get("FSOTA_DT_BOOTLOADER", "/proc/device-tree/chosen/bootloader")),
            bootsel_dir=Path(bootsel_dir) if bootsel_dir else None,
            bootsel_dev=e.get("FSOTA_BOOTSEL_DEV", f"/dev/disk/by-partuuid/{partuuid(BOOTSEL_PART)}"),
            run=subprocess.run,
            bootsel_mtools=e.get("FSOTA_BOOTSEL_MTOOLS") == "1",
        )


class Bootsel:
    """Read/write autoboot.txt on p1. On the player p1 is mounted only while it is touched."""

    def __init__(self, env: Env):
        self.env = env

    def _with_dir(self, fn: Callable[[Path], object], write: bool = False):
        if self.env.bootsel_dir is not None:
            return fn(self.env.bootsel_dir)
        mnt = Path("/run/freesounder/bootsel")
        mnt.mkdir(parents=True, exist_ok=True)
        opts = "rw,sync" if write else "ro"
        self.env.run(["mount", "-t", "vfat", "-o", opts, self.env.bootsel_dev, str(mnt)], check=True)
        try:
            return fn(mnt)
        finally:
            self.env.run(["sync"], check=False)
            self.env.run(["umount", str(mnt)], check=False)

    def _mtools_env(self) -> dict:
        return {**os.environ, "MTOOLS_SKIP_CHECK": "1"}

    def read(self) -> str:
        if self.env.bootsel_mtools:
            r = self.env.run(["mtype", "-i", self.env.bootsel_dev, "::/autoboot.txt"], check=True,
                             capture_output=True, text=True, env=self._mtools_env())
            return r.stdout
        return self._with_dir(lambda d: (d / "autoboot.txt").read_text())  # type: ignore[return-value]

    def write_default(self, slot: str) -> None:
        text = render_autoboot(slot).encode()
        if self.env.bootsel_mtools:
            tmp = self.env.state_dir / "autoboot.new"
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(text)
            self.env.run(["mcopy", "-o", "-i", self.env.bootsel_dev, str(tmp), "::/autoboot.txt"], check=True,
                         env=self._mtools_env())
            tmp.unlink()
            if self.read().encode() != text:
                raise OtaError("autoboot.txt did not read back as written")
            return

        def do(d: Path) -> None:
            path = d / "autoboot.txt"
            if path.exists() and path.stat().st_size == len(text):
                # Same length: overwrite the existing data sector in place (no FAT/dirent change).
                fd = os.open(path, os.O_RDWR)
                try:
                    os.pwrite(fd, text, 0)
                    os.fsync(fd)
                finally:
                    os.close(fd)
            else:
                tmp = d / "autoboot.new"
                tmp.write_bytes(text)
                fd = os.open(tmp, os.O_RDONLY)
                os.fsync(fd)
                os.close(fd)
                os.replace(tmp, path)
            dfd = os.open(d, os.O_RDONLY)
            try:
                os.fsync(dfd)
            except OSError:
                pass
            finally:
                os.close(dfd)
            if path.read_bytes() != text:
                raise OtaError("autoboot.txt did not read back as written")

        self._with_dir(do, write=True)


# ----------------------------------------------------------------------------- state
class State:
    """/data/ota/state.json: pending trial slot, per-slot good/bad, last result. Atomic writes."""

    def __init__(self, env: Env):
        self.path = env.state_dir / "state.json"
        self.data: dict = {"pending": None, "slots": {}, "last_result": None, "trial": None}
        if self.path.exists():
            try:
                self.data.update(json.loads(self.path.read_text()))
            except (OSError, ValueError):
                pass

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True) + "\n")
        fd = os.open(tmp, os.O_RDONLY)
        os.fsync(fd)
        os.close(fd)
        os.replace(tmp, self.path)

    def result(self, kind: str, **extra) -> None:
        self.data["last_result"] = {"result": kind, "time": int(time.time()), **extra}


# ----------------------------------------------------------------------------- the system view
class System:
    def __init__(self, env: Env | None = None):
        self.env = env or Env.from_environ()
        self.bootsel = Bootsel(self.env)

    def booted(self) -> str:
        for tok in self.env.cmdline.read_text().split():
            if tok.startswith("rauc.slot="):
                slot = tok.split("=", 1)[1]
                if slot in SLOTS:
                    return slot
        raise OtaError("rauc.slot= missing from the kernel command line")

    def default(self) -> str:
        return default_slot_from_autoboot(self.bootsel.read())

    def _dt_int(self, name: str) -> int | None:
        p = self.env.dt_bootloader / name
        try:
            raw = p.read_bytes()
        except OSError:
            return None
        return int.from_bytes(raw[:4], "big") if raw else None

    def tryboot_flag(self) -> bool | None:
        v = self._dt_int("tryboot")
        return None if v is None else bool(v)

    def in_trial(self) -> bool:
        """Booted the non-default slot: the only way there is a tryboot (one-shot) boot."""
        return self.booted() != self.default()


# ----------------------------------------------------------------------------- RAUC backend
def backend(argv: list[str], sysm: System | None = None, out=sys.stdout) -> int:
    sysm = sysm or System()
    if not argv:
        print("usage: rauc-backend get-primary|set-primary S|get-state S|set-state S good|bad|get-current",
              file=sys.stderr)
        return 2
    cmd, args = argv[0], argv[1:]
    st = State(sysm.env)
    try:
        if cmd == "get-current":
            print(sysm.booted(), file=out)
            return 0
        if cmd == "get-primary":
            print(st.data.get("pending") or sysm.default(), file=out)
            return 0
        if cmd == "set-primary":
            slot = args[0]
            if slot not in SLOTS:
                raise OtaError(f"unknown slot {slot}")
            st.data["pending"] = None if slot == sysm.default() else slot
            st.save()
            return 0
        if cmd == "get-state":
            slot = args[0]
            state = st.data.get("slots", {}).get(slot)
            if state is None:
                state = "good" if slot == sysm.default() else "bad"
            print(state, file=out)
            return 0
        if cmd == "set-state":
            slot, value = args[0], args[1]
            if slot not in SLOTS or value not in ("good", "bad"):
                raise OtaError(f"bad set-state arguments {args}")
            st.data.setdefault("slots", {})[slot] = value
            booted, default = sysm.booted(), sysm.default()
            if value == "good" and slot == booted and booted != default:
                # Health check passed in a trial boot: make it permanent.
                sysm.bootsel.write_default(slot)
                # The slot we came from was running fine: it is the known-good rollback target.
                st.data["slots"][default] = "good"
                st.data["pending"] = None
                st.data["trial"] = None
                st.result("committed", slot=slot)
            elif value == "bad" and st.data.get("pending") == slot:
                st.data["pending"] = None
            st.save()
            return 0
    except (OtaError, OSError, IndexError) as exc:
        print(f"rauc-backend: {exc}", file=sys.stderr)
        return 1
    print(f"rauc-backend: unknown command {cmd}", file=sys.stderr)
    return 2


# ----------------------------------------------------------------------------- health check
@dataclass
class Check:
    name: str
    fn: Callable[[], tuple[bool, str]]


def check_data_mounted(data: Path = Path("/data")) -> tuple[bool, str]:
    if not os.path.ismount(data):
        return False, f"{data} is not mounted"
    probe = data / "ota" / ".health-probe"
    try:
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text(str(time.time()))
        probe.unlink()
    except OSError as exc:
        return False, f"{data} is not writable: {exc}"
    return True, "ok"


def check_http_health(url: str = "http://127.0.0.1:8080/health") -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 (localhost)
            body = json.loads(resp.read().decode() or "{}")
        return True, f"freesounderd {body.get('version', '?')}"
    except Exception as exc:  # noqa: BLE001
        return False, f"freesounderd not healthy: {exc}"


def check_boot_finished(run=subprocess.run) -> tuple[bool, str]:
    r = run(["systemctl", "is-system-running"], capture_output=True, text=True, check=False)
    state = (r.stdout or "").strip()
    if state in ("running", "degraded"):
        return True, state
    return False, f"system is {state or 'unknown'}"


def check_network(run=subprocess.run) -> tuple[bool, str]:
    r = run(["nmcli", "-t", "-f", "STATE", "general"], capture_output=True, text=True, check=False)
    state = (r.stdout or "").strip()
    if state.startswith("connected"):
        return True, state
    return False, f"network is {state or 'unknown'}"


def default_checks(need_network: bool) -> list[Check]:
    checks = [
        Check("data", check_data_mounted),
        Check("boot", check_boot_finished),
        Check("freesounderd", check_http_health),
    ]
    if need_network:
        checks.append(Check("network", check_network))
    return checks


def run_checks(checks: list[Check], timeout: float, interval: float = 5.0,
               clock=time.monotonic, sleep=time.sleep) -> tuple[bool, dict[str, str]]:
    deadline = clock() + timeout
    while True:
        results = {}
        ok = True
        for c in checks:
            passed, detail = c.fn()
            results[c.name] = ("ok: " if passed else "FAIL: ") + detail
            ok = ok and passed
        if ok or clock() >= deadline:
            return ok, results
        sleep(interval)


# ----------------------------------------------------------------------------- CLI
def _rauc(sysm: System, *args: str) -> subprocess.CompletedProcess:
    return sysm.env.run(["rauc", *args], check=False, capture_output=True, text=True)


def mark(sysm: System, good: bool) -> None:
    """Prefer RAUC (keeps its status file in step); fall back to the backend directly."""
    if os.environ.get("FSOTA_NO_RAUC") != "1":
        r = _rauc(sysm, "status", "mark-good" if good else "mark-bad")
        if r.returncode == 0:
            return
        print(f"rauc status mark failed ({r.stderr.strip()}); using the backend directly", file=sys.stderr)
    rc = backend(["set-state", sysm.booted(), "good" if good else "bad"], sysm)
    if rc:
        raise OtaError("could not record the slot state")


def trial_reboot(sysm: System) -> None:
    sysm.env.run(["sync"], check=False)
    sysm.env.run(["systemctl", "reboot", "--reboot-argument=0 tryboot"], check=False)


def cmd_status(sysm: System) -> dict:
    st = State(sysm.env)
    info: dict = {"pending": st.data.get("pending"), "slots": st.data.get("slots", {}),
                  "last_result": st.data.get("last_result"), "trial": st.data.get("trial")}
    try:
        info["booted"] = sysm.booted()
        info["default"] = sysm.default()
        info["in_trial"] = info["booted"] != info["default"]
    except (OtaError, OSError) as exc:
        info["error"] = str(exc)
    info["tryboot_flag"] = sysm.tryboot_flag()
    return info


def cmd_boot_check(sysm: System) -> str:
    """Every boot, early. Notices a trial that never got committed (we are back on the default)."""
    st = State(sysm.env)
    booted, default = sysm.booted(), sysm.default()
    pending = st.data.get("pending")
    trial = st.data.get("trial")
    if booted == default:
        if trial and trial.get("slot") != booted:
            failed = trial["slot"]
            st.data.setdefault("slots", {})[failed] = "bad"
            st.data["pending"] = None
            st.data["trial"] = None
            st.result("rolled_back", slot=failed, reason=trial.get("reason") or "the new version did not start")
            st.save()
            return f"rolled back: {failed} did not pass its trial boot"
        return "normal boot"
    # Trial boot of the non-default slot.
    if pending != booted:
        # e.g. someone ran `reboot 3` by hand; treat it as a trial anyway so it can only commit via health.
        st.data["pending"] = booted
    st.data["trial"] = {"slot": booted, "started": int(time.time()), "reason": None}
    st.save()
    return f"trial boot of slot {booted}"


def cmd_health(sysm: System, timeout: float, checks: list[Check] | None = None,
               reboot: Callable[[], None] | None = None) -> bool:
    if not sysm.in_trial():
        print("not a trial boot; nothing to commit")
        return True
    st = State(sysm.env)
    need_network = bool(st.data.get("need_network", True))
    if checks is None and os.environ.get("FSOTA_FAKE_HEALTH") in ("pass", "fail"):
        # Simulation only (images/player/ota/simulate.sh): no freesounderd/systemd/NetworkManager there.
        verdict = os.environ["FSOTA_FAKE_HEALTH"] == "pass"
        checks = [Check("simulated", lambda: (verdict, "simulated " + ("pass" if verdict else "failure")))]
    ok, results = run_checks(checks if checks is not None else default_checks(need_network), timeout)
    for k, v in results.items():
        print(f"{k}: {v}")
    if ok:
        mark(sysm, True)
        print(f"slot {sysm.booted()} committed")
        return True
    st = State(sysm.env)
    reason = "; ".join(v for v in results.values() if v.startswith("FAIL"))
    if st.data.get("trial"):
        st.data["trial"]["reason"] = reason
        st.save()
    mark(sysm, False)
    print(f"health check failed ({reason}); rebooting into the previous version")
    (reboot or (lambda: sysm.env.run(["systemctl", "reboot"], check=False)))()
    return False


def cmd_install(sysm: System, bundle: str, reboot: bool) -> int:
    if sysm.in_trial():
        print("a trial boot is in progress; wait for it to commit or roll back", file=sys.stderr)
        return 1
    st = State(sysm.env)
    st.data["need_network"] = True
    st.save()
    r = sysm.env.run(["rauc", "install", bundle], check=False)
    if r.returncode != 0:
        st = State(sysm.env)
        st.result("install_failed", bundle=os.path.basename(bundle))
        st.save()
        return r.returncode or 1
    st = State(sysm.env)
    if st.data.get("pending") != other(sysm.booted()):
        print("rauc install finished but the other slot is not pending", file=sys.stderr)
        return 1
    st.result("installed", slot=st.data["pending"])
    st.save()
    if reboot:
        trial_reboot(sysm)
    return 0


def cmd_rollback(sysm: System, reboot: bool) -> int:
    """Go back to the other slot, if it still holds a good version. Trial-booted like an update."""
    st = State(sysm.env)
    target = other(sysm.booted())
    if st.data.get("slots", {}).get(target) != "good":
        print(f"slot {target} is not known-good; nothing to roll back to", file=sys.stderr)
        return 1
    st.data["pending"] = target
    st.save()
    if reboot:
        trial_reboot(sysm)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    prog = os.path.basename(sys.argv[0])
    if prog == "rauc-backend":
        return backend(argv)
    if not argv:
        print("usage: freesounder-ota status|install BUNDLE [--no-reboot]|boot-check|health [--timeout S]|rollback",
              file=sys.stderr)
        return 2
    sysm = System()
    cmd = argv[0]
    try:
        if cmd == "status":
            print(json.dumps(cmd_status(sysm), indent=2, sort_keys=True))
            return 0
        if cmd == "boot-check":
            print(cmd_boot_check(sysm))
            return 0
        if cmd == "health":
            timeout = 300.0
            if "--timeout" in argv:
                timeout = float(argv[argv.index("--timeout") + 1])
            return 0 if cmd_health(sysm, timeout) else 1
        if cmd == "install":
            return cmd_install(sysm, argv[1], reboot="--no-reboot" not in argv)
        if cmd == "rollback":
            return cmd_rollback(sysm, reboot="--no-reboot" not in argv)
        if cmd == "backend":
            return backend(argv[1:], sysm)
    except (OtaError, OSError) as exc:
        print(f"freesounder-ota: {exc}", file=sys.stderr)
        return 1
    print(f"freesounder-ota: unknown command {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

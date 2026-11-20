"""data-setup (bind + overlay of state onto /data) in a scratch root. Needs root for mounts: runs the
script through `sudo -n` when not root, and skips when that is not possible."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import OTA_LIB

SCRIPT = OTA_LIB / "data-setup"


def _sudo() -> list[str] | None:
    if os.geteuid() == 0:
        return []
    if shutil.which("sudo") and subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0:
        return ["sudo", "-n"]
    return None


SUDO = _sudo()
pytestmark = pytest.mark.skipif(SUDO is None, reason="needs root (mounts)")


def sh(*args: str, check=True) -> subprocess.CompletedProcess:
    return subprocess.run([*SUDO, *args], check=check, capture_output=True, text=True)


def run_setup(root: Path) -> str:
    r = sh("env", f"FSDATA_ROOT={root}", "FSDATA_SKIP_DISK=1", f"FSDATA_CMDLINE={root}/cmdline", "bash", str(SCRIPT))
    return r.stdout


def unmount_all(root: Path) -> None:
    out = sh("findmnt", "-rn", "-o", "TARGET", check=False).stdout.split("\n")
    for tgt in sorted((t for t in out if t.startswith(str(root))), key=len, reverse=True):
        sh("umount", "-l", tgt, check=False)


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "root"
    (r / "var/lib/freesounder").mkdir(parents=True)
    (r / "var/lib/freesounder/household.json").write_text('{"name": "image default"}')
    (r / "etc/freesounder").mkdir(parents=True)
    (r / "etc/freesounder/defaults.conf").write_text("volume=40\n")
    (r / "data").mkdir()
    (r / "cmdline").write_text("rauc.slot=A\n")
    yield r
    unmount_all(r)
    sh("rm", "-rf", str(tmp_path), check=False)


def test_first_boot_seeds_from_the_image_then_binds(root):
    out = run_setup(root)
    assert "seeded /var/lib/freesounder" in out
    assert (root / "data/state/var/lib/freesounder/household.json").exists()
    # Writes now land on /data.
    sh("sh", "-c", f"echo changed > {root}/var/lib/freesounder/household.json")
    assert (root / "data/state/var/lib/freesounder/household.json").read_text() == "changed\n"
    for d in ("ota", "state", "overlay"):
        assert (root / "data" / d).is_dir()


def test_state_survives_a_new_root_image(root):
    run_setup(root)
    sh("sh", "-c", f"echo mine > {root}/var/lib/freesounder/household.json")
    sh("sh", "-c", f"echo volume=55 > {root}/etc/freesounder/defaults.conf")
    unmount_all(root)
    # "Update": a fresh root with new image defaults and a new file.
    sh("sh", "-c", f"echo '{{\"name\": \"new default\"}}' > {root}/var/lib/freesounder/household.json")
    sh("sh", "-c", f"echo volume=40 > {root}/etc/freesounder/defaults.conf; echo x=1 > {root}/etc/freesounder/new.conf")
    out = run_setup(root)
    assert "seeded /var/lib/freesounder" not in out
    assert (root / "var/lib/freesounder/household.json").read_text() == "mine\n"   # state kept
    assert (root / "etc/freesounder/defaults.conf").read_text() == "volume=55\n"   # user change kept
    assert (root / "etc/freesounder/new.conf").read_text() == "x=1\n"              # new image file shows


def test_running_twice_is_harmless(root):
    run_setup(root)
    run_setup(root)
    mounts = sh("findmnt", "-rn", "-o", "TARGET").stdout.split("\n")
    assert mounts.count(f"{root}/var/lib/freesounder") == 1

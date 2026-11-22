"""Developer SSH: the helper script and freesounderd's handling of it (0.6.17).

On 0.6.16 the app's SSH switch snapped back to off: pi-gen deletes the SSH
host keys, nothing recreated them, sshd would not start, and freesounderd
turned the helper's failure into a plain "SSH is off".
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from freesounderd.ssh import SshHelperError, ssh_set, ssh_status

REPO = Path(__file__).resolve().parents[3]
HELPER = (
    REPO
    / "images/player/pi-gen/stage-freesounder/01-filesystem/files/usr/lib/freesounder/ssh-developer"
)


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/bin/bash\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def sandbox(tmp_path: Path) -> dict[str, Path]:
    """Stub the system tools the helper calls; log every call."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    keys = tmp_path / "keys"
    active = tmp_path / "sshd-active"
    for name in ("useradd", "usermod", "chpasswd", "passwd", "journalctl"):
        _stub(bin_dir, name, f'echo "{name} $*" >> "{log}"; cat >/dev/null 2>&1 <&- || true')
    _stub(
        bin_dir,
        "getent",
        f'echo "getent $*" >> "{log}"; echo "developer:x:1001:1001::/home/developer:/bin/bash"',
    )
    _stub(bin_dir, "hostname", "echo fs-office")
    _stub(bin_dir, "ssh-keygen", f'echo "ssh-keygen $*" >> "{log}"; touch "{keys}"')
    # sshd only starts when host keys exist, like the real thing.
    _stub(
        bin_dir,
        "systemctl",
        f'echo "systemctl $*" >> "{log}"\n'
        f'case "$*" in\n'
        f'  *"enable --now ssh.service"*|*"start ssh.service"*)\n'
        f'    [[ -f "{keys}" ]] && touch "{active}"; [[ -f "{keys}" ]];;\n'
        f'  *"is-active --quiet ssh.service"*) [[ -f "{active}" ]];;\n'
        f"  *) true;;\n"
        f"esac",
    )
    return {"bin": bin_dir, "log": log, "keys": keys, "tmp": tmp_path}


def _helper(sandbox: dict[str, Path], *args: str, fixed_hash: str | None = None):
    env = dict(os.environ)
    env["PATH"] = f"{sandbox['bin']}:{env['PATH']}"
    env["FREESOUNDER_SSH_STATE"] = str(sandbox["tmp"] / "run" / "ssh-developer.json")
    hash_file = sandbox["tmp"] / "developer-password.hash"
    if fixed_hash is not None:
        hash_file.write_text(fixed_hash + "\n")
    env["FREESOUNDER_DEVELOPER_HASH_FILE"] = str(hash_file)
    return subprocess.run(
        ["bash", str(HELPER), *args], env=env, capture_output=True, text=True, timeout=30
    )


def test_enable_creates_host_keys_before_starting_sshd(sandbox) -> None:
    done = _helper(sandbox, "enable")
    assert done.returncode == 0, done.stderr
    data = json.loads(done.stdout)
    assert data["enabled"] is True
    assert data["password_fixed"] is False
    assert data["password"] and len(data["password"]) == 20
    calls = sandbox["log"].read_text().splitlines()
    keygen = next(i for i, c in enumerate(calls) if c.startswith("ssh-keygen -A"))
    start = next(i for i, c in enumerate(calls) if "enable --now ssh.service" in c)
    assert keygen < start


def test_enable_with_fixed_password_uses_the_build_hash(sandbox) -> None:
    done = _helper(sandbox, "enable", fixed_hash="$6$salt$hash")
    assert done.returncode == 0, done.stderr
    data = json.loads(done.stdout)
    assert data["password_fixed"] is True
    assert data["password"] is None
    log = sandbox["log"].read_text()
    assert "usermod -p $6$salt$hash developer" in log
    assert "chpasswd" not in log


def test_enable_reports_why_when_sshd_does_not_start(sandbox) -> None:
    _stub(sandbox["bin"], "ssh-keygen", "true")  # keys never appear
    done = _helper(sandbox, "enable")
    assert done.returncode != 0
    assert "sshd did not start" in done.stderr


def test_disable_creates_missing_host_keys_for_a_quick_later_enable(sandbox) -> None:
    done = _helper(sandbox, "disable")
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["enabled"] is False
    assert "ssh-keygen -A" in sandbox["log"].read_text()


def _script(tmp_path: Path, body: str) -> str:
    path = tmp_path / "helper"
    path.write_text("#!/bin/bash\n" + body + "\n")
    path.chmod(0o755)
    return str(path)


def test_enable_failure_raises_with_the_reason(tmp_path: Path) -> None:
    cmd = _script(tmp_path, 'echo "ssh-developer: sshd did not start: no hostkeys" >&2; exit 1')
    with pytest.raises(SshHelperError, match="sshd did not start: no hostkeys"):
        ssh_set(cmd, "fs-office", enabled=True)


def test_status_failure_still_reads_as_off(tmp_path: Path) -> None:
    cmd = _script(tmp_path, "exit 1")
    assert ssh_status(cmd, "fs-office").enabled is False


def test_fixed_password_flag_reaches_the_api_model(tmp_path: Path) -> None:
    payload = {"enabled": True, "hostname": "fs-office", "password": None, "password_fixed": True}
    cmd = _script(tmp_path, f"echo '{json.dumps(payload)}'")
    status = ssh_set(cmd, "fs-office", enabled=True)
    assert status.enabled and status.password_fixed and status.password is None

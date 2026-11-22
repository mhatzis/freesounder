"""Call the ssh-developer helper (or a test stub)."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path

from freesounderd.models import SSH_API_WARNING, SshDeveloperStatus

logger = logging.getLogger(__name__)


class SshHelperError(RuntimeError):
    """Turning developer SSH on or off failed; the message says why."""


def ssh_status(command: str | None, hostname: str) -> SshDeveloperStatus:
    if not command:
        return _offline(hostname, enabled=False)
    return _run(command, ["status"], hostname)


def ssh_set(
    command: str | None,
    hostname: str,
    *,
    enabled: bool,
    public_key: str | None = None,
) -> SshDeveloperStatus:
    if not command:
        if enabled:
            return SshDeveloperStatus(
                enabled=True,
                hostname=hostname,
                command=f"ssh developer@{hostname}.local",
                password="unavailable",
            )
        return _offline(hostname, enabled=False)
    args = ["enable" if enabled else "disable"]
    key_path: Path | None = None
    try:
        if enabled and public_key and public_key.strip():
            handle = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8")
            handle.write(public_key.strip() + "\n")
            handle.close()
            key_path = Path(handle.name)
            os.chmod(key_path, 0o600)
            args.extend(["--public-key-file", str(key_path)])
        return _run(command, args, hostname, strict=True)
    finally:
        if key_path is not None:
            key_path.unlink(missing_ok=True)


def _offline(hostname: str, *, enabled: bool) -> SshDeveloperStatus:
    return SshDeveloperStatus(
        enabled=enabled,
        hostname=hostname,
        command=f"ssh developer@{hostname}.local",
        warning=SSH_API_WARNING,
    )


def _run(
    command: str, args: list[str], hostname: str, *, strict: bool = False
) -> SshDeveloperStatus:
    """Run the helper. A status read that fails reports SSH as off; an enable
    or disable that fails (strict) raises SshHelperError with the reason, so
    the app can say why instead of the switch silently snapping back."""
    try:
        completed = subprocess.run(
            [command, *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("ssh-developer helper failed: %s", exc, exc_info=True)
        if strict:
            raise SshHelperError(f"the SSH helper could not run ({exc})") from exc
        return _offline(hostname, enabled=False)
    payload = (completed.stdout or "").strip()
    if completed.returncode != 0 or not payload:
        logger.warning(
            "ssh-developer exited %s: %s",
            completed.returncode,
            (completed.stderr or payload)[:500],
        )
        if strict:
            reason = _last_line(completed.stderr) or f"exit code {completed.returncode}"
            raise SshHelperError(reason.removeprefix("ssh-developer: "))
        return _offline(hostname, enabled=False)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        logger.warning("ssh-developer produced invalid JSON: %s", payload[:300])
        if strict:
            raise SshHelperError("the SSH helper gave an unreadable answer") from None
        return _offline(hostname, enabled=False)
    data.setdefault("hostname", hostname)
    data.setdefault("warning", SSH_API_WARNING)
    return SshDeveloperStatus.model_validate(data)


def _last_line(text: str | None) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return lines[-1][:300] if lines else ""

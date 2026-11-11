"""Unattended Music Assistant onboarding for the household host.

Music Assistant (schema >= 28) refuses API clients without a token, and a fresh
server has no users. On the host the control API owns the bundled server, so it
creates the first admin with a random password (kept root-only on the player),
then logs in for a token. Nothing here is ever shipped in the image or repo.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from pathlib import Path
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger(__name__)

ADMIN_USERNAME = "freesounder"
DEVICE_NAME = "FreeSounder control API"


class BootstrapError(RuntimeError):
    """Could not obtain a Music Assistant token automatically."""


def is_local_server(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host in {"127.0.0.1", "localhost", "::1"}


def read_secret(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def write_secret(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(value + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _http_base(ma_url: str) -> str:
    parsed = urlparse(ma_url)
    scheme = "https" if parsed.scheme in {"https", "wss"} else "http"
    return f"{scheme}://{parsed.netloc}"


async def obtain_token(ma_url: str, admin_file: Path, token_file: Path) -> str:
    """Create the first admin (or log in as it) and store a token. Raises BootstrapError."""

    base = _http_base(ma_url)
    admin = None
    raw = read_secret(admin_file)
    if raw:
        try:
            admin = json.loads(raw)
        except ValueError:
            admin = None
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        if admin is None:
            admin = {"username": ADMIN_USERNAME, "password": secrets.token_urlsafe(24)}
            # Persist before the server sees it so a crash cannot lose the password.
            write_secret(admin_file, json.dumps(admin))
            token = await _setup(session, base, admin)
        else:
            token = await _login(session, base, admin)
            if token is None:
                token = await _setup(session, base, admin)
    write_secret(token_file, token)
    logger.info("Stored a Music Assistant token for the control API")
    return token


async def _setup(session: aiohttp.ClientSession, base: str, admin: dict) -> str:
    body = {
        "username": admin["username"],
        "password": admin["password"],
        "display_name": "FreeSounder",
        "device_name": DEVICE_NAME,
    }
    async with session.post(f"{base}/setup", json=body) as response:
        data = await _json(response)
        if response.status == 409:
            raise BootstrapError(
                "Music Assistant already has an admin account that FreeSounder did not "
                "create. Set a token with: sudo freesounder-setup set-ma-token"
            )
        if response.status >= 400 or not data.get("token"):
            raise BootstrapError(f"Music Assistant setup failed: HTTP {response.status}")
        return str(data["token"])


async def _login(session: aiohttp.ClientSession, base: str, admin: dict) -> str | None:
    body = {
        "provider_id": "builtin",
        "credentials": {"username": admin["username"], "password": admin["password"]},
        "device_name": DEVICE_NAME,
    }
    async with session.post(f"{base}/auth/login", json=body) as response:
        data = await _json(response)
        if response.status == 403:
            return None  # no users yet: run setup
        if response.status >= 400 or not data.get("token"):
            raise BootstrapError(f"Music Assistant login failed: HTTP {response.status}")
        return str(data["token"])


async def _json(response: aiohttp.ClientResponse) -> dict:
    try:
        data = await response.json(content_type=None)
    except (ValueError, aiohttp.ContentTypeError):
        return {}
    return data if isinstance(data, dict) else {}

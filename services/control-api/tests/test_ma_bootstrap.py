"""Unattended Music Assistant onboarding and 'still starting' reporting."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from aiohttp import web
from fastapi.testclient import TestClient

from freesounder_control_api.app import create_app
from freesounder_control_api.config import Settings
from freesounder_control_api.ma_backend import MusicAssistantBackend
from freesounder_control_api.ma_bootstrap import BootstrapError, obtain_token
from freesounder_control_api.players import LanPlayerIndex


class FakeMa:
    def __init__(self, has_users: bool = False) -> None:
        self.users: dict[str, str] = {}
        self.foreign_admin = has_users

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/setup", self.setup)
        app.router.add_post("/auth/login", self.login)
        return app

    async def setup(self, request: web.Request) -> web.Response:
        if self.users or self.foreign_admin:
            return web.json_response({"success": False}, status=409)
        body = await request.json()
        assert len(body["password"]) >= 8
        self.users[body["username"]] = body["password"]
        return web.json_response({"success": True, "token": "tok-setup"})

    async def login(self, request: web.Request) -> web.Response:
        if not self.users and not self.foreign_admin:
            return web.json_response({"success": False, "error": "Setup required"}, status=403)
        body = await request.json()
        creds = body["credentials"]
        if self.users.get(creds["username"]) != creds["password"]:
            return web.json_response({"success": False}, status=401)
        return web.json_response({"success": True, "token": "tok-login"})


async def _serve(fake: FakeMa) -> tuple[web.AppRunner, str]:
    runner = web.AppRunner(fake.app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    return runner, f"http://127.0.0.1:{port}"


@pytest.mark.asyncio
async def test_fresh_server_creates_admin_and_stores_token(tmp_path: Path) -> None:
    fake = FakeMa()
    runner, url = await _serve(fake)
    try:
        admin, token_file = tmp_path / "ma-admin.json", tmp_path / "ma-token"
        token = await obtain_token(url, admin, token_file)
        assert token == "tok-setup"
        assert token_file.read_text().strip() == "tok-setup"
        assert stat.S_IMODE(admin.stat().st_mode) == 0o600
        assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
        saved = json.loads(admin.read_text())
        assert fake.users == {saved["username"]: saved["password"]}
        # Second run (token lost or expired) logs in with the stored admin.
        assert await obtain_token(url, admin, token_file) == "tok-login"
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_foreign_admin_needs_manual_token(tmp_path: Path) -> None:
    runner, url = await _serve(FakeMa(has_users=True))
    try:
        with pytest.raises(BootstrapError, match="set-ma-token"):
            await obtain_token(url, tmp_path / "a.json", tmp_path / "t")
    finally:
        await runner.cleanup()


def test_health_says_starting_with_launcher_reason(tmp_path: Path) -> None:
    status = tmp_path / "ma-status.json"
    status.write_text(json.dumps({"state": "loading", "detail": "Loading Music Assistant"}))
    backend = MusicAssistantBackend(
        "http://127.0.0.1:9",
        token_file=tmp_path / "t",
        admin_file=tmp_path / "a.json",
        auto_setup=True,
        status_file=status,
    )
    app = create_app(Settings(mdns_enabled=False), backend, LanPlayerIndex(), enable_mdns=False)
    with TestClient(app) as client:
        health = client.get("/health").json()
        root = client.get("/").json()
        services = client.get("/api/v1/music-services")
    assert health["ma_state"] == "starting"
    assert health["ma_detail"] == "Loading Music Assistant"
    assert root["service"] == "FreeSounder control API"
    assert root["music_assistant"]["state"] == "starting"
    assert services.status_code == 503
    assert services.json()["ma_state"] == "starting"

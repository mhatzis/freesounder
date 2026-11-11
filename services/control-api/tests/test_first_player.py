"""First-player (create a system) vs join-existing adopt, and a host whose
Music Assistant is still starting."""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from freesounder_control_api import players as players_mod
from freesounder_control_api.app import create_app
from freesounder_control_api.config import Settings
from freesounder_control_api.ma_backend import MusicAssistantBackend
from freesounder_control_api.mock_backend import MockMusicBackend
from freesounder_control_api.models import AdoptBody
from freesounder_control_api.players import AgentError, LanPlayerIndex


class _Recorder:
    def __init__(self, response: dict[str, Any] | Exception | None = None) -> None:
        self.calls: list[tuple[str, str, dict | None, float]] = []
        self.response = response

    async def __call__(
        self, method: str, url: str, json: dict | None = None, *, timeout: float = 5.0  # noqa: ASYNC109
    ) -> dict[str, Any]:
        self.calls.append((method, url, json, timeout))
        if isinstance(self.response, Exception):
            raise self.response
        if self.response is not None:
            return self.response
        return {
            "id": "pi-1",
            "name": (json or {}).get("name", "x"),
            "role": (json or {}).get("role"),
            "state": "configured",
        }


@pytest.fixture
def lan(monkeypatch: pytest.MonkeyPatch) -> tuple[LanPlayerIndex, _Recorder]:
    recorder = _Recorder()
    monkeypatch.setattr(players_mod, "_request_json", recorder)
    monkeypatch.setattr(players_mod, "local_addresses", lambda: {"127.0.0.1", "192.168.1.50"})
    return LanPlayerIndex(), recorder


@pytest.mark.asyncio
async def test_first_player_adopts_own_agent_as_host(lan: tuple[LanPlayerIndex, _Recorder]) -> None:
    index, recorder = lan
    body = AdoptBody(name="Lounge", id="pi-1", url="http://192.168.1.50:8090", role="host")
    result = await index.adopt(body, "192.168.1.50")
    method, url, payload, timeout = recorder.calls[0]
    assert method == "POST"
    # The app passes the LAN IP; the host talks to its own agent on loopback.
    assert url == "http://127.0.0.1:8090/api/v1/agent/adopt"
    assert payload == {"name": "Lounge", "host": "192.168.1.50", "role": "host"}
    assert timeout >= 15
    assert result.role == "host"


@pytest.mark.asyncio
async def test_own_agent_defaults_to_host_never_room(lan: tuple[LanPlayerIndex, _Recorder]) -> None:
    index, recorder = lan
    await index.adopt(AdoptBody(name="Lounge", url="http://192.168.1.50:8090"), "192.168.1.50")
    assert recorder.calls[0][2]["role"] == "host"


@pytest.mark.asyncio
async def test_join_existing_adopts_remote_agent_as_room(
    lan: tuple[LanPlayerIndex, _Recorder],
) -> None:
    index, recorder = lan
    body = AdoptBody(name="Kitchen", id="pi-2", url="http://192.168.1.77:8090")
    result = await index.adopt(body, "192.168.1.50")
    method, url, payload, _ = recorder.calls[0]
    assert url == "http://192.168.1.77:8090/api/v1/agent/adopt"
    assert payload == {"name": "Kitchen", "host": "192.168.1.50", "role": "room"}
    assert result.address == "192.168.1.77"


def test_adopt_route_reports_agent_errors_as_502(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder(AgentError("POST http://192.168.1.77:8090/api/v1/agent/adopt timed out"))
    monkeypatch.setattr(players_mod, "_request_json", recorder)
    monkeypatch.setattr(players_mod, "local_addresses", lambda: {"127.0.0.1"})
    app = create_app(
        Settings(mock_ma=False, mdns_enabled=False),
        MockMusicBackend(),
        LanPlayerIndex(),
        enable_mdns=False,
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/players/adopt",
            json={"name": "Kitchen", "url": "http://192.168.1.77:8090"},
        )
    assert response.status_code == 502
    assert "timed out" in response.json()["detail"]


def test_host_serves_immediately_while_music_assistant_is_down() -> None:
    # Nothing listens on this port: Music Assistant is still pulling/starting.
    backend = MusicAssistantBackend("http://127.0.0.1:9")
    app = create_app(Settings(mdns_enabled=False), backend, LanPlayerIndex(), enable_mdns=False)
    started = time.monotonic()
    with TestClient(app) as client:
        health = client.get("/health")
        rooms = client.get("/api/v1/rooms")
        system = client.get("/api/v1/system")
        play = client.post("/api/v1/players/x/play")
    assert time.monotonic() - started < 10
    assert health.status_code == 200
    assert health.json()["status"] == "degraded"
    assert health.json()["ma_connected"] is False
    assert rooms.status_code == 200
    assert rooms.json() == []
    assert system.json()["ma_connected"] is False
    assert play.status_code == 503

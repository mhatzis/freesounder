"""HTTP and WebSocket tests against a mocked Music Assistant backend."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from freesounder_control_api.app import create_app
from freesounder_control_api.config import Settings
from freesounder_control_api.mock_backend import MockMusicBackend
from freesounder_control_api.models import Room


@pytest.fixture
def backend() -> MockMusicBackend:
    return MockMusicBackend()


@pytest.fixture
def client(backend: MockMusicBackend) -> TestClient:
    settings = Settings(mock_ma=True, mdns_enabled=False)
    app = create_app(settings, backend, enable_mdns=False)
    with TestClient(app) as test_client:
        yield test_client


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["ma_connected"] is True
    assert body["mock"] is True
    assert body["version"]


def test_list_rooms(client: TestClient) -> None:
    response = client.get("/api/v1/rooms")
    assert response.status_code == 200
    rooms = response.json()
    ids = {room["id"] for room in rooms}
    assert ids == {"kitchen", "lounge"}
    kitchen = next(room for room in rooms if room["id"] == "kitchen")
    assert kitchen["name"] == "Kitchen"
    assert kitchen["volume"] == 40


def test_create_join_leave_group(client: TestClient) -> None:
    created = client.post(
        "/api/v1/groups",
        json={"name": "Downstairs", "member_ids": ["kitchen", "lounge"]},
    )
    assert created.status_code == 200
    group = created.json()
    assert group["is_group"] is True
    assert set(group["member_ids"]) == {"kitchen", "lounge"}
    group_id = group["id"]

    groups = client.get("/api/v1/groups")
    assert groups.status_code == 200
    assert any(item["id"] == group_id for item in groups.json())

    # Leave then rejoin to exercise both sync-group endpoints.
    left = client.post(f"/api/v1/groups/{group_id}/leave", json={"player_id": "lounge"})
    assert left.status_code == 204
    rooms = {room["id"]: room for room in client.get("/api/v1/rooms").json()}
    assert rooms["lounge"]["group_id"] is None
    assert "lounge" not in rooms[group_id]["member_ids"]

    joined = client.post(f"/api/v1/groups/{group_id}/join", json={"player_id": "lounge"})
    assert joined.status_code == 204
    rooms = {room["id"]: room for room in client.get("/api/v1/rooms").json()}
    assert rooms["lounge"]["group_id"] == group_id
    assert "lounge" in rooms[group_id]["member_ids"]


def test_now_playing_and_transport(client: TestClient) -> None:
    playing = client.get("/api/v1/now-playing", params={"player_id": "kitchen"})
    assert playing.status_code == 200
    assert playing.json()["title"] == "Example Track"
    assert playing.json()["state"] == "playing"

    assert client.post("/api/v1/players/kitchen/pause").status_code == 204
    paused = client.get("/api/v1/now-playing", params={"player_id": "kitchen"})
    assert paused.json()["state"] == "paused"

    assert client.post("/api/v1/players/kitchen/play").status_code == 204
    state = client.get("/api/v1/now-playing", params={"player_id": "kitchen"}).json()["state"]
    assert state == "playing"

    assert client.post("/api/v1/players/kitchen/next").status_code == 204
    nxt = client.get("/api/v1/now-playing", params={"player_id": "kitchen"}).json()
    assert "next" in nxt["title"].lower()


def test_volume_room_and_group(client: TestClient) -> None:
    assert client.post("/api/v1/players/kitchen/volume", json={"level": 55}).status_code == 204
    kitchen = next(r for r in client.get("/api/v1/rooms").json() if r["id"] == "kitchen")
    assert kitchen["volume"] == 55

    group = client.post("/api/v1/groups", json={"name": "House", "member_ids": ["kitchen"]}).json()
    vol = client.post(f"/api/v1/groups/{group['id']}/volume", json={"level": 22})
    assert vol.status_code == 204
    rooms = {r["id"]: r for r in client.get("/api/v1/rooms").json()}
    assert rooms[group["id"]]["volume"] == 22
    assert rooms["kitchen"]["volume"] == 22

    bad = client.post("/api/v1/players/kitchen/volume", json={"level": 140})
    assert bad.status_code == 422


def test_search_and_browse_passthrough(client: TestClient) -> None:
    search = client.get("/api/v1/search", params={"q": "abbey road"})
    assert search.status_code == 200
    body = search.json()
    assert body["query"] == "abbey road"
    assert body["items"]
    assert "abbey road" in body["items"][0]["name"].lower()

    browse = client.get("/api/v1/browse", params={"path": "library://albums"})
    assert browse.status_code == 200
    assert browse.json()["path"] == "library://albums"
    assert browse.json()["items"][0]["media_type"] == "album"


def test_websocket_emits_health_and_rooms(client: TestClient) -> None:
    with client.websocket_connect("/api/v1/ws") as ws:
        first = ws.receive_json()
        second = ws.receive_json()
    events = {first["event"]: first["data"], second["event"]: second["data"]}
    assert "health" in events
    assert events["health"]["status"] == "ok"
    assert "rooms" in events
    assert {room["id"] for room in events["rooms"]} == {"kitchen", "lounge"}


@pytest.mark.asyncio
async def test_backend_protocol_is_mocked() -> None:
    """The HTTP layer talks only to MusicBackend, never a live MA client here."""

    backend = MockMusicBackend()
    settings = Settings(mock_ma=True, mdns_enabled=False)
    app = create_app(settings, backend, enable_mdns=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/v1/rooms")
    assert response.status_code == 200
    assert isinstance(Room.model_validate(response.json()[0]), Room)


def test_system_and_unclaimed_players(client: TestClient) -> None:
    system = client.get("/api/v1/system")
    assert system.status_code == 200
    body = system.json()
    assert body["version"]
    assert body["mock"] is True

    unclaimed = client.get("/api/v1/players/unclaimed")
    assert unclaimed.status_code == 200
    ids = {item["id"] for item in unclaimed.json()}
    assert "attic-pi" in ids


def test_adopt_adds_room(client: TestClient) -> None:
    adopted = client.post(
        "/api/v1/players/adopt",
        json={"id": "attic-pi", "name": "Attic"},
    )
    assert adopted.status_code == 200
    assert adopted.json()["name"] == "Attic"
    assert adopted.json()["state"] == "configured"

    rooms = {room["id"]: room for room in client.get("/api/v1/rooms").json()}
    assert "attic-pi" in rooms
    assert rooms["attic-pi"]["name"] == "Attic"

    leftover = client.get("/api/v1/players/unclaimed").json()
    assert all(item["id"] != "attic-pi" for item in leftover)

    settings = client.get("/api/v1/players/attic-pi/settings")
    assert settings.status_code == 200
    assert settings.json()["name"] == "Attic"

    missing = client.post("/api/v1/players/adopt", json={"name": "Nope"})
    assert missing.status_code == 422


def test_player_settings_roundtrip_and_volume_limit(client: TestClient) -> None:
    current = client.get("/api/v1/players/kitchen/settings")
    assert current.status_code == 200
    assert current.json()["name"] == "Kitchen"

    patched = client.patch(
        "/api/v1/players/kitchen/settings",
        json={
            "name": "Kitchen 2",
            "bass": 5,
            "treble": -4,
            "balance": -3,
            "loudness": True,
            "volume_limit": 50,
        },
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["name"] == "Kitchen 2"
    assert body["bass"] == 5
    assert body["loudness"] is True
    assert body["volume_limit"] == 50

    rooms = {room["id"]: room for room in client.get("/api/v1/rooms").json()}
    assert rooms["kitchen"]["name"] == "Kitchen 2"

    assert client.post("/api/v1/players/kitchen/volume", json={"level": 80}).status_code == 204
    kitchen = next(r for r in client.get("/api/v1/rooms").json() if r["id"] == "kitchen")
    assert kitchen["volume"] == 50

    missing = client.get("/api/v1/players/does-not-exist/settings")
    assert missing.status_code == 404

    bad = client.patch("/api/v1/players/kitchen/settings", json={"bass": 20})
    assert bad.status_code == 422

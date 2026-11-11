"""Music services facade tests against the in-memory backend (no live MA)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from freesounder_control_api.app import create_app
from freesounder_control_api.config import Settings
from freesounder_control_api.ma_music_services import MaMusicServices
from freesounder_control_api.mock_backend import MockMusicBackend
from freesounder_control_api.models import StartAuthBody
from freesounder_control_api.music_catalog import config_entry_to_field, map_provider_status


@pytest.fixture
def client() -> TestClient:
    settings = Settings(mock_ma=True, mdns_enabled=False)
    app = create_app(settings, MockMusicBackend(), enable_mdns=False)
    with TestClient(app) as test_client:
        yield test_client


def test_catalog_lists_curated_types_and_demo_spotify(client: TestClient) -> None:
    response = client.get("/api/v1/music-services/catalog")
    assert response.status_code == 200
    body = response.json()
    domains = {item["domain"] for item in body["services"]}
    assert {
        "spotify",
        "tidal",
        "apple_music",
        "deezer",
        "ytmusic",
        "qobuz",
        "soundcloud",
        "tunein",
        "radiobrowser",
        "plex",
        "jellyfin",
        "opensubsonic",
        "filesystem_local",
        "filesystem_smb",
        "filesystem_nfs",
    }.issubset(domains)
    apple = next(item for item in body["services"] if item["domain"] == "apple_music")
    assert apple["limited"] is True
    assert "experimental" in apple["limitation"].lower()
    assert apple["available_on_server"] is True
    nfs = next(item for item in body["services"] if item["domain"] == "filesystem_nfs")
    assert nfs["available_on_server"] is False
    configured = body["configured"]
    assert len(configured) == 1
    assert configured[0]["domain"] == "spotify"
    assert configured[0]["status"] == "loaded"
    assert configured[0]["account"] == "demo@example.com"


def test_list_and_get_configured_service(client: TestClient) -> None:
    listing = client.get("/api/v1/music-services")
    assert listing.status_code == 200
    assert listing.json()[0]["instance_id"] == "spotify-demo"
    detail = client.get("/api/v1/music-services/spotify-demo")
    assert detail.status_code == 200
    assert detail.json()["account"] == "demo@example.com"
    missing = client.get("/api/v1/music-services/does-not-exist")
    assert missing.status_code == 404


def test_add_credential_provider_does_not_echo_password(client: TestClient) -> None:
    added = client.post(
        "/api/v1/music-services",
        json={"domain": "qobuz", "values": {"username": "ada", "password": "super-secret"}},
    )
    assert added.status_code == 200
    body = added.json()
    assert body["domain"] == "qobuz"
    assert body["account"] == "ada"
    assert body["status"] == "loaded"
    assert "password" not in body["values"]
    assert "super-secret" not in added.text
    secrets = [field for field in body["entries"] if field["key"] == "password"]
    assert secrets
    assert secrets[0]["secret"] is True
    assert secrets[0]["has_value"] is True
    assert secrets[0]["value"] is None


def test_update_and_remove_service(client: TestClient) -> None:
    added = client.post(
        "/api/v1/music-services",
        json={"domain": "filesystem_local", "values": {"path": "/media/music"}},
    ).json()
    instance_id = added["instance_id"]
    patched = client.patch(
        f"/api/v1/music-services/{instance_id}",
        json={"values": {"path": "/media/flac"}, "name": "FLAC library"},
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "FLAC library"
    assert patched.json()["values"]["path"] == "/media/flac"
    deleted = client.delete(f"/api/v1/music-services/{instance_id}")
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/music-services/{instance_id}").status_code == 404


def test_oauth_flow_returns_url_and_completes_via_callback(client: TestClient) -> None:
    started = client.post("/api/v1/music-services/auth", json={"domain": "tidal"})
    assert started.status_code == 200
    session = started.json()
    assert session["status"] == "awaiting_browser"
    assert session["auth_url"]
    assert session["session_id"]
    assert session["callback_scheme"] == "freesounder"
    session_id = session["session_id"]

    status = client.get(f"/api/v1/music-services/auth/{session_id}")
    assert status.json()["status"] == "awaiting_browser"

    # Follow the demo callback; it redirects to the app scheme.
    callback = client.get(
        f"/api/v1/music-services/auth/{session_id}/callback",
        follow_redirects=False,
    )
    assert callback.status_code == 302
    location = callback.headers["location"]
    assert location.startswith("freesounder://music-services/auth")
    assert "status=success" in location

    done = client.get(f"/api/v1/music-services/auth/{session_id}")
    assert done.json()["status"] == "success"
    instance_id = done.json()["instance_id"]
    assert instance_id
    services = {item["instance_id"]: item for item in client.get("/api/v1/music-services").json()}
    assert instance_id in services
    assert services[instance_id]["domain"] == "tidal"


def test_reauthenticate_and_abort(client: TestClient) -> None:
    started = client.post(
        "/api/v1/music-services/auth", json={"instance_id": "spotify-demo"}
    )
    assert started.status_code == 200
    assert started.json()["domain"] == "spotify"
    aborted = client.post(f"/api/v1/music-services/auth/{started.json()['session_id']}/abort")
    assert aborted.status_code == 200
    assert aborted.json()["status"] == "aborted"


def test_add_oauth_without_values_starts_auth(client: TestClient) -> None:
    started = client.post("/api/v1/music-services", json={"domain": "spotify"})
    # Spotify is already connected in the demo household.
    assert started.status_code == 200
    # Starting auth is fine; adding a second instance after callback would 409.
    # A fresh oauth service (tidal) starts a browser session.
    tidal = client.post("/api/v1/music-services", json={"domain": "tidal"})
    assert tidal.status_code == 200
    assert tidal.json()["status"] == "awaiting_browser"


def test_form_then_submit(client: TestClient) -> None:
    started = client.post("/api/v1/music-services", json={"domain": "qobuz"})
    assert started.status_code == 200
    session = started.json()
    assert session["status"] == "awaiting_form"
    assert {field["key"] for field in session["entries"]} >= {"username", "password"}
    submitted = client.post(
        f"/api/v1/music-services/auth/{session['session_id']}/submit",
        json={"values": {"username": "sam", "password": "hidden"}},
    )
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "success"
    assert "hidden" not in submitted.text


def test_spotify_connect_toggle(client: TestClient) -> None:
    status = client.get("/api/v1/music-services/spotify-connect")
    assert status.status_code == 200
    body = status.json()
    assert body["available"] is True
    assert body["engine"] == "go_librespot"
    rooms = {room["player_id"]: room for room in body["rooms"]}
    assert rooms["kitchen"]["enabled"] is True
    assert rooms["lounge"]["enabled"] is False
    toggled = client.post(
        "/api/v1/music-services/spotify-connect/lounge",
        json={"enabled": True},
    )
    assert toggled.status_code == 200
    rooms = {room["player_id"]: room for room in toggled.json()["rooms"]}
    assert rooms["lounge"]["enabled"] is True
    missing = client.post(
        "/api/v1/music-services/spotify-connect/nope",
        json={"enabled": True},
    )
    assert missing.status_code == 404


def test_websocket_receives_auth_completion(client: TestClient) -> None:
    started = client.post("/api/v1/music-services/auth", json={"domain": "tidal"}).json()
    with client.websocket_connect("/api/v1/ws") as ws:
        # Drain the greeting events.
        ws.receive_json()
        ws.receive_json()
        client.get(
            f"/api/v1/music-services/auth/{started['session_id']}/callback",
            follow_redirects=False,
        )
        events = []
        for _ in range(6):
            try:
                events.append(ws.receive_json())
            except Exception:  # noqa: BLE001
                break
            if any(item.get("event") == "music_service_auth" for item in events):
                break
    kinds = {item["event"] for item in events}
    assert "music_service_auth" in kinds
    auth = next(item for item in events if item["event"] == "music_service_auth")
    assert auth["data"]["status"] == "success"


def test_config_entry_redaction_and_status_mapping() -> None:
    entry = SimpleNamespace(
        to_dict=lambda: {
            "key": "password",
            "type": "secure_string",
            "label": "Password",
            "required": True,
            "value": "hunter2",
            "options": [],
        },
        value="hunter2",
    )
    field = config_entry_to_field(entry)
    assert field.secret is True
    assert field.value is None
    assert field.has_value is True
    dropdown = config_entry_to_field(
        {
            "key": "quality",
            "type": "string",
            "label": "Quality",
            "value": "hires",
            "options": [{"value": "hires", "title": "Hi-res"}, {"value": "cd", "title": "CD"}],
        }
    )
    assert dropdown.type == "dropdown"
    assert dropdown.value == "hires"
    assert map_provider_status("auth_required") == "needs_reauth"
    assert map_provider_status("loaded") == "loaded"
    assert map_provider_status(None, available=False, enabled=True) == "error"


@pytest.mark.asyncio
async def test_ma_adapter_lists_and_starts_oauth_with_mocked_client() -> None:
    """The MA adapter talks only through music-assistant-client config commands."""

    manifest = SimpleNamespace(
        domain="spotify",
        name="Spotify",
        stage=SimpleNamespace(value="stable"),
        builtin=False,
        multi_instance=False,
        documentation="https://example.test/spotify",
        type=SimpleNamespace(value="music"),
    )
    instance = SimpleNamespace(
        domain="spotify",
        instance_id="spotify_1",
        available=True,
        type=SimpleNamespace(value="music"),
    )
    config = SimpleNamespace(
        domain="spotify",
        instance_id="spotify_1",
        name="Spotify",
        default_name="Spotify",
        type=SimpleNamespace(value="music"),
        enabled=True,
        status=SimpleNamespace(value="loaded"),
        last_error=None,
        values={},
        to_dict=lambda: {},
    )
    entries = [
        {
            "key": "username",
            "type": "string",
            "label": "Account",
            "value": "user@example.com",
            "options": [],
        },
        {
            "key": "refresh_token",
            "type": "secure_string",
            "label": "Token",
            "value": "SECRETTOKEN",
            "options": [],
        },
    ]
    step = SimpleNamespace(
        flow_id="flow-1",
        step_id="authenticate",
        type=SimpleNamespace(value="external"),
        url="https://accounts.spotify.com/authorize?x=1",
        title="Sign in to Spotify",
        description="Open Spotify",
        entries=[],
        errors={},
        last_step=None,
        copy_text=None,
        progress_text=None,
        image=None,
        result=None,
        reason=None,
        to_dict=lambda: {
            "flow_id": "flow-1",
            "step_id": "authenticate",
            "type": "external",
            "url": "https://accounts.spotify.com/authorize?x=1",
            "title": "Sign in to Spotify",
            "description": "Open Spotify",
            "entries": [],
            "errors": {},
        },
    )
    config_api = SimpleNamespace(
        get_provider_configs=AsyncMock(return_value=[config]),
        get_provider_config=AsyncMock(return_value=config),
        get_provider_config_entries=AsyncMock(return_value=entries),
        setup_provider=AsyncMock(return_value=step),
        reconfigure_provider=AsyncMock(return_value=step),
        save_provider_config=AsyncMock(return_value=config),
        remove_provider_config=AsyncMock(),
        submit_setup_flow=AsyncMock(),
        get_setup_flow=AsyncMock(return_value=step),
        abort_setup_flow=AsyncMock(),
    )
    client = SimpleNamespace(
        config=config_api,
        provider_manifests=[manifest],
        providers=[instance],
        players=SimpleNamespace(players=[], get=MagicMock(return_value=None)),
        subscribe=MagicMock(),
    )
    backend = SimpleNamespace(_client=client, client=client, _loop=None)
    services = MaMusicServices(backend)

    types = await services.list_music_service_types()
    assert any(item.domain == "spotify" and item.available_on_server for item in types)
    apple = next(item for item in types if item.domain == "apple_music")
    assert apple.limited is True
    assert apple.available_on_server is False

    listing = await services.list_music_services()
    assert listing[0].account == "user@example.com"
    assert "SECRETTOKEN" not in str(listing[0].model_dump())
    assert listing[0].values.get("refresh_token") is None

    session = await services.start_music_service_auth(StartAuthBody(domain="spotify"))
    config_api.setup_provider.assert_awaited_once_with("spotify")
    assert session.status == "awaiting_browser"
    assert session.auth_url.startswith("https://accounts.spotify.com/")
    assert session.flow_id == "flow-1"
    assert session.callback_scheme == "freesounder"

    # Simulate Music Assistant finishing the flow.
    finish = SimpleNamespace(
        flow_id="flow-1",
        step_id="done",
        type=SimpleNamespace(value="finish"),
        url=None,
        entries=[],
        errors={},
        result={"instance_id": "spotify_1"},
        reason=None,
        to_dict=lambda: {
            "flow_id": "flow-1",
            "step_id": "done",
            "type": "finish",
            "result": {"instance_id": "spotify_1"},
            "entries": [],
            "errors": {},
        },
    )
    event = SimpleNamespace(
        event=SimpleNamespace(value="setup_flow_updated"),
        object_id="flow-1",
        data=finish,
    )
    await services._handle_event("setup_flow_updated", event)
    refreshed = await services.get_music_service_auth(session.session_id)
    assert refreshed.status == "success"
    assert refreshed.instance_id == "spotify_1"

    await services.remove_music_service("spotify_1")
    config_api.remove_provider_config.assert_awaited_once_with("spotify_1")


@pytest.mark.asyncio
async def test_ma_adapter_spotify_connect_toggle() -> None:
    connect_config = SimpleNamespace(
        domain="spotify_connect",
        instance_id="sc_1",
        name="Spotify Connect",
        default_name="Spotify Connect",
        type=SimpleNamespace(value="plugin"),
        enabled=True,
        status=SimpleNamespace(value="loaded"),
        last_error=None,
        values={
            "backend": SimpleNamespace(value="go_librespot"),
            "connected_players": SimpleNamespace(value=["kitchen"]),
        },
        setup_data={"backend": "go_librespot"},
    )
    config_api = SimpleNamespace(
        get_provider_configs=AsyncMock(return_value=[connect_config]),
        get_provider_config=AsyncMock(return_value=connect_config),
        save_provider_config=AsyncMock(return_value=connect_config),
    )
    kitchen = SimpleNamespace(player_id="kitchen", display_name="Kitchen", type="player")
    lounge = SimpleNamespace(player_id="lounge", display_name="Lounge", type="player")
    client = SimpleNamespace(
        config=config_api,
        provider_manifests=[
            SimpleNamespace(
                domain="spotify_connect",
                stage="beta",
                builtin=False,
                multi_instance=False,
                documentation=None,
            )
        ],
        providers=[],
        players=SimpleNamespace(players=[kitchen, lounge], get=MagicMock(return_value=kitchen)),
        subscribe=MagicMock(),
    )
    services = MaMusicServices(SimpleNamespace(_client=client, client=client, _loop=None))
    status = await services.spotify_connect_status()
    assert status.available is True
    assert status.engine == "go_librespot"
    rooms = {room.player_id: room for room in status.rooms}
    assert rooms["kitchen"].enabled is True
    assert rooms["lounge"].enabled is False
    await services.set_spotify_connect("lounge", True)
    config_api.save_provider_config.assert_awaited()
    saved_values = config_api.save_provider_config.await_args.kwargs["values"]
    assert set(saved_values["connected_players"]) == {"kitchen", "lounge"}

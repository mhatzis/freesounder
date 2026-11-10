"""HTTP tests for the player agent."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from freesounder_player_agent.app import create_app
from freesounder_player_agent.config import AgentSettings
from freesounder_player_agent.store import PlayerStore


@pytest.fixture
def tmp_env(tmp_path: Path) -> AgentSettings:
    marker = tmp_path / "applied"
    apply = f"touch {marker}"
    settings = AgentSettings(
        host="127.0.0.1",
        agent_port=8090,
        mdns_enabled=False,
        config_dir=tmp_path / "etc",
        state_dir=tmp_path / "var",
        alsa_conf=tmp_path / "alsa" / "99-freesounder.conf",
        camilla_conf=tmp_path / "camilla.yml",
        apply_command=apply,
    )
    settings.config_dir.mkdir(parents=True, exist_ok=True)
    # An unclaimed room player — do not rely on this machine's RAM for role auto.
    (settings.config_dir / "role").write_text("room\n")
    return settings


@pytest.fixture
def client(tmp_env: AgentSettings) -> TestClient:
    store = PlayerStore(tmp_env)
    app = create_app(tmp_env, store, enable_mdns=False)
    with TestClient(app) as test_client:
        yield test_client


def test_health_and_unconfigured_agent(client: TestClient) -> None:
    health = client.get("/health")
    assert health.status_code == 200
    body = health.json()
    assert body["status"] == "ok"
    assert body["state"] == "unconfigured"
    assert body["id"]

    info = client.get("/api/v1/agent").json()
    assert info["id"] == body["id"]
    assert info["state"] == "unconfigured"
    assert info["role"] == "room"


def test_adopt_marks_configured_and_writes_host(client: TestClient, tmp_env: AgentSettings) -> None:
    response = client.post(
        "/api/v1/agent/adopt",
        json={"name": "Kitchen", "host": "192.168.1.10", "role": "room"},
    )
    assert response.status_code == 200
    info = response.json()
    assert info["name"] == "Kitchen"
    assert info["state"] == "configured"
    assert info["host"] == "192.168.1.10"
    assert info["role"] == "room"
    assert (tmp_env.config_dir / "configured").is_file()
    assert tmp_env.alsa_conf.is_file()
    # Apply hook ran.
    assert (tmp_env.config_dir.parent / "applied").is_file() or (
        tmp_env.state_dir.parent / "applied"
    ).is_file() or (tmp_env.config_dir.parent.parent / "applied").is_file()


def test_settings_roundtrip(client: TestClient, tmp_env: AgentSettings) -> None:
    current = client.get("/api/v1/settings")
    assert current.status_code == 200
    assert current.json()["bass"] == 0

    patched = client.patch(
        "/api/v1/settings",
        json={
            "name": "Lounge",
            "bass": 4,
            "treble": -2.5,
            "balance": 3,
            "loudness": True,
            "volume_limit": 80,
            "dac": "dacplus",
        },
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["name"] == "Lounge"
    assert body["bass"] == 4
    assert body["treble"] == -2.5
    assert body["balance"] == 3
    assert body["loudness"] is True
    assert body["volume_limit"] == 80
    assert body["dac"] == "dacplus"
    assert body["dac_reboot_required"] is True
    alsa = tmp_env.alsa_conf.read_text()
    assert "Eq10X2" in alsa
    # bass 4 + loudness 6 on the first band
    assert "10.0" in alsa or "10.00" in alsa

    again = client.get("/api/v1/settings").json()
    assert again["name"] == "Lounge"
    assert again["loudness"] is True


def test_settings_validation(client: TestClient) -> None:
    bad = client.patch("/api/v1/settings", json={"bass": 40})
    assert bad.status_code == 422
    bad_limit = client.patch("/api/v1/settings", json={"volume_limit": 0})
    assert bad_limit.status_code == 422

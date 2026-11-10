"""Unit tests for the official-client wrapper, with MusicAssistantClient mocked out."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from freesounder_control_api.backend import _player_to_room
from freesounder_control_api.ma_backend import MusicAssistantBackend


def test_player_to_room_maps_sync_group_fields() -> None:
    player = SimpleNamespace(
        player_id="kitchen",
        display_name="Kitchen",
        available=True,
        volume_level=41,
        volume_muted=False,
        synced_to="group-1",
        group_members=[],
        type="player",
        powered=True,
        state=SimpleNamespace(value="playing"),
    )
    room = _player_to_room(player)
    assert room.id == "kitchen"
    assert room.volume == 41
    assert room.group_id == "group-1"
    assert room.state == "playing"


@pytest.mark.asyncio
async def test_transport_calls_official_player_commands() -> None:
    backend = MusicAssistantBackend("http://ma:8095", token="tok")
    players = SimpleNamespace(
        play=AsyncMock(),
        pause=AsyncMock(),
        next_track=AsyncMock(),
        volume_set=AsyncMock(),
        group_volume=AsyncMock(),
        group=AsyncMock(),
        ungroup=AsyncMock(),
        create_group_player=AsyncMock(
            return_value=SimpleNamespace(
                player_id="g1",
                name="Downstairs",
                available=True,
                volume_level=10,
                volume_muted=False,
                group_members=["kitchen"],
                type="group",
                powered=True,
                state="idle",
            )
        ),
        fetch_state=AsyncMock(),
        players=[],
        get=MagicMock(return_value=None),
    )
    backend._client = SimpleNamespace(
        players=players, player_queues=SimpleNamespace(), music=SimpleNamespace()
    )

    await backend.play("kitchen")
    players.play.assert_awaited_once_with("kitchen")
    await backend.pause("kitchen")
    players.pause.assert_awaited_once_with("kitchen")
    await backend.next_track("kitchen")
    players.next_track.assert_awaited_once_with("kitchen")
    await backend.set_volume("kitchen", 30)
    players.volume_set.assert_awaited_once_with("kitchen", 30)
    await backend.set_group_volume("g1", 20)
    players.group_volume.assert_awaited_once_with("g1", 20)
    await backend.join_group("g1", "lounge")
    players.group.assert_awaited_once_with("lounge", "g1")
    await backend.leave_group("g1", "lounge")
    players.ungroup.assert_awaited_once_with("lounge")

    group = await backend.create_group("Downstairs", ["kitchen"], "snapcast", True)
    players.create_group_player.assert_awaited_once()
    assert group.id == "g1"
    assert group.is_group is True

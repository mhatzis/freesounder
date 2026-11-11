"""CI guard: the MA client we pin must work with the MA server the image bundles."""

from __future__ import annotations

import re
import tomllib
from importlib.metadata import version
from pathlib import Path

import pytest

from freesounder_control_api import ma_compat

ROOT = Path(__file__).resolve().parents[1]
FLOW_METHODS = (
    "setup_provider",
    "reconfigure_provider",
    "submit_setup_flow",
    "get_setup_flow",
    "abort_setup_flow",
)


def test_bundled_server_schema_is_known_and_sufficient() -> None:
    ref = ma_compat.bundled_image_ref()
    tag = ref.rsplit(":", 1)[1]
    assert tag in ma_compat.SERVER_SCHEMAS, f"add {tag}'s API_SCHEMA_VERSION to SERVER_SCHEMAS"
    schema = ma_compat.SERVER_SCHEMAS[tag]
    too_new = {c: s for c, s in ma_compat.COMMAND_MIN_SCHEMA.items() if s > schema}
    assert not too_new, f"MA {tag} (schema {schema}) lacks {too_new}"


def test_client_and_models_are_pinned_exactly() -> None:
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    assert f"music-assistant-client=={ma_compat.PINNED_CLIENT}" in deps
    assert f"music-assistant-models=={ma_compat.PINNED_MODELS}" in deps
    assert version("music-assistant-client") == ma_compat.PINNED_CLIENT
    assert version("music-assistant-models") == ma_compat.PINNED_MODELS


def test_schema_guarded_flow_calls_go_through_compat() -> None:
    src = "\n".join(p.read_text() for p in (ROOT / "src").rglob("*.py"))
    for name in FLOW_METHODS:
        assert not re.search(rf"client\.config\.{name}\(", src), (
            f"client.config.{name} requires schema 101; use ma_compat.{name}"
        )


class _Client:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    async def send_command(self, command: str, **kwargs):
        self.sent.append((command, kwargs))
        return {"flow_id": "f1", "step_id": "auth", "type": "external", "url": "https://x"}


@pytest.mark.asyncio
async def test_compat_sends_flow_commands_without_schema_guard() -> None:
    client = _Client()
    step = await ma_compat.setup_provider(client, "spotify")
    await ma_compat.get_setup_flow(client, "f1")
    await ma_compat.submit_setup_flow(client, "f1", {"a": 1})
    await ma_compat.reconfigure_provider(client, "spotify--1")
    await ma_compat.abort_setup_flow(client, "f1")
    assert step.flow_id == "f1" and step.url == "https://x"
    assert [c for c, _ in client.sent] == [
        "config/providers/setup",
        "config/flows/get",
        "config/flows/submit",
        "config/providers/reconfigure",
        "config/flows/abort",
    ]
    assert all("require_schema" not in kwargs for _, kwargs in client.sent)
    assert all(c in ma_compat.COMMAND_MIN_SCHEMA for c, _ in client.sent)


def _flow_step(step_type: str, **extra):
    from music_assistant_models.setup_flow import SetupFlowStep

    return SetupFlowStep.from_dict(
        {"flow_id": "f1", "step_id": "s1", "type": step_type, **extra}
    ).to_dict()


async def test_ensure_snapcast_creates_instance_through_setup_flow() -> None:
    """MA 2.10 refuses config/providers/save without an instance id (the v0.4.5 bug)."""

    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    client = SimpleNamespace(
        config=SimpleNamespace(
            get_provider_configs=AsyncMock(return_value=[]),
            save_provider_config=AsyncMock(side_effect=ValueError("setup flow only")),
        ),
        send_command=AsyncMock(return_value=_flow_step("finish")),
    )
    assert await ma_compat.ensure_snapcast(client) == "enabled just now"
    client.send_command.assert_awaited_once_with(
        "config/providers/setup", provider_domain="snapcast"
    )
    client.config.save_provider_config.assert_not_awaited()


async def test_ensure_snapcast_submits_form_and_reports_failure() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    client = SimpleNamespace(
        config=SimpleNamespace(get_provider_configs=AsyncMock(return_value=[])),
        send_command=AsyncMock(side_effect=[_flow_step("form"), _flow_step("finish")]),
    )
    assert await ma_compat.ensure_snapcast(client) == "enabled just now"
    client.send_command.assert_awaited_with(
        "config/flows/submit", flow_id="f1", values={"snapcast_use_external_server": False}
    )

    client.send_command = AsyncMock(return_value=_flow_step("abort", reason="provider_retired"))
    with pytest.raises(RuntimeError, match="provider_retired"):
        await ma_compat.ensure_snapcast(client)


async def test_ensure_snapcast_switches_existing_external_instance() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    existing = SimpleNamespace(
        instance_id="snapcast", values={"snapcast_use_external_server": True}
    )
    client = SimpleNamespace(
        config=SimpleNamespace(get_provider_configs=AsyncMock(return_value=[existing])),
        send_command=AsyncMock(return_value={}),
    )
    assert await ma_compat.ensure_snapcast(client) == "switched to the built-in snapserver"
    assert client.send_command.await_args.kwargs["instance_id"] == "snapcast"

"""Music Assistant setup-flow commands, matched to the bundled server.

The player image bundles Music Assistant 2.10.9 (API schema 65). Its
config/providers/setup, config/providers/reconfigure and config/flows/*
commands take exactly the arguments music-assistant-client 1.8.0 sends, but
that client guards them with require_schema=101 (the 2.11 beta schema), so it
refuses to send them to a 2.10.9 server ("update Music Assistant server to a
version that supports at least schema 101"). Send them directly instead.

Verified against server tag 2.10.9 (controllers/config/flows.py) and
music_assistant_models 1.1.205 vs 1.1.236 setup_flow.py (1.1.236 only adds
optional fields). tests/test_ma_compat.py keeps these tables honest.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from music_assistant_models.setup_flow import SetupFlowStep

# Music Assistant server image tag -> API_SCHEMA_VERSION in music_assistant/constants.py
SERVER_SCHEMAS: dict[str, int] = {"2.10.9": 65, "2.11.0b8": 101}

# Lowest server schema that has each command we call (flows arrived well before 65).
COMMAND_MIN_SCHEMA: dict[str, int] = {
    "config/providers": 28,
    "config/providers/get": 28,
    "config/providers/get_entries": 40,
    "config/providers/save": 28,
    "config/providers/remove": 28,
    "config/providers/setup": 65,
    "config/providers/reconfigure": 65,
    "config/flows/submit": 65,
    "config/flows/get": 65,
    "config/flows/abort": 65,
    "config/onboard_complete": 28,
}

PINNED_CLIENT = "1.8.0"
PINNED_MODELS = "1.1.236"


def bundled_image_ref(path: Path | None = None) -> str:
    """The MA image the player image ships (images/.../etc/freesounder/music-assistant-image)."""

    candidates = [path] if path else []
    candidates.append(Path("/etc/freesounder/music-assistant-image"))
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidates.append(
            parent
            / "images/player/pi-gen/stage-freesounder/01-filesystem/files/etc/freesounder"
            / "music-assistant-image"
        )
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate.read_text(encoding="utf-8").strip()
    raise FileNotFoundError("music-assistant-image pin not found")


async def setup_provider(client: Any, provider_domain: str) -> SetupFlowStep:
    return SetupFlowStep.from_dict(
        await client.send_command("config/providers/setup", provider_domain=provider_domain)
    )


async def reconfigure_provider(client: Any, instance_id: str) -> SetupFlowStep:
    return SetupFlowStep.from_dict(
        await client.send_command("config/providers/reconfigure", instance_id=instance_id)
    )


async def submit_setup_flow(client: Any, flow_id: str, values: dict[str, Any]) -> SetupFlowStep:
    return SetupFlowStep.from_dict(
        await client.send_command("config/flows/submit", flow_id=flow_id, values=values)
    )


async def get_setup_flow(client: Any, flow_id: str) -> SetupFlowStep:
    return SetupFlowStep.from_dict(await client.send_command("config/flows/get", flow_id=flow_id))


async def abort_setup_flow(client: Any, flow_id: str) -> None:
    await client.send_command("config/flows/abort", flow_id=flow_id)


SNAPCAST_VALUES = {"snapcast_use_external_server": False}


async def ensure_snapcast(client: Any) -> str:
    """Make sure MA's Snapcast provider exists and uses the built-in snapserver.

    MA 2.10 rejects `config/providers/save` without an instance id ("Adding a
    provider is only possible through the setup flow"), so a new instance must be
    created with `config/providers/setup`. Snapcast has no setup flow, so the
    server creates it at once and answers FINISH. Returns a short status string;
    raises when the provider could not be enabled.
    """

    from music_assistant_models.enums import FlowStepType

    configs = await client.config.get_provider_configs(provider_domain="snapcast")
    if configs:
        config = configs[0]
        values = getattr(config, "values", {}) or {}
        external = values.get("snapcast_use_external_server")
        external = getattr(external, "value", external)
        if external:
            await client.send_command(
                "config/providers/save",
                provider_domain="snapcast",
                values=SNAPCAST_VALUES,
                instance_id=config.instance_id,
            )
            return "switched to the built-in snapserver"
        return "enabled"
    step = await setup_provider(client, "snapcast")
    if step.type == FlowStepType.FORM:
        step = await submit_setup_flow(client, step.flow_id, dict(SNAPCAST_VALUES))
    if step.type == FlowStepType.ABORT and step.reason == "already_configured":
        return "enabled"
    if step.type != FlowStepType.FINISH:
        raise RuntimeError(
            f"Snapcast setup ended with {step.type.value}"
            + (f" ({step.reason})" if step.reason else "")
        )
    return "enabled just now"

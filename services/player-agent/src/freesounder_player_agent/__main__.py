"""CLI entry point: `freesounder-player-agent`."""

from __future__ import annotations

import logging

import uvicorn

from freesounder_player_agent.app import create_app
from freesounder_player_agent.config import load_settings


def main() -> None:
    """Load settings and serve the agent."""

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    app = create_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.agent_port, log_level="info")


if __name__ == "__main__":
    main()

"""CLI entry point: `freesounderd`."""

from __future__ import annotations

import logging

import uvicorn

from freesounderd.app import create_app
from freesounderd.config import load_settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level="info",
        ws_ping_interval=20,
    )


if __name__ == "__main__":
    main()

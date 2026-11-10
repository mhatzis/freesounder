"""CLI entry point: `freesounder-control-api` or `python -m freesounder_control_api`."""

from __future__ import annotations

import logging

import uvicorn

from freesounder_control_api.app import create_app
from freesounder_control_api.config import load_settings


def main() -> None:
    """Load settings and serve the FastAPI app."""

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    app = create_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()

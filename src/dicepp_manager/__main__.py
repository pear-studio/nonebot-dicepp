"""Run the standalone DicePP Manager service."""

from __future__ import annotations

from pathlib import Path

import uvicorn

from .api import create_manager_app
from .config import ManagerSettings


def main() -> None:
    settings = ManagerSettings.from_env(Path(__file__).resolve().parents[2])
    settings.layout.manager_state_dir.mkdir(parents=True, exist_ok=True)
    app = create_manager_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()

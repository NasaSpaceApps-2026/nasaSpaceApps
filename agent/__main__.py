"""`python -m agent` -> serve the agent backend on port 8766."""
from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "agent.app:create_app",
        factory=True,
        host=os.environ.get("ATLAS_AGENT_HOST", "127.0.0.1"),
        port=int(os.environ.get("ATLAS_AGENT_PORT", "8766")),
        log_level=os.environ.get("ATLAS_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()

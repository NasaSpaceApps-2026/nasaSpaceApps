"""Environment-driven settings, read at call time so tests can monkeypatch the environment."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = "claude-opus-5-5"


def _flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    model: str = DEFAULT_MODEL
    effort: str = "medium"
    max_tokens: int = 32000
    fallbacks: bool = False
    mcp_url: str = "http://localhost:8765/mcp"
    mcp_stdio: bool = False
    api_url: str = "http://localhost:8000"
    max_tool_calls: int = 12
    tool_timeout_s: float = 180.0
    context_timeout_s: float = 1.5
    replay_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent / "replays")
    record: bool = True
    replay_file: str | None = None
    replay_tools: bool = False
    replay_speed: float = 1.0
    replay_max_gap_s: float = 3.0

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get
        return cls(
            model=e("ATLAS_MODEL") or DEFAULT_MODEL,
            effort=e("ATLAS_EFFORT", "medium"),
            max_tokens=int(e("ATLAS_MAX_TOKENS", "32000")),
            fallbacks=_flag("ATLAS_FALLBACKS", False),
            mcp_url=e("ATLAS_MCP_URL") or "http://localhost:8765/mcp",
            mcp_stdio=_flag("ATLAS_MCP_STDIO", False),
            api_url=(e("ATLAS_API_URL") or "http://localhost:8000").rstrip("/"),
            max_tool_calls=int(e("ATLAS_MAX_TOOL_CALLS", "12")),
            tool_timeout_s=float(e("ATLAS_TOOL_TIMEOUT", "180")),
            context_timeout_s=float(e("ATLAS_CONTEXT_TIMEOUT", "1.5")),
            replay_dir=Path(e("ATLAS_REPLAY_DIR") or Path(__file__).resolve().parent / "replays"),
            record=_flag("ATLAS_RECORD", True),
            replay_file=e("ATLAS_REPLAY") or None,
            replay_tools=_flag("ATLAS_REPLAY_REPLAY_TOOLS", False),
            replay_speed=float(e("ATLAS_REPLAY_SPEED", "1")),
            replay_max_gap_s=float(e("ATLAS_REPLAY_MAX_GAP", "3")),
        )

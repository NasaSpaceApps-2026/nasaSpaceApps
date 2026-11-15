"""Environment-driven settings, read at call time so tests can monkeypatch the environment."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_MODELS = {"anthropic": DEFAULT_MODEL, "deepseek": "deepseek-chat"}
PROVIDERS = tuple(DEFAULT_MODELS)
KEY_VARS = {"anthropic": "ANTHROPIC_API_KEY", "deepseek": "DEEPSEEK_API_KEY"}


def parse_dotenv(text: str) -> dict[str, str]:
    """Tolerant .env parser: `KEY = value`, `export KEY=value`, comments, single/double quotes."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if not key:
            continue
        if val[:1] in ("'", '"') and val.find(val[0], 1) != -1:
            val = val[1:val.find(val[0], 1)]
        else:
            val = re.sub(r"\s+#.*$", "", val).strip()
        out[key] = val
    return out


def load_dotenv(path: Path | None = None, environ=None) -> list[str]:
    """Load .env into the environment without overriding existing variables. Returns names set (never values)."""
    environ = os.environ if environ is None else environ
    path = path or ROOT / ".env"
    try:
        vals = parse_dotenv(path.read_text(encoding="utf-8"))
    except OSError:
        return []
    set_: list[str] = []
    for k, v in vals.items():
        if v and not environ.get(k):
            environ[k] = v
            set_.append(k)
    return set_


def has_key(provider: str, environ=None) -> bool:
    """True if the API key variable for `provider` is set in `environ` (Anthropic also accepts ANTHROPIC_AUTH_TOKEN). Never returns or logs the key."""
    environ = os.environ if environ is None else environ
    if provider == "anthropic":
        return bool(environ.get("ANTHROPIC_API_KEY") or environ.get("ANTHROPIC_AUTH_TOKEN"))
    return bool(environ.get(KEY_VARS[provider]))


def select_provider(environ=None) -> str:
    """ATLAS_PROVIDER if set; else anthropic when its key is present, else deepseek when its key is present;
    else anthropic (the check() error then names ANTHROPIC_API_KEY)."""
    environ = os.environ if environ is None else environ
    p = (environ.get("ATLAS_PROVIDER") or "").strip().lower()
    if p:
        if p not in PROVIDERS:
            raise ValueError(f"ATLAS_PROVIDER must be one of {', '.join(PROVIDERS)} (got {p!r})")
        return p
    if has_key("anthropic", environ):
        return "anthropic"
    if has_key("deepseek", environ):
        return "deepseek"
    return "anthropic"


def _flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _opt_float(raw: str | None, default: float | None) -> float | None:
    if raw is None:
        return default
    raw = raw.strip().lower()
    return None if raw in ("", "none", "default") else float(raw)


@dataclass
class Settings:
    """Agent configuration (provider, model, effort, token cap, MCP target), built from environment variables by Settings.from_env at call time."""
    provider: str = "anthropic"
    model: str = DEFAULT_MODEL
    effort: str = "medium"
    max_tokens: int = 32000
    fallbacks: bool = False
    mcp_url: str = "http://localhost:8765/mcp"
    mcp_stdio: bool = False
    api_url: str = "http://localhost:8000"
    rover_api_url: str | None = None      # Yousef's API (ATLAS_ROVER_API_URL): rover state and markers for the context block
    max_tool_calls: int = 12
    tool_timeout_s: float = 180.0
    context_timeout_s: float = 1.5
    replay_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent / "replays")
    record: bool = True
    replay_file: str | None = None
    replay_tools: bool = False
    replay_speed: float = 1.0
    replay_max_gap_s: float = 3.0
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_thinking: bool = False       # ATLAS_DEEPSEEK_THINKING=1 enables DeepSeek thinking mode
    deepseek_temperature: float | None = 0.3  # ATLAS_DEEPSEEK_TEMPERATURE (empty or "none" = API default); low = steadier tool use and quoting

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get
        provider = select_provider()
        return cls(
            provider=provider,
            model=e("ATLAS_MODEL") or DEFAULT_MODELS[provider],
            effort=e("ATLAS_EFFORT", "medium"),
            max_tokens=int(e("ATLAS_MAX_TOKENS", "32000")),
            fallbacks=_flag("ATLAS_FALLBACKS", False),
            mcp_url=e("ATLAS_MCP_URL") or "http://localhost:8765/mcp",
            mcp_stdio=_flag("ATLAS_MCP_STDIO", False),
            api_url=(e("ATLAS_API_URL") or "http://localhost:8000").rstrip("/"),
            rover_api_url=(e("ATLAS_ROVER_API_URL") or "").rstrip("/") or None,
            max_tool_calls=int(e("ATLAS_MAX_TOOL_CALLS", "12")),
            tool_timeout_s=float(e("ATLAS_TOOL_TIMEOUT", "180")),
            context_timeout_s=float(e("ATLAS_CONTEXT_TIMEOUT", "1.5")),
            replay_dir=Path(e("ATLAS_REPLAY_DIR") or Path(__file__).resolve().parent / "replays"),
            record=_flag("ATLAS_RECORD", True),
            replay_file=e("ATLAS_REPLAY") or None,
            replay_tools=_flag("ATLAS_REPLAY_REPLAY_TOOLS", False),
            replay_speed=float(e("ATLAS_REPLAY_SPEED", "1")),
            replay_max_gap_s=float(e("ATLAS_REPLAY_MAX_GAP", "3")),
            deepseek_base_url=(e("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/"),
            deepseek_thinking=_flag("ATLAS_DEEPSEEK_THINKING", False),
            deepseek_temperature=_opt_float(e("ATLAS_DEEPSEEK_TEMPERATURE"), 0.3),
        )

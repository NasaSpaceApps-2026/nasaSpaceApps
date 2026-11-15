"""One recorded chat session: context fetch -> system blocks -> tool loop -> recording.

Shared by the HTTP app and the eval runner so both exercise exactly the same path.
"""
from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

import httpx

from .config import Settings
from .conversation import Conversation, render_entities, sanitize, track_entities, trim
from .context import fetch_context, render_context_block
from .loop import normalize_messages, run_turn
from .mcp_bridge import McpBridge, McpUnavailable
from .model import ModelClient
from .replay import Recorder

PROMPT_PATH = Path(__file__).resolve().parent / "system_prompt.md"


def load_system_prompt() -> str:
    """Read agent/system_prompt.md (ATLAS_SYSTEM_PROMPT=<file> overrides it, for A/B runs)."""
    import os
    return Path(os.environ.get("ATLAS_SYSTEM_PROMPT") or PROMPT_PATH).read_text(encoding="utf-8")


def build_system(prompt: str, context_block: str) -> list[dict]:
    # Stable prompt first (cacheable prefix), volatile per-turn context in its own block after it.
    """System blocks: the stable prompt (prompt-cached prefix) then the volatile per-turn context block."""
    return [{"type": "text", "text": prompt, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": context_block}]


async def run_session(*, model: ModelClient, bridge: McpBridge, settings: Settings, messages: list[dict],
                      client_context: dict | None = None, http: httpx.AsyncClient | None = None,
                      record_dir: Path | None = None, record: bool | None = None,
                      info: dict | None = None, conversation: Conversation | None = None) -> AsyncIterator[dict]:
    """`info`, if given, is filled with the session meta (and `recording` path) for callers that score it.
    With `conversation`, `messages` are only the NEW user message(s); the earlier transcript (tool calls and results
    included) comes from the conversation and is updated when the turn ends."""
    try:
        msgs = normalize_messages(messages)
    except ValueError as e:
        yield {"event": "error", "data": {"message": f"Bad request: {e}"}}
        return
    n_old = 0  # messages of `working` that came from earlier turns
    if conversation is not None:
        base, dropped = trim(sanitize(conversation.messages))
        conversation.dropped = (conversation.dropped + dropped)[-20:]
        n_new = len(msgs)
        msgs = sanitize(base + msgs)
        n_old = len(msgs) - n_new
    try:
        tools = await bridge.tools()
    except McpUnavailable as e:
        yield {"event": "error", "data": {"message": f"Tool server unavailable: {e}"}}
        return
    ctx = await fetch_context(settings.api_url, client_context, settings.context_timeout_s, http,
                              rover_url=getattr(settings, "rover_api_url", None))
    block = render_context_block(ctx, render_entities(conversation.entities, conversation.dropped)
                                 if conversation is not None else None)
    meta = {"user_prompt": msgs[-1]["content"], "messages": msgs, "context_block": block,
            "model": getattr(model, "name", "?"), "provider": settings.provider, "tools": [t["name"] for t in tools],
            "max_tool_calls": settings.max_tool_calls}
    rec = Recorder(record_dir or settings.replay_dir, meta,
                   enabled=settings.record if record is None else record)
    if info is not None:
        info.update(meta, recording=str(rec.path) if rec.path else None)
    working: list[dict] = []
    try:
        async for ev in run_turn(model=model, bridge=bridge, system=build_system(load_system_prompt(), block),
                                 messages=msgs, max_tool_calls=settings.max_tool_calls,
                                 tool_timeout_s=settings.tool_timeout_s, tools=tools, transcript=working):
            rec.write(ev["event"], ev["data"])
            yield ev
    finally:
        rec.close()
        if conversation is not None and working:
            answered = any(m["role"] == "assistant" for m in working[n_old:])
            if answered:  # a turn that failed before any model output leaves the transcript as it was
                track_entities(conversation.entities, working[n_old:])
                conversation.messages = sanitize(working)

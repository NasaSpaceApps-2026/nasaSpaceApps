"""The tool loop. One call to `run_turn` handles one user turn and yields SSE-ready events.

Events (dicts {"event": name, "data": {...}}):
  text             {delta}
  tool_call_start  {id, name, args}
  tool_call_result {id, summary, is_error, result}      (result = full text the model saw)
  done             {stop_reason, tool_calls}
  error            {message}
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from .mcp_bridge import McpBridge, McpUnavailable, ToolOutcome
from .model import ModelClient, describe_model_error

SUMMARY_CHARS = 240
BANNER_PREFIX = "[SYNTHETIC"

CAP_NOTE = ("[system note] The tool-call budget for this user turn ({cap}) is used up and no further tools "
            "can be called. Answer now using only the results above. Say plainly what you could not verify "
            "or complete. Do not invent numbers.")
SKIPPED = ("Not executed: the per-turn tool-call limit ({cap}) was reached. Answer with the results you "
           "already have and state what is missing.")


def summarize(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    synthetic = bool(lines and lines[0].startswith(BANNER_PREFIX))
    if synthetic:
        lines = lines[1:]
    s = " ".join(lines[:2]) if lines else "(empty)"
    if len(s) > SUMMARY_CHARS:
        s = s[: SUMMARY_CHARS - 1].rstrip() + "…"
    return ("[synthetic site] " if synthetic else "") + s


def normalize_messages(messages: list[dict]) -> list[dict]:
    """Client messages -> API messages. Accepts string or text-block content; drops empty turns."""
    out: list[dict] = []
    for m in messages:
        role, content = m.get("role"), m.get("content")
        if role not in ("user", "assistant"):
            raise ValueError(f"unsupported role {role!r}")
        if isinstance(content, list):
            content = "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
        if not isinstance(content, str):
            raise ValueError("message content must be a string")
        if not content.strip():
            continue
        out.append({"role": role, "content": content})
    if not out or out[0]["role"] != "user" or out[-1]["role"] != "user":
        raise ValueError("messages must start and end with a user message")
    return out


def validate_args(schema: dict, args: Any) -> str | None:
    """Minimal guard so truncated/odd streamed tool input never reaches a tool."""
    if not isinstance(args, dict):
        return "Tool input was not a JSON object (it may have been truncated). Re-issue the call."
    missing = [k for k in schema.get("required", []) if k not in args]
    if missing:
        return f"Missing required argument(s): {', '.join(missing)}. Re-issue the call with them."
    return None


async def run_turn(*, model: ModelClient, bridge: McpBridge, system: list[dict], messages: list[dict],
                   max_tool_calls: int = 12, tool_timeout_s: float = 180.0,
                   tools: list[dict] | None = None) -> AsyncIterator[dict]:
    if tools is None:
        try:
            tools = await bridge.tools()
        except McpUnavailable as e:
            yield {"event": "error", "data": {"message": f"Tool server unavailable: {e}"}}
            return
    schemas = {t["name"]: t["input_schema"] for t in tools}
    msgs = list(messages)
    calls = 0
    cap_reached = False

    for _ in range(max_tool_calls + 4):  # hard backstop on model round-trips
        final: dict | None = None
        try:
            async for kind, payload in model.stream_turn(system=system, messages=msgs, tools=tools,
                                                         no_tools=cap_reached):
                if kind == "text":
                    yield {"event": "text", "data": {"delta": payload}}
                elif kind == "final":
                    final = payload
        except Exception as e:  # noqa: BLE001 - surfaced to the client as an error event
            yield {"event": "error", "data": {"message": describe_model_error(e)}}
            return
        if final is None:
            yield {"event": "error", "data": {"message": "Model returned no final message."}}
            return

        content, stop = final["content"], final["stop_reason"]
        msgs.append({"role": "assistant", "content": content})
        uses = [b for b in content if b.get("type") == "tool_use"]

        if stop == "pause_turn":
            continue
        if not uses or stop in ("max_tokens", "refusal", "end_turn", "stop_sequence"):
            if uses and stop == "max_tokens":
                yield {"event": "error", "data": {"message": "The reply was cut off (max_tokens) mid tool call; "
                                                             "no tool was run."}}
                return
            yield {"event": "done", "data": {"stop_reason": stop, "tool_calls": calls}}
            return

        # Run the tool calls of this assistant message (concurrently), results in one user message.
        results: dict[str, tuple[str, bool]] = {}
        runnable = []
        for b in uses:
            yield {"event": "tool_call_start", "data": {"id": b["id"], "name": b["name"], "args": b.get("input")}}
            if calls >= max_tool_calls:
                cap_reached = True
                results[b["id"]] = (SKIPPED.format(cap=max_tool_calls), True)
                continue
            err = validate_args(schemas.get(b["name"], {}), b.get("input")) if b["name"] in schemas else None
            if err:
                results[b["id"]] = (err, True)
                continue
            calls += 1
            runnable.append(b)
        if calls >= max_tool_calls:
            cap_reached = True

        async def _run(b: dict) -> tuple[str, ToolOutcome]:
            return b["id"], await bridge.call(b["name"], b.get("input"), timeout=tool_timeout_s)

        pending = {asyncio.ensure_future(_run(b)) for b in runnable}
        # Emit results of pre-resolved (skipped/invalid) calls first, then as each real call finishes.
        for b in uses:
            if b["id"] in results:
                text, is_err = results[b["id"]]
                yield {"event": "tool_call_result", "data": {"id": b["id"], "summary": summarize(text),
                                                             "is_error": is_err, "result": text}}
        try:
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for fut in done:
                    tid, out = fut.result()
                    results[tid] = (out.text, out.is_error)
                    yield {"event": "tool_call_result", "data": {"id": tid, "summary": summarize(out.text),
                                                                 "is_error": out.is_error, "result": out.text}}
        finally:
            for fut in pending:
                fut.cancel()

        blocks: list[dict] = []
        for b in uses:
            text, is_err = results[b["id"]]
            blocks.append({"type": "tool_result", "tool_use_id": b["id"], "content": text,
                           **({"is_error": True} if is_err else {})})
        if cap_reached:
            blocks.append({"type": "text", "text": CAP_NOTE.format(cap=max_tool_calls)})
        msgs.append({"role": "user", "content": blocks})

    yield {"event": "error", "data": {"message": "Stopped: too many model round-trips in one turn."}}

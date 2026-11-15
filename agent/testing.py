"""ScriptedModel: a deterministic fake ModelClient for tests and key-less demos.

Each scripted turn is a list of content blocks (text / tool_use dicts), or a callable
`(messages) -> blocks` for turns that depend on earlier tool results. stop_reason is "tool_use"
if the turn has a tool_use block, else "end_turn", unless given as (blocks, stop_reason).
"""
from __future__ import annotations

import re
from typing import Any, AsyncIterator

Turn = Any  # list[dict] | tuple[list[dict], str] | Callable[[list[dict]], list[dict]]


def text(t: str) -> dict:
    """A text content block."""
    return {"type": "text", "text": t}


def tool(id: str, name: str, **input: Any) -> dict:
    """A tool_use content block with id, tool name and input arguments."""
    return {"type": "tool_use", "id": id, "name": name, "input": input}


class ScriptedModel:
    """Deterministic fake ModelClient replaying scripted turns; `calls` records what the loop sent."""
    name = "scripted"

    def __init__(self, turns: list[Turn]):
        self.turns = list(turns)
        self.calls: list[dict] = []  # what the loop sent, for assertions

    async def stream_turn(self, *, system, messages, tools, no_tools=False) -> AsyncIterator[tuple[str, Any]]:
        import copy
        self.calls.append({"system": copy.deepcopy(system), "messages": copy.deepcopy(messages),
                           "tools": [t["name"] for t in tools], "no_tools": no_tools})
        if not self.turns:
            raise AssertionError("ScriptedModel ran out of scripted turns")
        turn = self.turns.pop(0)
        stop = None
        if callable(turn):
            turn = turn(messages)
        if isinstance(turn, tuple):
            turn, stop = turn
        blocks = [dict(b) for b in turn]
        for b in blocks:
            if b["type"] == "text":
                for piece in re.findall(r"\S+\s*", b["text"]):
                    yield ("text", piece)
        if stop is None:
            stop = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn"
        yield ("final", {"content": blocks, "stop_reason": stop})

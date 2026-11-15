"""MCP client bridge: connects to the Atlas MCP server, lists tools, calls them.

The connection is owned by one background task (anyio cancel scopes must be entered and exited in
the same task) and is (re)established lazily, so the agent boots even when the MCP server is down.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from typing import Any

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

from .config import ROOT, Settings

MAX_RESULT_CHARS = 12000
SMALL_STRUCTURED_CHARS = 800


class McpUnavailable(RuntimeError):
    """The MCP server could not be reached or the call failed to start."""
    pass


@dataclass
class ToolOutcome:
    """Result of one tool call: the text the model sees and whether the server flagged an error."""
    text: str
    is_error: bool


def result_to_text(result: Any) -> str:
    """Prefer the MCP text content; fall back to / append structured content only when small."""
    parts = [c.text for c in (result.content or []) if getattr(c, "type", None) == "text"]
    text = "\n".join(p for p in parts if p).strip()
    sc = getattr(result, "structured_content", None)
    if sc is not None:
        blob = json.dumps(sc, separators=(",", ":"), default=str)
        if not text:
            text = blob
        elif len(blob) <= SMALL_STRUCTURED_CHARS:
            text += "\n[structured] " + blob
    if not text:
        text = "(tool returned no content)"
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + f"\n[truncated: result was {len(text)} chars; narrow the query or page with get_set]"
    return text


def claude_tool_def(tool: Any) -> dict:
    """MCP tool -> Anthropic tool definition (name, description, input_schema with eager input streaming)."""
    schema = dict(tool.input_schema or {})
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    d = {"name": tool.name, "description": tool.description or tool.title or tool.name,
         "input_schema": schema, "eager_input_streaming": True}
    return d


def stdio_params() -> StdioServerParameters:
    """Parameters to launch mcp/server.py over stdio with the current interpreter and environment."""
    env = dict(os.environ)
    return StdioServerParameters(command=sys.executable, args=[str(ROOT / "mcp" / "server.py")], env=env)


class McpBridge:
    """Lazy MCP client: connects on first use (so the agent boots without the server), lists tools and calls them; the connection lives in one background task."""
    def __init__(self, target: Any | None = None, settings: Settings | None = None):
        """target: anything mcp.client.Client accepts (URL str, StdioServerParameters, server).
        None -> chosen from settings (ATLAS_MCP_STDIO / ATLAS_MCP_URL)."""
        self._settings = settings
        self._target = target
        self._client: Client | None = None
        self._tools: list[Any] = []
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._error: BaseException | None = None
        self._lock = asyncio.Lock()
        self._broken = False

    @property
    def connected(self) -> bool:
        return self._client is not None and not self._broken

    @property
    def tool_names(self) -> list[str]:
        return [t.name for t in self._tools]

    def _resolve_target(self) -> Any:
        if self._target is not None:
            return self._target
        s = self._settings or Settings.from_env()
        return stdio_params() if s.mcp_stdio else s.mcp_url

    async def _owner(self) -> None:
        try:
            async with Client(self._resolve_target()) as c:
                self._client = c
                self._tools = await self._list_all(c)
                self._ready.set()
                await self._stop.wait()
        except BaseException as e:  # noqa: BLE001 - reported through ensure()
            self._error = e
            if isinstance(e, asyncio.CancelledError):
                raise
        finally:
            self._client = None
            self._ready.set()

    @staticmethod
    async def _list_all(c: Client) -> list[Any]:
        tools, cursor = [], None
        while True:
            page = await c.list_tools(cursor=cursor)
            tools.extend(page.tools)
            cursor = getattr(page, "next_cursor", None)
            if not cursor:
                return tools

    async def ensure(self) -> None:
        async with self._lock:
            if self._task is not None and not self._task.done() and not self._broken and self._client is not None:
                return
            await self._shutdown()
            self._ready = asyncio.Event()
            self._stop = asyncio.Event()
            self._error = None
            self._broken = False
            self._task = asyncio.create_task(self._owner(), name="mcp-bridge")
            try:
                await asyncio.wait_for(self._ready.wait(), timeout=30)
            except asyncio.TimeoutError:
                await self._shutdown()
                raise McpUnavailable("Timed out connecting to the MCP server.") from None
            if self._client is None:
                err = self._error
                await self._shutdown()
                raise McpUnavailable(f"Could not connect to the MCP server: {type(err).__name__}: {err}" if err
                                     else "Could not connect to the MCP server.")

    async def _shutdown(self) -> None:
        t, self._task = self._task, None
        if t is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(asyncio.shield(t), timeout=5)
        except BaseException:  # noqa: BLE001
            t.cancel()
            try:
                await t
            except BaseException:  # noqa: BLE001
                pass

    async def stop(self) -> None:
        async with self._lock:
            await self._shutdown()

    async def tools(self) -> list[dict]:
        """Claude tool definitions, re-discovered from the server each call."""
        await self.ensure()
        try:
            assert self._client is not None
            self._tools = await self._list_all(self._client)
        except Exception:  # keep the cached list; the next call will reconnect if the link is dead
            self._broken = True
            await self.ensure()
        return [claude_tool_def(t) for t in self._tools]

    async def call(self, name: str, args: dict | None, timeout: float | None = None) -> ToolOutcome:
        try:
            await self.ensure()
            assert self._client is not None
            res = await asyncio.wait_for(self._client.call_tool(name, args or {}), timeout=timeout)
        except asyncio.TimeoutError:
            return ToolOutcome(f"Tool {name} timed out after {timeout:.0f}s. Its effect may be partial; "
                               "check state before retrying.", True)
        except McpUnavailable as e:
            return ToolOutcome(f"Tool server unavailable: {e}", True)
        except Exception as e:  # protocol-level failure (unknown tool, invalid args, dead link)
            self._broken = isinstance(e, (OSError, EOFError, ConnectionError)) or self._broken
            return ToolOutcome(f"Tool call failed ({type(e).__name__}): {str(e)[:400]}", True)
        return ToolOutcome(result_to_text(res), bool(getattr(res, "is_error", False)))

"""FastAPI app: POST /chat (SSE), GET /health. `python -m agent` serves it on :8766."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from .config import Settings, has_key, load_dotenv
from .mcp_bridge import McpBridge
from .model import ModelClient, make_model
from .replay import replay_events
from .session import run_session


class ChatRequest(BaseModel):
    """POST /chat body: the conversation `messages` (Anthropic message dicts) and optional client `context` (viewer state such as the selected markers)."""
    messages: list[dict[str, Any]]
    context: dict[str, Any] | None = None


def create_app(*, model: ModelClient | None = None, bridge: McpBridge | None = None,
               settings: Settings | None = None) -> FastAPI:
    """Build the agent FastAPI app (POST /chat as SSE, GET /health). `model`, `bridge` and `settings` can be injected for tests; otherwise they come from the environment."""
    if settings is None:
        load_dotenv()  # .env fills only variables not already in the environment
        settings = Settings.from_env()
    bridge = bridge or McpBridge(settings=settings)
    state: dict[str, Any] = {"model": model, "http": None}

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        state["http"] = httpx.AsyncClient()
        try:
            yield
        finally:
            await state["http"].aclose()
            await bridge.stop()

    app = FastAPI(title="Ares Atlas agent", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    def get_model() -> ModelClient:
        if state["model"] is None:
            m = make_model(settings)
            m.check()  # raises ModelUnavailable without credentials; nothing is cached on failure
            state["model"] = m
        return state["model"]

    @app.get("/health")
    async def health():
        key = has_key(settings.provider)
        return {"ok": True, "provider": settings.provider, "model": settings.model, "api_key_present": key,
                "mcp_connected": bridge.connected, "tools": bridge.tool_names,
                "mcp_target": "stdio" if settings.mcp_stdio else settings.mcp_url,
                "rest_api": settings.api_url, "rover_api": settings.rover_api_url, "replay": settings.replay_file,
                "max_tool_calls": settings.max_tool_calls}

    def sse(ev: dict) -> dict:
        return {"event": ev["event"], "data": json.dumps(ev["data"], ensure_ascii=False, default=str)}

    @app.post("/chat")
    async def chat(req: ChatRequest):
        async def stream():
            if settings.replay_file:
                async def reissue(name: str, args: dict):
                    return await bridge.call(name, args, timeout=settings.tool_timeout_s)
                try:
                    async for ev in replay_events(settings.replay_file, settings.replay_speed,
                                                  settings.replay_max_gap_s,
                                                  reissue if settings.replay_tools else None):
                        yield sse(ev)
                except (OSError, ValueError) as e:
                    yield sse({"event": "error", "data": {"message": f"Replay failed: {e}"}})
                return
            try:
                m = get_model()
            except Exception as e:  # noqa: BLE001
                yield sse({"event": "error", "data": {"message": str(e)}})
                return
            async for ev in run_session(model=m, bridge=bridge, settings=settings, messages=req.messages,
                                        client_context=req.context, http=state["http"]):
                yield sse(ev)

        return EventSourceResponse(stream(), ping=15)

    return app

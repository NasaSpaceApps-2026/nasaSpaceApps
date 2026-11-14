"""Model clients. The loop only depends on `ModelClient.stream_turn`, so tests inject a fake.

stream_turn yields ("text", delta) as text streams, then exactly one ("final", {"content": [block
dicts], "stop_reason": str}). Block dicts are ready to append to `messages` unchanged.
"""
from __future__ import annotations

import os
from typing import Any, AsyncIterator, Protocol

from .config import Settings

ModelEvent = tuple[str, Any]

_KEEP = {
    "text": ("type", "text"),
    "tool_use": ("type", "id", "name", "input"),
    "thinking": ("type", "thinking", "signature"),
    "redacted_thinking": ("type", "data"),
}


class ModelUnavailable(RuntimeError):
    """No credentials / cannot reach the model. Message is safe to show the user."""


class ModelClient(Protocol):
    name: str

    def stream_turn(self, *, system: list[dict], messages: list[dict], tools: list[dict],
                    no_tools: bool = False) -> AsyncIterator[ModelEvent]: ...


def block_to_dict(block: Any) -> dict:
    d = block.model_dump(exclude_none=True, mode="json")
    keep = _KEEP.get(d.get("type"))
    return {k: d[k] for k in keep if k in d} if keep else d


class AnthropicModel:
    """Claude via the official SDK: streaming, adaptive thinking, effort, prompt caching on the system
    prompt, eager tool-input streaming, server-side refusal fallbacks (beta)."""

    def __init__(self, settings: Settings | None = None, client: Any | None = None):
        s = settings or Settings.from_env()
        self.name = s.model
        self._s = s
        self._client = client
        self._fallbacks = s.fallbacks

    def _get_client(self):
        if self._client is None:
            import anthropic
            has_creds = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
            if not has_creds:
                raise ModelUnavailable(
                    "ANTHROPIC_API_KEY is not set. Set it and restart the agent, or set ATLAS_REPLAY=<file> "
                    "to replay a recorded session.")
            self._client = anthropic.AsyncAnthropic()
        return self._client

    def check(self) -> None:
        """Fail fast (ModelUnavailable) when no credentials are configured."""
        self._get_client()

    def _kwargs(self, system, messages, tools, no_tools: bool, fallbacks: bool) -> dict:
        kw: dict[str, Any] = dict(model=self._s.model, max_tokens=self._s.max_tokens, system=system,
                                  messages=messages, tools=tools, thinking={"type": "adaptive"})
        if self._s.effort:
            kw["output_config"] = {"effort": self._s.effort}
        if no_tools:
            kw["tool_choice"] = {"type": "none"}
        if fallbacks:
            kw["betas"] = ["server-side-fallback-2026-07-01"]
            kw["fallbacks"] = "default"
        return kw

    async def stream_turn(self, *, system, messages, tools, no_tools=False) -> AsyncIterator[ModelEvent]:
        import anthropic
        client = self._get_client()
        for attempt in (0, 1):
            use_fb = self._fallbacks
            kw = self._kwargs(system, messages, tools, no_tools, use_fb)
            api = client.beta.messages if use_fb else client.messages
            emitted = False
            try:
                async with api.stream(**kw) as stream:
                    async for ev in stream:
                        if ev.type == "content_block_delta" and ev.delta.type == "text_delta":
                            emitted = True
                            yield ("text", ev.delta.text)
                    final = await stream.get_final_message()
                break
            except (anthropic.BadRequestError, TypeError):
                # Fallback beta unsupported by this account, platform or SDK: retry once without it.
                if use_fb and not emitted and attempt == 0:
                    self._fallbacks = False
                    continue
                raise
        yield ("final", {"content": [block_to_dict(b) for b in final.content],
                         "stop_reason": final.stop_reason or "end_turn"})


def describe_model_error(e: BaseException) -> str:
    try:
        import anthropic
    except ImportError:  # pragma: no cover
        return str(e)
    if isinstance(e, ModelUnavailable):
        return str(e)
    if isinstance(e, anthropic.AuthenticationError):
        return "The Anthropic API rejected the credentials (check ANTHROPIC_API_KEY)."
    if isinstance(e, anthropic.RateLimitError):
        return "Rate limited by the Anthropic API. Wait a moment and retry."
    if isinstance(e, anthropic.APIConnectionError):
        return "Could not reach the Anthropic API (network error)."
    if isinstance(e, anthropic.APIStatusError):
        return f"Anthropic API error {e.status_code}: {getattr(e, 'message', str(e))[:300]}"
    return f"{type(e).__name__}: {str(e)[:300]}"

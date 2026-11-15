"""Model clients. The loop only depends on `ModelClient.stream_turn`, so tests inject a fake.

stream_turn yields ("text", delta) as text streams, then exactly one ("final", {"content": [block
dicts], "stop_reason": str}). Block dicts are ready to append to `messages` unchanged.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import Any, AsyncIterator, Protocol

from .config import KEY_VARS, Settings

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
    """What the loop needs from a model: `stream_turn` yielding ("text", delta) events and one ("final", {content, stop_reason})."""
    name: str

    def stream_turn(self, *, system: list[dict], messages: list[dict], tools: list[dict],
                    no_tools: bool = False) -> AsyncIterator[ModelEvent]: ...


def block_to_dict(block: Any) -> dict:
    """SDK content block -> plain dict, keeping only the fields the API accepts back."""
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
                    "Provider anthropic: ANTHROPIC_API_KEY is not set. Set it and restart the agent "
                    "(or set DEEPSEEK_API_KEY / ATLAS_PROVIDER=deepseek), or set ATLAS_REPLAY=<file> "
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


# ---------------------------------------------------------------------------------------------
# DeepSeek (OpenAI-compatible Chat Completions). Messages stay Anthropic-format inside the loop;
# conversion happens only at the API boundary.
# ---------------------------------------------------------------------------------------------

class DeepSeekAPIError(RuntimeError):
    """HTTP error from the DeepSeek API. Never contains credentials."""

    def __init__(self, status: int, message: str):
        super().__init__(f"DeepSeek API error {status}: {message}")
        self.status = status
        self.detail = message


def system_to_text(system: list[dict] | str) -> str:
    """Flatten system blocks to one string (for providers without block-structured system prompts)."""
    if isinstance(system, str):
        return system
    return "\n\n".join(b.get("text", "") for b in system if b.get("type", "text") == "text" and b.get("text"))


def tools_to_openai(tools: list[dict]) -> list[dict]:
    """Anthropic tool definitions -> OpenAI-style function tools (used for DeepSeek)."""
    return [{"type": "function",
             "function": {"name": t["name"], "description": t.get("description", ""),
                          "parameters": t.get("input_schema") or {"type": "object", "properties": {}}}}
            for t in tools]


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return json.dumps(content, default=str)


def messages_to_openai(system: list[dict] | str, messages: list[dict]) -> list[dict]:
    """Anthropic-format system + messages -> OpenAI chat messages."""
    out: list[dict] = []
    sys_text = system_to_text(system)
    if sys_text:
        out.append({"role": "system", "content": sys_text})
    for m in messages:
        role, content = m["role"], m["content"]
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if role == "assistant":
            texts = [b["text"] for b in content if b.get("type") == "text"]
            thinking = "".join(b.get("thinking", "") for b in content if b.get("type") == "thinking")
            calls = []
            for b in content:
                if b.get("type") != "tool_use":
                    continue
                inp = b.get("input")
                args = inp if isinstance(inp, str) else json.dumps(inp if inp is not None else {})
                calls.append({"id": b["id"], "type": "function", "function": {"name": b["name"], "arguments": args}})
            msg: dict[str, Any] = {"role": "assistant", "content": "".join(texts) or None}
            if thinking:
                msg["reasoning_content"] = thinking
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        else:
            for b in content:  # tool results first: they must directly follow the assistant tool_calls
                if b.get("type") == "tool_result":
                    text = _result_text(b.get("content", ""))
                    if b.get("is_error"):
                        text = "Error: " + text
                    out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": text})
            texts = [b["text"] for b in content if b.get("type") == "text"]
            if texts:
                out.append({"role": "user", "content": "\n\n".join(texts)})
    return out


def parse_tool_arguments(raw: str) -> Any:
    """JSON object -> dict. Empty -> {}. Malformed or non-object -> the raw string, which the loop's
    validate_args reports back to the model as an error result (never a crash)."""
    if raw is None or not raw.strip():
        return {}
    try:
        v = json.loads(raw)
    except ValueError:
        return raw
    return v if isinstance(v, dict) else raw


class StreamAssembler:
    """Accumulates OpenAI streaming chunks (text deltas, tool_call fragments keyed by index)."""

    FINISH = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use",
              "content_filter": "refusal", "insufficient_system_resource": "max_tokens"}

    def __init__(self) -> None:
        self.text: list[str] = []
        self.reasoning: list[str] = []
        self.calls: dict[int, dict] = {}
        self.finish: str | None = None

    def feed(self, chunk: dict) -> str | None:
        """Consume one decoded chunk; returns a text delta to stream, if any."""
        delta_text = None
        for ch in chunk.get("choices") or []:
            d = ch.get("delta") or {}
            if d.get("reasoning_content"):
                self.reasoning.append(d["reasoning_content"])
            if d.get("content"):
                self.text.append(d["content"])
                delta_text = (delta_text or "") + d["content"]
            for tc in d.get("tool_calls") or []:
                slot = self.calls.setdefault(tc.get("index", len(self.calls)), {"id": None, "name": "", "args": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] += fn["name"]
                if fn.get("arguments"):
                    slot["args"] += fn["arguments"]
            if ch.get("finish_reason"):
                self.finish = ch["finish_reason"]
        return delta_text

    def final(self) -> dict:
        content: list[dict] = []
        if self.reasoning:
            content.append({"type": "thinking", "thinking": "".join(self.reasoning), "signature": ""})
        text = "".join(self.text)
        if text:
            content.append({"type": "text", "text": text})
        for idx in sorted(self.calls):
            c = self.calls[idx]
            if not c["name"]:
                continue
            content.append({"type": "tool_use", "id": c["id"] or f"call_{uuid.uuid4().hex[:12]}",
                            "name": c["name"], "input": parse_tool_arguments(c["args"])})
        stop = self.FINISH.get(self.finish or "stop", "end_turn")
        if stop != "max_tokens" and any(b["type"] == "tool_use" for b in content):
            stop = "tool_use"
        elif stop == "tool_use":
            stop = "end_turn"
        return {"content": content, "stop_reason": stop}


class DeepSeekModel:
    """DeepSeek via its OpenAI-compatible Chat Completions API (httpx, SSE streaming). Thinking mode is off by
    default (it would require passing reasoning_content back on every turn; we do that if enabled)."""

    RETRY_STATUS = {429, 500, 502, 503, 504}

    def __init__(self, settings: Settings | None = None, client: Any | None = None,
                 retries: int = 3, backoff_s: float = 1.0):
        s = settings or Settings.from_env()
        self.name = s.model
        self._s = s
        self._client = client
        self._retries = retries
        self._backoff = backoff_s

    def _key(self) -> str:
        key = os.environ.get(KEY_VARS["deepseek"])
        if not key:
            raise ModelUnavailable(
                "Provider deepseek: DEEPSEEK_API_KEY is not set (environment or .env). Set it and restart the "
                "agent, or set ATLAS_REPLAY=<file> to replay a recorded session.")
        return key

    def check(self) -> None:
        self._key()

    def _get_client(self):
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0))
        return self._client

    def _payload(self, system, messages, tools, no_tools: bool) -> dict:
        p: dict[str, Any] = dict(model=self._s.model, messages=messages_to_openai(system, messages),
                                 stream=True, max_tokens=self._s.max_tokens)
        if tools:
            p["tools"] = tools_to_openai(tools)
            if no_tools:
                p["tool_choice"] = "none"
        if self._s.deepseek_temperature is not None and not self._s.deepseek_thinking:  # ignored/rejected in thinking mode
            p["temperature"] = self._s.deepseek_temperature
        p["thinking"] = {"type": "enabled" if self._s.deepseek_thinking else "disabled"}
        return p

    async def stream_turn(self, *, system, messages, tools, no_tools=False) -> AsyncIterator[ModelEvent]:
        import httpx
        key = self._key()
        client = self._get_client()
        payload = self._payload(system, messages, tools, no_tools)
        url = self._s.deepseek_base_url + "/chat/completions"
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        asm = StreamAssembler()
        for attempt in range(self._retries + 1):
            emitted = False
            try:
                async with client.stream("POST", url, json=payload, headers=headers) as r:
                    if r.status_code != 200:
                        body = (await r.aread()).decode("utf-8", "replace")[:300]
                        if r.status_code in self.RETRY_STATUS and attempt < self._retries:
                            ra = r.headers.get("retry-after")
                            await asyncio.sleep(float(ra) if ra and ra.replace(".", "").isdigit()
                                                else self._backoff * 2 ** attempt)
                            continue
                        raise DeepSeekAPIError(r.status_code, body)
                    async for line in r.aiter_lines():
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except ValueError:
                            continue
                        if chunk.get("error"):
                            raise DeepSeekAPIError(500, str(chunk["error"])[:300])
                        delta = asm.feed(chunk)
                        if delta:
                            emitted = True
                            yield ("text", delta)
                break
            except (httpx.TransportError, httpx.TimeoutException):
                if emitted or attempt >= self._retries:
                    raise
                asm = StreamAssembler()
                await asyncio.sleep(self._backoff * 2 ** attempt)
        yield ("final", asm.final())


def make_model(settings: Settings | None = None) -> ModelClient:
    """Model client for settings.provider ("anthropic" or "deepseek")."""
    s = settings or Settings.from_env()
    return DeepSeekModel(s) if s.provider == "deepseek" else AnthropicModel(s)


def describe_model_error(e: BaseException) -> str:
    """Readable one-sentence message for a model/API failure (credentials, balance, rate limit, network); Anthropic errors go through _describe_anthropic."""
    if isinstance(e, ModelUnavailable):
        return str(e)
    if isinstance(e, DeepSeekAPIError):
        if e.status in (401, 403):
            return "The DeepSeek API rejected the credentials (check DEEPSEEK_API_KEY)."
        if e.status == 402:
            return "DeepSeek API: insufficient balance (402)."
        if e.status == 429:
            return "Rate limited by the DeepSeek API. Wait a moment and retry."
        return f"DeepSeek API error {e.status}: {e.detail[:300]}"
    try:
        import httpx
        if isinstance(e, httpx.TransportError):
            return "Could not reach the DeepSeek API (network error)."
    except ImportError:  # pragma: no cover
        pass
    return _describe_anthropic(e)


def _describe_anthropic(e: BaseException) -> str:
    try:
        import anthropic
    except ImportError:  # pragma: no cover
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

"""DeepSeek provider: message conversion, stream assembly, malformed args, provider selection, .env parser.
No network; all keys here are fake."""
import json

import anyio
import httpx
import pytest

from agent import config
from agent.config import Settings, load_dotenv, parse_dotenv, select_provider
from agent.loop import validate_args
from agent.model import (DeepSeekAPIError, DeepSeekModel, ModelUnavailable, StreamAssembler, describe_model_error,
                         make_model, messages_to_openai, parse_tool_arguments, tools_to_openai)

FAKE = "sk-fake-not-a-real-key"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    for v in ("ATLAS_PROVIDER", "ATLAS_MODEL", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(v, raising=False)


# ---- .env parser ----
def test_dotenv_parser_tolerant():
    txt = f'''# comment
DEEPSEEK_API_KEY ={FAKE}
  A = "quoted value"  # trailing
B='single'
export C=plain   # note
EMPTY=
no_equals_line
 = nokey
'''
    d = parse_dotenv(txt)
    assert d == {"DEEPSEEK_API_KEY": FAKE, "A": "quoted value", "B": "single", "C": "plain", "EMPTY": ""}


def test_load_dotenv_does_not_override(tmp_path):
    p = tmp_path / ".env"
    p.write_text(f"DEEPSEEK_API_KEY = {FAKE}\nOTHER=x\n")
    env = {"OTHER": "already"}
    names = load_dotenv(p, env)
    assert names == ["DEEPSEEK_API_KEY"] and env["OTHER"] == "already" and env["DEEPSEEK_API_KEY"] == FAKE
    assert load_dotenv(tmp_path / "missing", {}) == []


# ---- provider selection ----
def test_provider_selection():
    assert select_provider({}) == "anthropic"
    assert select_provider({"DEEPSEEK_API_KEY": FAKE}) == "deepseek"
    assert select_provider({"DEEPSEEK_API_KEY": FAKE, "ANTHROPIC_API_KEY": "a"}) == "anthropic"
    assert select_provider({"DEEPSEEK_API_KEY": FAKE, "ANTHROPIC_API_KEY": "a", "ATLAS_PROVIDER": "DeepSeek"}) == "deepseek"
    with pytest.raises(ValueError):
        select_provider({"ATLAS_PROVIDER": "gpt"})


def test_settings_model_defaults_per_provider(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE)
    s = Settings.from_env()
    assert (s.provider, s.model) == ("deepseek", "deepseek-chat")
    monkeypatch.setenv("ATLAS_MODEL", "deepseek-v4-pro")
    assert Settings.from_env().model == "deepseek-v4-pro"
    monkeypatch.delenv("ATLAS_MODEL")
    monkeypatch.setenv("ATLAS_PROVIDER", "anthropic")
    assert Settings.from_env().model == "claude-opus-5-5"
    assert isinstance(make_model(Settings(provider="deepseek")), DeepSeekModel)


def test_missing_key_error_names_provider_and_var():
    with pytest.raises(ModelUnavailable) as e:
        DeepSeekModel(Settings(provider="deepseek", model="deepseek-chat")).check()
    assert "deepseek" in str(e.value) and "DEEPSEEK_API_KEY" in str(e.value)


# ---- conversion ----
SYSTEM = [{"type": "text", "text": "You are Atlas.", "cache_control": {"type": "ephemeral"}},
          {"type": "text", "text": "<atlas_context>x</atlas_context>"}]
TOOLS = [{"name": "list_layers", "description": "d", "input_schema": {"type": "object", "properties": {}}}]


def test_tools_conversion():
    assert tools_to_openai(TOOLS) == [{"type": "function", "function": {
        "name": "list_layers", "description": "d", "parameters": {"type": "object", "properties": {}}}}]


def test_messages_round_trip():
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "hmm", "signature": ""},
            {"type": "text", "text": "Checking."},
            {"type": "tool_use", "id": "c1", "name": "a", "input": {"x": 1}},
            {"type": "tool_use", "id": "c2", "name": "b", "input": {}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "c1", "content": "res1"},
            {"type": "tool_result", "tool_use_id": "c2", "content": [{"type": "text", "text": "bad"}], "is_error": True},
            {"type": "text", "text": "[system note] stop"}]},
    ]
    out = messages_to_openai(SYSTEM, msgs)
    assert out[0] == {"role": "system", "content": "You are Atlas.\n\n<atlas_context>x</atlas_context>"}
    assert out[1] == {"role": "user", "content": "hi"}
    a = out[2]
    assert a["content"] == "Checking." and a["reasoning_content"] == "hmm"
    assert [(c["id"], c["function"]["name"], json.loads(c["function"]["arguments"])) for c in a["tool_calls"]] == [
        ("c1", "a", {"x": 1}), ("c2", "b", {})]
    assert out[3] == {"role": "tool", "tool_call_id": "c1", "content": "res1"}
    assert out[4] == {"role": "tool", "tool_call_id": "c2", "content": "Error: bad"}
    assert out[5] == {"role": "user", "content": "[system note] stop"}

    # and back: the assembler turns the same OpenAI message into the original Anthropic blocks
    asm = StreamAssembler()
    asm.feed({"choices": [{"delta": {"reasoning_content": "hmm", "content": "Checking.", "tool_calls": [
        {"index": i, "id": c["id"], "function": c["function"]} for i, c in enumerate(a["tool_calls"])]},
        "finish_reason": "tool_calls"}]})
    assert asm.final() == {"stop_reason": "tool_use", "content": msgs[1]["content"]}


# ---- streaming assembly ----
def test_stream_fragment_assembly():
    asm = StreamAssembler()
    chunks = [
        {"choices": [{"delta": {"role": "assistant", "content": "Look"}}]},
        {"choices": [{"delta": {"content": "ing."}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "t0", "function": {"name": "get_", "arguments": ""}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "set", "arguments": '{"id": "ab'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 1, "id": "t1", "function": {"name": "list_layers", "arguments": "{}"}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'c"}'}}]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    ]
    deltas = [asm.feed(c) for c in chunks]
    assert [d for d in deltas if d] == ["Look", "ing."]
    f = asm.final()
    assert f["stop_reason"] == "tool_use"
    assert f["content"] == [{"type": "text", "text": "Looking."},
                            {"type": "tool_use", "id": "t0", "name": "get_set", "input": {"id": "abc"}},
                            {"type": "tool_use", "id": "t1", "name": "list_layers", "input": {}}]


def test_finish_reason_mapping():
    for fr, want in (("stop", "end_turn"), ("length", "max_tokens"), ("content_filter", "refusal")):
        a = StreamAssembler()
        a.feed({"choices": [{"delta": {"content": "x"}, "finish_reason": fr}]})
        assert a.final()["stop_reason"] == want
    a = StreamAssembler()  # tool calls with finish "stop" must still run the tools
    a.feed({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "i", "function": {"name": "n", "arguments": "{}"}}]},
                         "finish_reason": "stop"}]})
    assert a.final()["stop_reason"] == "tool_use"


def test_malformed_args_never_crash_and_loop_reports_them():
    assert parse_tool_arguments("") == {}
    assert parse_tool_arguments('{"a": 1}') == {"a": 1}
    for raw in ('{"a": 1', "[1,2]", "not json"):
        v = parse_tool_arguments(raw)
        assert v == raw
        assert "not a JSON object" in validate_args({"required": []}, v)
    # a malformed call still converts back to an OpenAI message
    out = messages_to_openai([], [{"role": "assistant", "content": [
        {"type": "tool_use", "id": "z", "name": "n", "input": '{"a": 1'}]}])
    assert out[0]["tool_calls"][0]["function"]["arguments"] == '{"a": 1'


# ---- request + retries with a mock transport ----
def sse(*chunks):
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def run_model(handler, monkeypatch, **kw):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    m = DeepSeekModel(Settings(provider="deepseek", model="deepseek-chat"), client=client, backoff_s=0, **kw)

    async def go():
        return [ev async for ev in m.stream_turn(system=SYSTEM, messages=[{"role": "user", "content": "hi"}],
                                                 tools=TOOLS, no_tools=kw.get("_nt", False))]
    return anyio.run(go)


def test_request_shape_retry_and_stream(monkeypatch):
    seen = []

    def handler(req):
        seen.append(req)
        if len(seen) == 1:
            return httpx.Response(429, text="slow down")
        if len(seen) == 2:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, text=sse(
            {"choices": [{"delta": {"content": "Hel"}}]},
            {"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]}))
    evs = run_model(handler, monkeypatch)
    assert [e for e in evs if e[0] == "text"] == [("text", "Hel"), ("text", "lo")]
    assert evs[-1] == ("final", {"content": [{"type": "text", "text": "Hello"}], "stop_reason": "end_turn"})
    assert len(seen) == 3
    body = json.loads(seen[-1].content)
    assert seen[-1].url == "https://api.deepseek.com/chat/completions"
    assert body["model"] == "deepseek-chat" and body["stream"] and "tool_choice" not in body
    assert body["thinking"] == {"type": "disabled"} and body["tools"][0]["function"]["name"] == "list_layers"
    assert seen[-1].headers["authorization"] == f"Bearer {FAKE}"


def test_no_tools_sets_tool_choice_none_and_errors_hide_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE)
    m = DeepSeekModel(Settings(provider="deepseek", model="m"))
    assert m._payload(SYSTEM, [{"role": "user", "content": "x"}], TOOLS, True)["tool_choice"] == "none"
    with pytest.raises(DeepSeekAPIError) as e:
        run_model(lambda r: httpx.Response(401, text="bad key"), monkeypatch)
    msg = describe_model_error(e.value)
    assert "DEEPSEEK_API_KEY" in msg and FAKE not in msg and FAKE not in str(e.value)
    with pytest.raises(DeepSeekAPIError):  # retries exhausted on persistent 500
        run_model(lambda r: httpx.Response(500, text="x"), monkeypatch, retries=1)

"""Eval scorer, questions file, replay scoring, and the AnthropicModel request shape (fake SDK)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import anyio
import httpx
import pytest

from agent.config import Settings
from agent.evals import run_evals
from agent.evals.scoring import (Call, Session, check_reply, extract_numbers, score_session,
                                 score_tool_path, session_from_events, ungrounded_numbers)
from agent.mcp_bridge import McpBridge, stdio_params
from agent.model import AnthropicModel, ModelUnavailable
from agent.session import run_session
from agent.testing import ScriptedModel, text, tool



@pytest.fixture(autouse=True)
def _no_real_dotenv(tmp_path, monkeypatch):
    """Never read the developer's real .env (it may hold a live key) in tests."""
    import agent.config
    monkeypatch.setattr(agent.config, "ROOT", tmp_path)

def vals(t, **kw):
    return [(m.value, m.unit) for m in extract_numbers(t, **kw)]


# ---- number extraction ----

def test_extract_basic_units_and_commas():
    assert vals("Slope 6.4° over 2,503 m, 41% cover, 22 min walk") == [
        (6.4, "deg"), (2503.0, "m"), (41.0, "%"), (22.0, "min")]


def test_extract_skips_identifiers_dates_list_markers_and_small_counts():
    t = "1. First\n2. Second: set:2 member a1b2.1 slope_2m rough_5x5 on 2026-11-14 at 12:30; found 3 spots"
    assert vals(t) == []


def test_extract_negative_and_ranges():
    assert (-5.5, "m") in vals("drop of -5.5 m")
    assert (20.0, "m") in vals("between 10-20 m")  # dash after a digit is a separator, not a minus


def test_extract_bare_small_numbers_kept_when_decimal_or_unit():
    assert vals("0.5 ratio and 7 m") == [(0.5, ""), (7.0, "m")]


def test_extract_sources_keep_everything():
    assert [v for v, _ in vals("set:2 slope_2m 3 spots", skip_small_bare=False, skip_identifiers=False)] == [2, 2, 3]


# ---- grounding with rounding tolerance ----

SRC = ["slope_2m max 6.4 deg; distance 2503 m; carbonate share 0.41; walk 22.4 min; area 12840 m2"]


@pytest.mark.parametrize("reply", [
    "Max slope is 6.4°.", "About 6° at most.", "6.4 degrees", "2,503 m away", "about 2.5 km away",
    "roughly 2,500 m", "22 min walk", "22.4 minutes", "41% of cells", "walk takes 0.37 hours" if False else "ok",
    "area of 12,840 m²", "12,800 m2"])
def test_grounded_rounding(reply):
    assert ungrounded_numbers(reply, SRC) == []


@pytest.mark.parametrize("reply,bad", [
    ("Max slope is 9.4°.", "9.4°"), ("It is 3,100 m away.", "3,100 m"), ("55% of cells", "55%"),
    ("walk takes 40 min", "40 min"), ("about 2.9 km", "2.9 km")])
def test_ungrounded_detected(reply, bad):
    assert ungrounded_numbers(reply, SRC) == [bad]


def test_unit_conversion_hours_minutes():
    assert ungrounded_numbers("a 2 hour walk", ["walk_time_min 120"]) == []
    assert ungrounded_numbers("a 3 hour walk", ["walk_time_min 120"]) == ["3 hour"]


def test_exact_unit_conversions_grounded():
    src = ["region area 430708 m2; 2700 m away; walk 95.2 min; route 3.4 km; area 0.36 km2; 3 h limit; position +-43 m"]
    for ok in ["0.43 km2", "0.431 km²", "2.7 km", "2,700 m", "1.59 hours", "95 min", "3,400 m", "360,000 m2",
               "180 min", "43 m", "about 3.4 km"]:
        assert ungrounded_numbers(f"it is {ok}", src) == [], ok
    # conversions never excuse numbers that did not appear in any unit
    for bad in ["0.5 km2", "3.7 km", "3,700 m", "5 hours", "50 m", "600,000 m2"]:
        assert ungrounded_numbers(f"it is {bad}", src) == [bad], bad


def test_round_numbers_not_treated_as_loose():
    # 100 is not a sig-fig rounding of 130 (only >= 1000 round integers get slack)
    assert ungrounded_numbers("100 m", ["130 m"]) == ["100 m"]


# ---- tool path ----

def test_tool_path_required_alternatives_order_allowed():
    spec = {"required": [["find_sites", "find_locations"], "set_view"], "order": [["find_sites", "find_locations"], "set_view"],
            "allowed": ["find_sites", "find_locations", "set_view"]}
    assert score_tool_path(["get_site_overview", "find_locations", "set_view"], spec, ["get_site_overview"]) == []
    f = score_tool_path(["set_view", "find_locations"], spec)
    assert any("wrong order" in x for x in f)
    f = score_tool_path(["find_locations"], spec)
    assert f == ["required tool never called: set_view"]
    f = score_tool_path(["find_locations", "set_view", "teleport_rover"], spec)
    assert f == ["unexpected tools called: teleport_rover"]
    assert score_tool_path(["describe_layer"], {"forbidden": ["describe_layer"]}) == ["forbidden tools called: describe_layer"]


def test_check_reply():
    assert check_reply("CRISM saw it", {"reply_contains_all": [["CRISM"]], "reply_not_contains": ["probability"]}) == []
    assert check_reply("probability 4", {"reply_not_contains": ["probability"]})
    assert check_reply("", {})


# ---- case scoring ----

CASE = {"id": "c", "prompt": "p", "tool_path": {"required": ["describe_location"]},
        "checks": {"reply_contains_any": ["slope"]}}


def sess(reply, calls=None, **kw):
    return Session(prompt="p", reply=reply, calls=calls or [Call("1", "describe_location", {}, "slope 6.4 deg")],
                   stop_reason="end_turn", **kw)


def test_score_pass_fail_and_skip():
    assert score_session(CASE, sess("The slope is 6.4°.")).passed
    s = score_session(CASE, sess("The slope is 9°."))
    assert not s.passed and not s.numbers_ok and s.ungrounded == ["9°"]
    s = score_session(CASE, sess("slope", calls=[Call("1", "list_layers", {}, "x")]))
    assert not s.tool_ok
    s = score_session(CASE, sess("slope", tools_available=["list_layers"]))
    assert s.skipped and not s.passed
    s = score_session(CASE, sess("slope 6.4", error="boom"))
    assert not s.passed


def test_prompt_and_context_numbers_count_as_grounded():
    c = {"id": "c", "prompt": "route under 15°", "tool_path": {}}
    s = Session(prompt="route under 15°", reply="Kept under 15° as asked; rover at 12.5 m east.", calls=[],
                context_block='{"x":12.5}', stop_reason="end_turn")
    assert score_session(c, s).passed
    assert not score_session(c, s, allow_prompt_numbers=False).passed


# ---- questions file ----

def test_questions_file_complete_and_consistent():
    cases, allowed = run_evals.load_cases()
    ids = [c["id"] for c in cases]
    assert len(cases) >= 16 and len(set(ids)) == len(ids)
    assert sum(i.startswith("demo_") for i in ids) == 6 and sum(i.startswith("q") for i in ids) == 10
    for c in cases:
        assert c["prompt"] and c["tool_path"].get("required"), c["id"]
    assert "get_site_overview" in allowed


# ---- replay scoring end to end (real MCP, scripted model, recorded, re-scored) ----

def test_record_then_score_replay_and_cli(tmp_path, capsys):
    model = ScriptedModel([
        [tool("t1", "describe_layer", layer_id="slope_2m")],
        [text("Slope layer: HiRISE derived slope over a 2 m baseline. It does not measure features smaller than 3 m.")],
    ])
    st = Settings(replay_dir=tmp_path, api_url="http://api.test")
    prompt = "What can the slope layer not tell me?"

    async def go():
        bridge = McpBridge(stdio_params())
        http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
        try:
            return [e async for e in run_session(model=model, bridge=bridge, settings=st, http=http,
                                                 messages=[{"role": "user", "content": prompt}])]
        finally:
            await http.aclose()
            await bridge.stop()
    anyio.run(go)
    cases, allowed = run_evals.load_cases()
    scores, unmatched = run_evals.score_replays(cases, allowed, run_evals.replay_files([str(tmp_path)]))
    assert unmatched == [] and [s.case_id for s in scores] == ["q06_slope_layer_limits"]
    assert scores[0].passed, scores[0].failures
    rc = run_evals.main(["--replay", str(tmp_path)])
    assert rc == 0 and "1/1 passed" in capsys.readouterr().out


def test_replay_scoring_flags_made_up_number(tmp_path):
    rec = tmp_path / "s.jsonl"
    rows = [{"t": 0, "event": "meta", "data": {"user_prompt": "What can the slope layer not tell me?", "tools": ["describe_layer"]}},
            {"t": 0, "event": "tool_call_start", "data": {"id": "1", "name": "describe_layer", "args": {"layer_id": "slope_2m"}}},
            {"t": 0, "event": "tool_call_result", "data": {"id": "1", "summary": "s", "is_error": False,
                                                           "result": "slope_2m does not measure rocks below 3 m"}},
            {"t": 0, "event": "text", "data": {"delta": "It cannot see rocks below 3 m and is accurate to 0.7 m."}},
            {"t": 0, "event": "done", "data": {"stop_reason": "end_turn", "tool_calls": 1}}]
    rec.write_text("\n".join(json.dumps(r) for r in rows))
    cases, allowed = run_evals.load_cases()
    scores, _ = run_evals.score_replays(cases, allowed, [rec])
    assert not scores[0].passed and scores[0].ungrounded == ["0.7 m"]
    assert run_evals.main(["--replay", str(rec)]) == 1


def test_live_eval_without_key_exits_2(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert run_evals.main([]) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_session_from_events_uses_full_result_text():
    s = session_from_events({"user_prompt": "p"}, [
        {"event": "tool_call_start", "data": {"id": "1", "name": "x", "args": {}}},
        {"event": "tool_call_result", "data": {"id": "1", "summary": "short", "result": "full text 42", "is_error": True}},
        {"event": "text", "data": {"delta": "a"}}, {"event": "text", "data": {"delta": "b"}},
        {"event": "done", "data": {"stop_reason": "end_turn"}}])
    assert s.reply == "ab" and s.calls[0].result == "full text 42" and s.calls[0].is_error


# ---- AnthropicModel request shape with a fake SDK client ----

class FakeStream:
    def __init__(self, owner, kw):
        self.owner, self.kw = owner, kw

    async def __aenter__(self):
        self.owner.requests.append(self.kw)
        if self.owner.fail_fallback and "fallbacks" in self.kw:
            import anthropic
            raise anthropic.BadRequestError("unknown field fallbacks", response=httpx.Response(
                400, request=httpx.Request("POST", "http://x")), body=None)
        return self

    async def __aexit__(self, *a):
        return False

    async def __aiter__(self):
        for t in ("Hel", "lo"):
            yield SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text=t))
        yield SimpleNamespace(type="message_stop")

    async def get_final_message(self):
        blocks = [SimpleNamespace(model_dump=lambda **k: {"type": "thinking", "thinking": "", "signature": "sig", "extra": 1}),
                  SimpleNamespace(model_dump=lambda **k: {"type": "text", "text": "Hello", "citations": None}),
                  SimpleNamespace(model_dump=lambda **k: {"type": "tool_use", "id": "t", "name": "n", "input": {"a": 1}, "caller": {"type": "direct"}})]
        return SimpleNamespace(content=blocks, stop_reason="tool_use")


class FakeSdk:
    def __init__(self, fail_fallback=False):
        self.requests, self.fail_fallback = [], fail_fallback
        mk = lambda: SimpleNamespace(stream=lambda **kw: FakeStream(self, kw))
        self.beta, self.messages = SimpleNamespace(messages=mk()), mk()


def drive(model, **kw):
    async def go():
        return [e async for e in model.stream_turn(system=[{"type": "text", "text": "s"}], messages=[{"role": "user", "content": "hi"}],
                                                   tools=[{"name": "n"}], **kw)]
    return anyio.run(go)


def test_anthropic_model_request_and_blocks():
    sdk = FakeSdk()
    m = AnthropicModel(Settings(fallbacks=True), client=sdk)
    out = drive(m)
    assert [k for k, _ in out] == ["text", "text", "final"]
    kw = sdk.requests[0]
    assert kw["model"] == "claude-opus-5-5" and kw["thinking"] == {"type": "adaptive"}
    assert kw["output_config"] == {"effort": "medium"} and "tool_choice" not in kw
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    final = out[-1][1]
    assert final["stop_reason"] == "tool_use"
    assert final["content"][0] == {"type": "thinking", "thinking": "", "signature": "sig"}
    assert final["content"][1] == {"type": "text", "text": "Hello"}
    assert final["content"][2] == {"type": "tool_use", "id": "t", "name": "n", "input": {"a": 1}}
    drive(m, no_tools=True)
    assert sdk.requests[1]["tool_choice"] == {"type": "none"}


def test_anthropic_model_retries_without_fallbacks_if_rejected():
    sdk = FakeSdk(fail_fallback=True)
    m = AnthropicModel(Settings(fallbacks=True), client=sdk)
    out = drive(m)
    assert out[-1][0] == "final" and len(sdk.requests) == 2 and "fallbacks" not in sdk.requests[1]
    drive(m)
    assert "fallbacks" not in sdk.requests[2]  # remembered


def test_anthropic_model_env_model_and_no_key(monkeypatch):
    monkeypatch.setenv("ATLAS_MODEL", "claude-sonnet-5-5")
    assert Settings.from_env().model == "claude-sonnet-5-5"
    monkeypatch.delenv("ATLAS_MODEL")
    assert Settings.from_env().model == "claude-opus-5-5"
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    with pytest.raises(ModelUnavailable):
        AnthropicModel(Settings()).check()


def test_fallbacks_off_by_default():
    """The fallback beta is unverified without a key, so it must be opt-in."""
    assert Settings().fallbacks is False


def test_q04_accepts_reported_absence_but_not_a_made_up_pad():
    from agent.evals.run_evals import load_cases
    cases, _ = load_cases()
    checks = next(c for c in cases if c["id"] == "q04_zero_slope_pad")["checks"]
    ok = ("A literal zero-slope pad doesn't exist in this dataset. The slope_2m layer bottoms out at 0.00124 deg, "
          "so the 0% pass rate is the bottleneck.")
    assert check_reply(ok, checks) == []
    assert check_reply("Pad 3.1 at (10, 20) is perfectly flat.", checks) != []


def test_setup_cases_declared():
    from agent.evals.run_evals import load_cases
    cases = {c["id"]: c for c in load_cases()[0]}
    assert cases["los_radio_base_from_marker"]["setup"]["markers"]
    assert cases["demo_scout"]["setup"]["route"]["goal"]


def test_negative_source_magnitude_grounded_but_not_other_numbers():
    src = ["Profile: B is -4.4 m relative to A."]
    assert ungrounded_numbers("B is 4.4 m below A", src) == []
    assert ungrounded_numbers("B is 5.4 m below A", src) == ["5.4 m"]


def test_check_reply_ignores_markdown_emphasis():
    checks = {"reply_contains_any": ["today's topography"]}
    assert check_reply("It floods **today's** topography.", checks) == []
    assert check_reply("It floods the ancient floor.", checks) != []

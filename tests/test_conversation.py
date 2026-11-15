"""Server-side conversation memory: store, sanitize, trim, entities, and the /chat session flow (ScriptedModel + real MCP)."""
from __future__ import annotations

import time

from agent.app import create_app
from agent.context import render_context_block
from agent.conversation import (ConversationStore, render_entities, sanitize, track_entities, trim, turns_of)
from agent.loop import normalize_history
from agent.testing import ScriptedModel, text, tool
from tests.test_agent_loop import parse_sse, post_chat, run, settings


def u(t): return {"role": "user", "content": t}
def a(*blocks): return {"role": "assistant", "content": list(blocks)}
def tr(i, t, err=False): return {"role": "user", "content": [{"type": "tool_result", "tool_use_id": i, "content": t, **({"is_error": True} if err else {})}]}


def ids_ok(msgs):
    """Every tool_use answered by the next message; every tool_result answers the previous message; roles alternate."""
    for i, m in enumerate(msgs):
        if i and msgs[i - 1]["role"] == m["role"]:
            return False
        bl = m["content"] if isinstance(m["content"], list) else []
        uses = {b["id"] for b in bl if b.get("type") == "tool_use"}
        if uses:
            nxt = msgs[i + 1]["content"] if i + 1 < len(msgs) else []
            if not uses <= {b.get("tool_use_id") for b in nxt if isinstance(b, dict) and b.get("type") == "tool_result"}:
                return False
        for b in bl:
            if b.get("type") == "tool_result":
                prev = msgs[i - 1]["content"] if i else []
                if b["tool_use_id"] not in {x.get("id") for x in prev if isinstance(x, dict)}:
                    return False
    return True


def test_store_lru_ttl_and_generated_id():
    s = ConversationStore(max_sessions=2, ttl_s=100)
    c1, ex = s.get(None)
    assert c1.sid and not ex
    assert s.get(c1.sid)[1] is True
    s.get("b"); s.get("c")
    assert len(s) == 2 and s.get("b")[1] and not s.get(c1.sid)[1]  # oldest evicted
    s2 = ConversationStore(ttl_s=0.01)
    s2.get("x"); time.sleep(0.03)
    assert s2.get("x")[1] is False
    assert s.get("bad id!/")[0].sid != "bad id!/"


def test_sanitize_repairs_dangling_pairs():
    msgs = [u("q"), a(text("ok"), tool("t1", "x")), tr("t1", "r"), a(text("done")),
            u("q2"), a(tool("t2", "x"))]  # aborted: tool_use never answered
    out = sanitize(msgs)
    assert ids_ok(out) and out[0]["role"] == "user"
    assert not any(b.get("type") == "tool_use" and b["id"] == "t2" for m in out for b in (m["content"] if isinstance(m["content"], list) else []))
    out2 = sanitize(out + [u("q3")])
    assert ids_ok(out2) and out2[-1]["role"] == "user"
    assert sanitize([tr("zzz", "orphan"), u("hi")])[0] == u("hi")


def test_sanitize_merges_consecutive_user_texts():
    assert sanitize([u("a"), u("b")]) == [{"role": "user", "content": "a\n\nb"}]


def test_trim_keeps_pairs_shortens_old_results_and_digests_dropped_turns():
    msgs = []
    for i in range(12):
        msgs += [u(f"question {i}"), a(tool(f"t{i}", "find_regions")), tr(f"t{i}", "R" * 20000), a(text(f"answer {i}"))]
    out, dropped = trim(msgs, budget=3000)
    assert ids_ok(out) and out[0]["role"] == "user" and out[0]["content"].startswith("question")
    assert out[-1]["content"][0]["text"] == "answer 11" and any("question 0" in d for d in dropped)
    last2 = turns_of(out)[-2:]
    assert all(len(b["content"]) == 20000 for t in last2 for m in t for b in (m["content"] if isinstance(m["content"], list) else []) if b.get("type") == "tool_result")
    assert len(turns_of(out)) >= 2


def test_entities_track_set_route_place_and_render():
    ent = {}
    msgs = [u("find pads"), a(tool("t1", "find_locations", profile="landing_pad")),
            tr("t1", "Found 149 spots; stored as set:3.\n1. 3.1 (2393, -81) m, score 1.15\n2. 3.2 (699, 31) m, score 1.16\nHint: x"),
            a(tool("t2", "plan_route", goal="3.2")), tr("t2", "Route route-2 (eva_walkable): 2698 m, 59.2 min"),
            a(tool("t3", "teleport_rover", target="the delta")), tr("t3", "ARRIVED - teleported to Delta Front"), a(text("ok"))]
    track_entities(ent, msgs)
    r = render_entities(ent, ["old question"])
    assert "set:3" in r and "3.2 (699, 31)" in r and "route-2" in r and "Delta Front" in r and "old question" in r
    assert "conversation memory" in render_context_block({"rover": None, "markers": None}, r)
    assert "conversation memory" not in render_context_block({"rover": None, "markers": None})


def test_normalize_history_text_turns():
    h = normalize_history([u("a"), {"role": "assistant", "content": "b"}, u("c")])
    assert [m["role"] for m in h] == ["user", "assistant"]


def test_session_followup_sees_prior_tool_results_and_stateless_still_works(tmp_path):
    model = ScriptedModel([[tool("t1", "list_layers")], [text("Here are the layers.")], [text("The first one.")],
                           [text("stateless reply")]])
    st = settings(tmp_path)

    async def go(bridge):
        app = create_app(model=model, bridge=bridge, settings=st)
        r1 = await post_chat(app, {"message": "what layers?", "session_id": "sess1"})
        r2 = await post_chat(app, {"message": "tell me about the first", "session_id": "sess1"})
        r3 = await post_chat(app, {"messages": [{"role": "user", "content": "legacy"}]})
        return r1, r2, r3
    r1, r2, r3 = run(go)
    assert parse_sse(r1.text)[0][0] == "session" and parse_sse(r2.text)[0][1]["resumed"] is True
    third_call = model.calls[2]["messages"]  # second turn: previous turn's tool_use/tool_result are present
    assert ids_ok(third_call) and any(isinstance(m["content"], list) and any(b.get("type") == "tool_result" for b in m["content"]) for m in third_call)
    assert third_call[-1]["content"] == "tell me about the first"
    assert len(model.calls[3]["messages"]) == 1  # legacy path is stateless
    assert "conversation memory" in model.calls[2]["system"][1]["text"]
    assert parse_sse(r3.text)[-1][0] == "done"


def test_unknown_session_is_seeded_from_history_and_new_sessions_are_isolated(tmp_path):
    model = ScriptedModel([[text("a")], [text("b")]])

    async def go(bridge):
        app = create_app(model=model, bridge=bridge, settings=settings(tmp_path))
        await post_chat(app, {"message": "and then?", "session_id": "fresh1",
                              "history": [u("hello"), {"role": "assistant", "content": "hi"}]})
        await post_chat(app, {"message": "x", "session_id": "fresh2"})
    run(go)
    assert [m["role"] for m in model.calls[0]["messages"]] == ["user", "assistant", "user"]
    assert len(model.calls[1]["messages"]) == 1


def test_failed_turn_does_not_poison_session(tmp_path):
    class Flaky(ScriptedModel):
        n = 0

        async def stream_turn(self, **kw):
            Flaky.n += 1
            if Flaky.n == 1:
                raise RuntimeError("boom")
            async for x in super().stream_turn(**kw):
                yield x
    model = Flaky([[text("fine")]])

    async def go(bridge):
        app = create_app(model=model, bridge=bridge, settings=settings(tmp_path))
        r1 = await post_chat(app, {"message": "first", "session_id": "p1"})
        r2 = await post_chat(app, {"message": "second", "session_id": "p1"})
        return r1, r2
    r1, r2 = run(go)
    assert parse_sse(r1.text)[-1][0] == "error" and parse_sse(r2.text)[-1][0] == "done"
    assert model.calls[0]["messages"] == [u("second")]  # the failed turn left nothing behind


def test_conversation_eval_checks():
    from agent.evals.scoring import Call, Session, check_conversation_turn, first_sentence, prior_item_handles
    listing = "Found 3 spots; set:3.\n1. 3.1 (2393, -81) m, score 1\n2. 3.2 (699, 31) m, score 2\n"
    prior = [Session("find", "Three.", [Call("a", "find_locations", {}, listing)])]
    ok = Session("2nd", "Going to the second pad.", [Call("b", "teleport_rover", {"target": "3.2"}, "ARRIVED")])
    bad = Session("2nd", "I'll check.", [Call("b", "teleport_rover", {"target": "3.1"}, "ARRIVED")])
    chk = {"args_match_prior_item": {"turn": 0, "index": 2}, "answer_first": True}
    assert check_conversation_turn(chk, ok, prior) == []
    assert len(check_conversation_turn(chk, bad, prior)) == 2
    assert prior_item_handles([listing], 2) == ["3.2", "699, 31"]
    assert first_sentence("About 2.9 km. Then more.") == "About 2.9 km."
    recall = Session("again", "It was 2393 m.", [])
    assert check_conversation_turn({"recall_from_turn": 0}, recall, prior) == []
    assert check_conversation_turn({"recall_from_turn": 0}, Session("x", "It was 999 m.", []), prior)


def test_conversation_yaml_loads():
    from agent.evals.run_evals import load_conversations
    convs, _ = load_conversations()
    assert len(convs) >= 15 and all(2 <= len(c["turns"]) <= 4 for c in convs)

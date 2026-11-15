"""Run or re-score agent evals.

  python -m agent.evals.run_evals                      live: every case through the real loop (needs a key)
  python -m agent.evals.run_evals --only demo_scout    subset
  python -m agent.evals.run_evals --replay agent/replays   score recorded sessions, no key needed
  python -m agent.evals.run_evals --conversations          live multi-turn conversations (conversation_questions.yaml)

Exit code: 0 all scored cases pass, 1 a case failed, 2 cannot run (no key / no sessions / bad args).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

import yaml

from ..config import KEY_VARS, Settings, has_key, load_dotenv
from ..replay import load_replay
from .scoring import Score, Session, check_conversation_turn, score_session, session_from_events

QUESTIONS = Path(__file__).resolve().parent / "questions.yaml"
CONVERSATIONS = Path(__file__).resolve().parent / "conversation_questions.yaml"


def load_cases(path: Path = QUESTIONS) -> tuple[list[dict], list[str]]:
    """Load agent/evals/questions.yaml: (cases, tools always allowed). Raises ValueError on duplicate case ids."""
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = doc["cases"]
    ids = [c["id"] for c in cases]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate case ids in " + str(path))
    return cases, doc.get("always_allowed", [])


def norm(s: str) -> str:
    """Lower-case and collapse whitespace, for matching prompts to recorded sessions."""
    return re.sub(r"\s+", " ", s.strip().lower())


def replay_files(targets: list[str]) -> list[Path]:
    """Expand files and directories (recursively, *.jsonl) into a sorted list of recorded sessions."""
    files: list[Path] = []
    for t in targets:
        p = Path(t)
        files += sorted(p.rglob("*.jsonl")) if p.is_dir() else [p]
    return files


def score_replays(cases: list[dict], always_allowed: list[str], files: list[Path], strict: bool = False
                  ) -> tuple[list[Score], list[str]]:
    """Match each recorded session to a case by identical user prompt; the latest recording per case wins."""
    by_prompt = {norm(c["prompt"]): c for c in cases}
    latest: dict[str, tuple[Path, Session]] = {}
    unmatched: list[str] = []
    for f in files:
        try:
            meta, events = load_replay(f)
        except (ValueError, OSError) as e:
            unmatched.append(f"{f}: unreadable ({e})")
            continue
        case = by_prompt.get(norm(meta.get("user_prompt", "")))
        if case is None:
            unmatched.append(f"{f}: prompt matches no case")
            continue
        latest[case["id"]] = (f, session_from_events(meta, events))
    scores = [score_session(next(c for c in cases if c["id"] == cid), s, always_allowed)
              for cid, (_, s) in latest.items()]
    return scores, unmatched


async def _rest(settings: Settings, method: str, path: str, body: dict | None = None):
    import httpx
    base = (settings.rover_api_url or settings.api_url).rstrip("/")
    async with httpx.AsyncClient(timeout=10) as http:
        r = await http.request(method, base + path, json=body)
        r.raise_for_status()
        return r.json()


async def apply_setup(settings: Settings, setup: dict | None) -> None:
    """Case `setup`: {markers: [{x, y, label}], route: {start, goal, profile}} created through the REST API before the
    case, for prompts that presuppose app state ("my marker", "that route")."""
    for m in (setup or {}).get("markers", []):
        await _rest(settings, "POST", "/markers", {"created_by": "user", **m})
    if (setup or {}).get("route"):
        await _rest(settings, "POST", "/route", setup["route"])
    if (setup or {}).get("mission") is not None:
        await apply_mission_setup(settings, setup["mission"])


async def apply_mission_setup(settings: Settings, m: dict) -> None:
    """Mid-EVA setup (agent/evals/mission_questions.yaml): `mission: {rover_at, crew, eva, ltst, advance_min, markers_after}`.
    Resets (ends any EVA, markers gone, crew and rover back at the base), parks the rover at `rover_at`, then starts an EVA
    with the crew at `crew` (kwargs of POST /mission/eva/start in `eva`; `eva: false` = no EVA running). `ltst` ("HH:MM")
    sets the EVA start on the simulated mission clock to that local true solar time on sol 1788; `advance_min` moves the clock on."""
    try:
        await _rest(settings, "POST", "/mission/eva/end", {})
    except Exception:  # noqa: BLE001  (no EVA running)
        pass
    await _rest(settings, "POST", "/rover/command", {"cmd": "teleport", "target": "base"})
    if m.get("rover_at"):
        await _rest(settings, "POST", "/rover/command", {"cmd": "teleport", **m["rover_at"]})
    eva = m.get("eva", {})
    body = dict(eva or {})
    if m.get("crew"):
        body["crew_at"] = m["crew"]
    if m.get("ltst") and "now" not in body:
        from atlas_core import illumination as I
        body["now"] = I.resolve_time(sol=int(m.get("sol", 1788)), ltst=m["ltst"]).isoformat().replace("+00:00", "Z")
    if eva is False:
        if m.get("crew"):
            await _rest(settings, "POST", "/rover/command", {"cmd": "teleport", **m["crew"]})
        return
    await _rest(settings, "POST", "/mission/eva/start", body)
    if m.get("advance_min"):
        await _rest(settings, "POST", "/mission/advance", {"minutes": m["advance_min"]})


async def teardown_setup(settings: Settings, setup: dict | None) -> None:
    """Remove the markers a live eval case created through its `setup`, so cases do not leak state into each other."""
    if (setup or {}).get("markers") or (setup or {}).get("mission") is not None:
        for m in (await _rest(settings, "GET", "/markers")).get("markers", []):
            await _rest(settings, "DELETE", f"/markers/{m['id']}")
    if (setup or {}).get("mission") is not None:
        try:
            await _rest(settings, "POST", "/mission/eva/end", {})
        except Exception:  # noqa: BLE001
            pass
        await _rest(settings, "POST", "/rover/command", {"cmd": "teleport", "target": "base"})


async def run_live(cases: list[dict], always_allowed: list[str], settings: Settings) -> list[Score]:
    """Run every case through the real loop (model + MCP bridge + REST) and score each session. Needs an API key."""
    from ..mcp_bridge import McpBridge
    from ..model import make_model
    from ..session import run_session
    model = make_model(settings)
    model.check()
    bridge = McpBridge(settings=settings)
    scores: list[Score] = []
    out_dir = settings.replay_dir / "evals"  # recorded so they can be re-scored with --replay
    try:
        for c in cases:
            events: list[dict] = []
            info: dict = {}
            await apply_setup(settings, c.get("setup"))
            async for ev in run_session(model=model, bridge=bridge, settings=settings,
                                        messages=[{"role": "user", "content": c["prompt"]}],
                                        client_context=c.get("client_context"), record_dir=out_dir,
                                        record=True, info=info):
                events.append(ev)
            await teardown_setup(settings, c.get("setup"))
            meta = info or {"user_prompt": c["prompt"], "tools": bridge.tool_names}
            scores.append(score_session(c, session_from_events(meta, events), always_allowed))
            print_row(scores[-1], file=sys.stderr)
    finally:
        await bridge.stop()
    return scores


def load_conversations(path: Path = CONVERSATIONS) -> tuple[list[dict], list[str]]:
    """Load conversation_questions.yaml: (conversations, tools always allowed). Each conversation has `turns`, each with
    `say`, optional `tool_path` and `checks` (see scoring.check_conversation_turn)."""
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    convs = doc["conversations"]
    ids = [c["id"] for c in convs]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate conversation ids in " + str(path))
    return convs, doc.get("always_allowed", [])


async def run_conversation(conv: dict, always_allowed: list[str], settings: Settings, model, bridge, out_dir: Path,
                           baseline: bool = False) -> list[Score]:
    """Play one conversation turn by turn through the same server-side memory path the HTTP app uses; one Score per turn."""
    from ..conversation import Conversation
    from ..session import run_session
    memory = Conversation(conv["id"])
    text_turns: list[dict] = []  # baseline: what the old panel sent (earlier turns as plain text, no tool results)
    prior: list[Session] = []
    scores: list[Score] = []
    await apply_setup(settings, conv.get("setup"))
    try:
        for i, turn in enumerate(conv["turns"]):
            events: list[dict] = []
            info: dict = {}
            async for ev in run_session(model=model, bridge=bridge, settings=settings,
                                        messages=text_turns + [{"role": "user", "content": turn["say"]}] if baseline
                                        else [{"role": "user", "content": turn["say"]}],
                                        client_context=conv.get("client_context"), record_dir=out_dir / conv["id"],
                                        record=True, info=info, conversation=None if baseline else memory):
                events.append(ev)
            meta = dict(info or {"user_prompt": turn["say"], "tools": bridge.tool_names})
            meta["user_prompt"] = turn["say"]
            sess = session_from_events(meta, events)
            if baseline:
                text_turns += [{"role": "user", "content": turn["say"]}, {"role": "assistant", "content": sess.reply or "(no reply)"}]
            sess.extra_sources = [c.result or "" for p in prior for c in p.calls]
            case = {"id": f"{conv['id']}#{i + 1}", "tool_path": turn.get("tool_path", {}), "checks": turn.get("checks", {})}
            sc = score_session(case, sess, always_allowed)
            if not sc.skipped:
                extra = check_conversation_turn(case["checks"], sess, prior)
                sc.checks_ok = sc.checks_ok and not extra
                sc.failures += extra
                sc.passed = sc.passed and not extra
            sc.failures = [f"[{turn['say'][:40]}] {f}" for f in sc.failures]
            sc.reply = sess.reply
            scores.append(sc)
            prior.append(sess)
            print_row(sc, file=sys.stderr)
    finally:
        await teardown_setup(settings, conv.get("setup"))
    return scores


async def run_live_conversations(convs: list[dict], always_allowed: list[str], settings: Settings,
                                 baseline: bool = False, out_dir: Path | None = None) -> list[Score]:
    """Run every conversation live (model + MCP bridge + REST); recordings go to <replay_dir>/conversation/<id>/."""
    from ..mcp_bridge import McpBridge
    from ..model import make_model
    model = make_model(settings)
    model.check()
    bridge = McpBridge(settings=settings)
    scores: list[Score] = []
    try:
        for c in convs:
            scores += await run_conversation(c, always_allowed, settings, model, bridge,
                                             out_dir or settings.replay_dir / "conversation", baseline)
    finally:
        await bridge.stop()
    return scores


def report_conversations(scores: list[Score]) -> int:
    """CLI output: per-turn rows, then one PASS/FAIL line per conversation (all turns must pass); exit code 1 on any failure."""
    by: dict[str, list[Score]] = {}
    for s in scores:
        by.setdefault(s.case_id.split("#")[0], []).append(s)
    ok = 0
    for cid, ts in by.items():
        good = all(t.passed or t.skipped for t in ts)
        ok += good
        print(f"{'PASS' if good else 'FAIL'}  {cid}  turns: " + " ".join("ok" if t.passed else "BAD" for t in ts))
        for t in ts:
            for f in t.failures:
                if not t.passed:
                    print(f"        - turn {t.case_id.split('#')[1]}: {f}")
            for w in t.warnings:
                print(f"        ~ turn {t.case_id.split('#')[1]}: {w}")
    print(f"\n{ok}/{len(by)} conversations passed ({sum(t.passed for t in scores)}/{len(scores)} turns)")
    return 0 if ok == len(by) else 1


def status(s: Score) -> str:
    """"SKIP", "PASS" or "FAIL" for a Score."""
    return "SKIP" if s.skipped else ("PASS" if s.passed else "FAIL")


def print_row(s: Score, file=sys.stdout) -> None:
    """CLI output: one result line per case."""
    print(f"{status(s):4}  {s.case_id:34} tools={'ok' if s.tool_ok else 'BAD'} numbers={'ok' if s.numbers_ok else 'BAD'} "
          f"checks={'ok' if s.checks_ok else 'BAD'}  calls={s.n_tool_calls}", file=file)


def report(scores: list[Score], unmatched: list[str], strict: bool) -> int:
    """CLI output: per-case rows, failure details, unmatched sessions and totals; returns the process exit code (1 if any case failed, or any skipped under `strict`)."""
    for s in scores:
        print_row(s)
        for f in s.failures:
            print(f"        - {f}")
        for w in s.warnings:
            print(f"        ~ {w}")
    for u in unmatched:
        print(f"UNMATCHED  {u}")
    ran = [s for s in scores if not s.skipped]
    skipped = [s for s in scores if s.skipped]
    failed = [s for s in ran if not s.passed]
    print(f"\n{len(ran) - len(failed)}/{len(ran)} passed, {len(skipped)} skipped, {len(failed)} failed")
    if strict and skipped:
        return 1
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; exit code 0 all pass, 1 a case failed, 2 cannot run (no key, no sessions, bad args)."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--questions", type=Path, default=QUESTIONS)
    ap.add_argument("--only", nargs="*", help="case ids to run/score")
    ap.add_argument("--replay", nargs="+", metavar="PATH", help="score recorded sessions (files or dirs); no key needed")
    ap.add_argument("--conversations", action="store_true", help="run the multi-turn conversations live (conversation_questions.yaml)")
    ap.add_argument("--baseline", action="store_true", help="with --conversations: old behaviour (client re-sends text-only history, no server memory)")
    ap.add_argument("--out", type=Path, help="with --conversations: recording directory")
    ap.add_argument("--strict", action="store_true", help="treat skipped cases (missing tools) as failures")
    ap.add_argument("--json", type=Path, help="also write scores to this file")
    args = ap.parse_args(argv)

    if args.conversations:
        convs, always_allowed = load_conversations(CONVERSATIONS if args.questions == QUESTIONS else args.questions)
        if args.only:
            convs = [c for c in convs if c["id"] in args.only]
        load_dotenv()
        settings = Settings.from_env()
        if not has_key(settings.provider):
            print(f"Provider {settings.provider}: {KEY_VARS[settings.provider]} is not set; conversations need a live model.",
                  file=sys.stderr)
            return 2
        print(f"live conversations: provider={settings.provider} model={settings.model}", file=sys.stderr)
        scores = asyncio.run(run_live_conversations(convs, always_allowed, settings, args.baseline, args.out))
        if args.json:
            args.json.write_text(json.dumps({"provider": settings.provider, "model": settings.model,
                                             "scores": [s.__dict__ for s in scores]}, indent=2, default=str))
        return report_conversations(scores)

    cases, always_allowed = load_cases(args.questions)
    if args.only:
        unknown = set(args.only) - {c["id"] for c in cases}
        if unknown:
            print(f"unknown case ids: {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2
        cases = [c for c in cases if c["id"] in args.only]

    unmatched: list[str] = []
    settings = None
    if args.replay:
        scores, unmatched = score_replays(cases, always_allowed, replay_files(args.replay), args.strict)
        if not scores:
            print("No recorded session matched any case. " + "; ".join(unmatched), file=sys.stderr)
            return 2
    else:
        load_dotenv()
        settings = Settings.from_env()
        if not has_key(settings.provider):
            print(f"Provider {settings.provider}: {KEY_VARS[settings.provider]} is not set, so the live eval cannot run.\n"
                  "Set it, or score recorded sessions without a key:\n"
                  "  .venv/bin/python -m agent.evals.run_evals --replay agent/replays", file=sys.stderr)
            return 2
        print(f"live eval: provider={settings.provider} model={settings.model}", file=sys.stderr)
        scores = asyncio.run(run_live(cases, always_allowed, settings))
    if args.json:
        args.json.write_text(json.dumps({"provider": None if args.replay else settings.provider,
                                         "model": None if args.replay else settings.model,
                                         "scores": [s.__dict__ for s in scores]}, indent=2, default=str))
    return report(scores, unmatched, args.strict)


if __name__ == "__main__":
    sys.exit(main())

"""Run or re-score agent evals.

  python -m agent.evals.run_evals                      live: every case through the real loop (needs a key)
  python -m agent.evals.run_evals --only demo_scout    subset
  python -m agent.evals.run_evals --replay agent/replays   score recorded sessions, no key needed

Exit code: 0 all scored cases pass, 1 a case failed, 2 cannot run (no key / no sessions / bad args).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

import yaml

from ..config import KEY_VARS, ROOT, Settings, has_key, load_dotenv
from ..replay import load_replay
from .scoring import Score, Session, score_session, session_from_events

QUESTIONS = Path(__file__).resolve().parent / "questions.yaml"


def load_cases(path: Path = QUESTIONS) -> tuple[list[dict], list[str]]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = doc["cases"]
    ids = [c["id"] for c in cases]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate case ids in " + str(path))
    return cases, doc.get("always_allowed", [])


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def replay_files(targets: list[str]) -> list[Path]:
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


async def teardown_setup(settings: Settings, setup: dict | None) -> None:
    if (setup or {}).get("markers"):
        for m in (await _rest(settings, "GET", "/markers")).get("markers", []):
            await _rest(settings, "DELETE", f"/markers/{m['id']}")


async def run_live(cases: list[dict], always_allowed: list[str], settings: Settings) -> list[Score]:
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


def status(s: Score) -> str:
    return "SKIP" if s.skipped else ("PASS" if s.passed else "FAIL")


def print_row(s: Score, file=sys.stdout) -> None:
    print(f"{status(s):4}  {s.case_id:34} tools={'ok' if s.tool_ok else 'BAD'} numbers={'ok' if s.numbers_ok else 'BAD'} "
          f"checks={'ok' if s.checks_ok else 'BAD'}  calls={s.n_tool_calls}", file=file)


def report(scores: list[Score], unmatched: list[str], strict: bool) -> int:
    for s in scores:
        print_row(s)
        for f in s.failures:
            print(f"        - {f}")
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
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--questions", type=Path, default=QUESTIONS)
    ap.add_argument("--only", nargs="*", help="case ids to run/score")
    ap.add_argument("--replay", nargs="+", metavar="PATH", help="score recorded sessions (files or dirs); no key needed")
    ap.add_argument("--strict", action="store_true", help="treat skipped cases (missing tools) as failures")
    ap.add_argument("--json", type=Path, help="also write scores to this file")
    args = ap.parse_args(argv)

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

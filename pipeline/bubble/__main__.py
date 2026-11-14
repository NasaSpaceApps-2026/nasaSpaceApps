from __future__ import annotations

from pathlib import Path
import argparse

from bubble.fixture import make_fixture
from bubble.scripted import apply_judge, run_scripted
from bubble.tick import run_tick


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="bubble")
    sub = parser.add_subparsers(dest="cmd", required=True)

    fixture = sub.add_parser("fixture")
    fixture.add_argument("--out", type=Path, required=True)

    tick = sub.add_parser("tick")
    tick.add_argument("--bundle", type=Path, required=True)
    tick.add_argument("--work", type=Path, required=True)
    tick.add_argument("--operator", required=True)
    tick.add_argument("--value", required=True)

    scripted = sub.add_parser("scripted")
    scripted.add_argument("--bundle", type=Path, required=True)
    scripted.add_argument("--work", type=Path, required=True)

    judge = sub.add_parser("judge")
    judge.add_argument("--work", type=Path, required=True)
    judge.add_argument("--verdict", required=True, choices=["accept", "reject"])

    args = parser.parse_args(argv)
    if args.cmd == "fixture":
        make_fixture(args.out)
    elif args.cmd == "tick":
        run_tick(args.bundle, args.work, args.operator, args.value)
    elif args.cmd == "scripted":
        run_scripted(args.bundle, args.work)
    else:
        apply_judge(args.work, args.verdict)


if __name__ == "__main__":
    main()

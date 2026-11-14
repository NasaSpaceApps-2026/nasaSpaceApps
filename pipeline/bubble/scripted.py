from __future__ import annotations

from pathlib import Path
import json
import shutil

from bubble.bundle import load_bundle
from bubble.errors import BubbleError
from bubble.tick import ensure_initialized, run_tick


def assert_separated(bundle_dir: Path, work_dir: Path) -> None:
    bundle_dir = bundle_dir.resolve()
    work_dir = work_dir.resolve()
    if bundle_dir == work_dir or bundle_dir in work_dir.parents or work_dir in bundle_dir.parents:
        raise BubbleError("bundle and work directories must be separate")


def run_scripted(bundle_dir: Path, work_dir: Path) -> dict:
    assert_separated(bundle_dir, work_dir)
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)
    bundle = load_bundle(bundle_dir)
    metrics = ensure_initialized(bundle, work_dir)
    if not bundle.poses_trusted:
        with (work_dir / "LOOP_STATE.md").open("a") as handle:
            handle.write("## refusing to project\nposes_trusted is false. Shipping the ortho drape.\n")
        return metrics
    fit = [cam.name for cam in bundle.cameras if cam.role == "fit"]
    steps = [("set_opacity", "1"), ("enable_frame", fit[0]), ("set_fade_end_m", "20")]
    for operator, value in steps:
        metrics = run_tick(bundle_dir, work_dir, operator, value)
    return metrics


def apply_judge(work_dir: Path, verdict: str) -> dict:
    if verdict not in ("accept", "reject"):
        raise BubbleError("verdict must be accept or reject")
    metrics_path = work_dir / "metrics.json"
    metrics = json.loads(metrics_path.read_text())
    if verdict == "accept":
        if metrics["geometry_delta_m_max"] != 0.0 or not metrics["outside_unchanged"]:
            raise BubbleError("refusing accept; geometry or footprint gate failed")
        metrics["human_verdict"] = "accept"
        metrics["awaiting_human"] = False
        metrics["pass"] = True
        metrics["active_recommendation"] = "refined"
    else:
        metrics["human_verdict"] = "reject"
        metrics["awaiting_human"] = False
        metrics["pass"] = False
        metrics["active_recommendation"] = "baseline"
    metrics_path.write_text(json.dumps(metrics, indent=2))
    block_path = work_dir / "bubble_block.json"
    block = json.loads(block_path.read_text())
    block["active"] = metrics["active_recommendation"]
    block["metrics"] = metrics
    block_path.write_text(json.dumps(block, indent=2))
    return metrics

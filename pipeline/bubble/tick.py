from __future__ import annotations

from pathlib import Path
import json

import numpy as np
from PIL import Image

from bubble.bundle import Bundle, load_bundle
from bubble.compose import as_rgb, bake_texture
from bubble.errors import BubbleError
from bubble.gates import MAX_TICKS, THRESHOLD_SSIM_RECORDED, Scores, decide_pass, verdict
from bubble.gltf import position_delta, write_glb
from bubble.params import RefineParams, apply_operator, load_params, save_params
from bubble.render import render_mesh
from bubble.score import score_views, ssim_masked, write_compare


def format_tick(tick, operator, value, before: Scores, after: Scores, result: str, reverted: bool) -> str:
    revert_note = "; reverted" if reverted else ""
    return "\n".join([
        f"## Tick {tick} | operator {operator} {value}",
        "### Gaps vs ref",
        f"- fit SSIM {before.fit_ssim:.4f}; holdout SSIM {before.holdout_ssim:.4f}",
        "- compare PNGs are the visual judge; these numbers are the revert gate",
        "### Edit made",
        f"- {operator} {value}",
        "### Render result",
        f"- {result} on fit_ssim ({before.fit_ssim:.4f} -> {after.fit_ssim:.4f}); "
        f"holdout {before.holdout_ssim:.4f} -> {after.holdout_ssim:.4f}{revert_note}",
        "### Next",
        "- stay" if reverted else "- continue",
        "",
    ])


def _dump(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))


def _save_rgb(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.asarray(image, dtype=np.uint8), mode="RGB").save(path)


def _load_metrics(work_dir: Path) -> dict:
    return json.loads((work_dir / "metrics.json").read_text())


def render_all(bundle: Bundle, texture: np.ndarray) -> dict[str, np.ndarray]:
    return {cam.name: render_mesh(bundle.dem, texture, cam, bundle.grid) for cam in bundle.cameras}


def score_images(bundle: Bundle, rendered: dict[str, np.ndarray]) -> Scores:
    per = {}
    for cam in bundle.cameras:
        if cam.role == "nadir" or cam.image is None:
            continue
        per[cam.name] = ssim_masked(rendered[cam.name], bundle.images[cam.name])
    return score_views(per, bundle.cameras)


def write_views(bundle: Bundle, work_dir: Path, refined_images: dict[str, np.ndarray]) -> None:
    for cam in bundle.cameras:
        base = np.asarray(Image.open(work_dir / "cache" / f"{cam.name}.png").convert("RGB"))
        if cam.image is None:
            ref = base
        else:
            ref = bundle.images[cam.name]
        write_compare(work_dir / "compare" / f"{cam.name}.png", ref, base, refined_images[cam.name])


def fresh_metrics(bundle: Bundle, baseline: Scores) -> dict:
    return {
        "threshold_ssim": THRESHOLD_SSIM_RECORDED,
        "ssim": baseline.fit_ssim,
        "lpips": None,
        "pass": False,
        "numeric_pass": False,
        "active_recommendation": "baseline",
        "geometry_delta_m_max": 0.0,
        "registration_rms_m": bundle.registration_rms_m,
        "holdout_ssim": baseline.holdout_ssim,
        "fit_ssim": baseline.fit_ssim,
        "baseline_fit_ssim": baseline.fit_ssim,
        "baseline_holdout_ssim": baseline.holdout_ssim,
        "human_verdict": "pending",
        "awaiting_human": bundle.requires_human,
        "outside_unchanged": True,
        "iterations": [],
        "bubble_id": bundle.bubble_id,
        "requires_human": bundle.requires_human,
        "size_m": bundle.size_m,
        "center_x": bundle.grid.center_x,
        "center_y": bundle.grid.center_y,
    }


def write_block(work_dir: Path, metrics: dict) -> None:
    block = {
        "id": metrics["bubble_id"],
        "size_m": metrics["size_m"],
        "center_x": metrics["center_x"],
        "center_y": metrics["center_y"],
        "baseline_mesh": "/bubble/baseline/mesh.glb",
        "refined_mesh": "/bubble/refined/mesh.glb",
        "active": metrics["active_recommendation"],
        "metrics": metrics,
    }
    _dump(work_dir / "bubble_block.json", block)


def update_gates(bundle: Bundle, work_dir: Path, metrics: dict) -> dict:
    params = load_params(work_dir / "params.json")
    texture, touched = bake_texture(
        bundle.dem, bundle.ortho, bundle.grid, bundle.cameras, bundle.images,
        params, bundle.poses_trusted,
    )
    ortho = as_rgb(bundle.ortho)
    outside = True if not np.any(~touched) else bool(np.array_equal(texture[~touched], ortho[~touched]))
    delta = position_delta(work_dir / "baseline" / "mesh.glb", work_dir / "refined" / "mesh.glb")
    metrics["outside_unchanged"] = outside
    metrics["geometry_delta_m_max"] = delta
    metrics["ssim"] = metrics["fit_ssim"]
    numeric = decide_pass(
        Scores(metrics["baseline_fit_ssim"], metrics["baseline_holdout_ssim"]),
        Scores(metrics["fit_ssim"], metrics["holdout_ssim"]),
        delta,
        outside,
    )
    metrics["numeric_pass"] = numeric
    verdict_name = metrics.get("human_verdict")
    if verdict_name == "accept":
        metrics["pass"] = True
        metrics["active_recommendation"] = "refined"
        metrics["awaiting_human"] = False
    elif verdict_name == "reject":
        metrics["pass"] = False
        metrics["active_recommendation"] = "baseline"
        metrics["awaiting_human"] = False
    elif bundle.requires_human:
        metrics["human_verdict"] = "pending"
        metrics["awaiting_human"] = True
        metrics["pass"] = False
        metrics["active_recommendation"] = "baseline"
    else:
        metrics["human_verdict"] = "auto_gate"
        metrics["awaiting_human"] = False
        metrics["pass"] = numeric
        metrics["active_recommendation"] = "refined" if numeric else "baseline"
    _dump(work_dir / "metrics.json", metrics)
    write_block(work_dir, metrics)
    return metrics


def _write_pair(bundle: Bundle, work_dir: Path, texture: np.ndarray, kind: str) -> None:
    _save_rgb(work_dir / kind / "texture.png", texture)
    write_glb(work_dir / kind / "mesh.glb", bundle.dem, texture, bundle.grid)


def ensure_initialized(bundle: Bundle, work_dir: Path) -> dict:
    work_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = work_dir / "metrics.json"
    if metrics_path.exists():
        return _load_metrics(work_dir)
    params = RefineParams()
    texture, _touched = bake_texture(
        bundle.dem, bundle.ortho, bundle.grid, bundle.cameras, bundle.images, params, bundle.poses_trusted
    )
    images = render_all(bundle, texture)
    for name, image in images.items():
        _save_rgb(work_dir / "cache" / f"{name}.png", image)
    _write_pair(bundle, work_dir, texture, "baseline")
    _write_pair(bundle, work_dir, texture, "refined")
    write_views(bundle, work_dir, images)
    save_params(work_dir / "params.json", params)
    scores = score_images(bundle, images)
    metrics = fresh_metrics(bundle, scores)
    header = (
        "# Bubble refine loop\n\n"
        "Photos are judged from `compare/`. Fit SSIM checks that a projected photo "
        "reappears from its own camera. It does not prove the pose is located on Mars. "
        "`human_verdict` is that check.\n\n"
    )
    (work_dir / "LOOP_STATE.md").write_text(header)
    return update_gates(bundle, work_dir, metrics)


def run_tick(bundle_dir: Path, work_dir: Path, operator: str, value: str) -> dict:
    bundle = load_bundle(bundle_dir)
    metrics = ensure_initialized(bundle, work_dir)
    if len(metrics["iterations"]) >= MAX_TICKS:
        raise BubbleError("tick cap 5 reached")
    params = load_params(work_dir / "params.json")
    new_params = apply_operator(params, operator, value, bundle.cameras)
    if new_params.enabled_frames and not bundle.poses_trusted:
        raise BubbleError("poses_trusted is false; refusing to project")
    before = Scores(metrics["fit_ssim"], metrics["holdout_ssim"])
    texture, _touched = bake_texture(
        bundle.dem, bundle.ortho, bundle.grid, bundle.cameras, bundle.images,
        new_params, bundle.poses_trusted,
    )
    images = render_all(bundle, texture)
    after = score_images(bundle, images)
    result = verdict(before, after)
    reverted = result == "worse"
    if not reverted:
        save_params(work_dir / "params.json", new_params)
        _write_pair(bundle, work_dir, texture, "refined")
        write_views(bundle, work_dir, images)
        metrics["fit_ssim"] = after.fit_ssim
        metrics["holdout_ssim"] = after.holdout_ssim
    entry = {
        "tick": len(metrics["iterations"]) + 1,
        "operator": operator,
        "value": value,
        "fit_ssim": after.fit_ssim,
        "holdout_ssim": after.holdout_ssim,
        "result": result,
        "reverted": reverted,
    }
    metrics["iterations"].append(entry)
    with (work_dir / "LOOP_STATE.md").open("a") as handle:
        handle.write(format_tick(entry["tick"], operator, value, before, after, result, reverted))
    return update_gates(bundle, work_dir, metrics)

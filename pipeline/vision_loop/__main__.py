"""Commands for the vision refine loop.

  PYTHONPATH=pipeline python3 -m vision_loop init --bundle DIR --work DIR
  PYTHONPATH=pipeline python3 -m vision_loop render --work DIR --camera NAME
  PYTHONPATH=pipeline python3 -m vision_loop revert --work DIR
  PYTHONPATH=pipeline python3 -m vision_loop gap --work DIR
  PYTHONPATH=pipeline python3 -m vision_loop check --work DIR
  PYTHONPATH=pipeline python3 -m vision_loop drive --work DIR
"""

from __future__ import annotations

from pathlib import Path
import argparse
import json
import math

import numpy as np
from PIL import Image

from bubble.geom import Camera
from bubble.gltf import write_glb
from bubble.render import render_mesh
from vision_loop.coverage import coverage_count, inferred_ops_on_high_coverage, low_coverage_mask
from vision_loop.drive_surface import write_drive
from vision_loop.errors import VisionError
from vision_loop.model import compile_model, load_baseline, load_model, pop_last, save_model
from vision_loop.workspace import init_workspace


def _cameras(work: Path) -> tuple[Camera, ...]:
    raw = json.loads((work / "viewpoints.json").read_text())
    cameras = []
    for item in raw["cameras"]:
        cameras.append(Camera(
            name=item["name"],
            role=item["role"],
            xyz=(float(item["xyz"][0]), float(item["xyz"][1]), float(item["xyz"][2])),
            heading_deg=float(item["heading_deg"]),
            pitch_deg=float(item["pitch_deg"]),
            fov_deg=float(item["fov_deg"]),
            width=int(item["width"]),
            height=int(item["height"]),
            image=item["image"],
        ))
    return tuple(cameras)


def _save_rgb(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.asarray(image, dtype=np.uint8), mode="RGB").save(path)


def _camera_record(work: Path, name: str) -> dict:
    raw = json.loads((work / "viewpoints.json").read_text())
    for item in raw["cameras"]:
        if item["name"] == name:
            return item
    raise VisionError(f"unknown camera '{name}'")


def _crop(height, grid, x: float, y: float, crop_m: float):
    from bubble.geom import Grid

    res = float(grid.res_m)
    c0 = max(0, int(np.floor((x - crop_m / 2 - grid.x0) / res)))
    c1 = min(height.shape[1] - 1, int(np.ceil((x + crop_m / 2 - grid.x0) / res)))
    r0 = max(0, int(np.floor((grid.y0 - (y + crop_m / 2)) / res)))
    r1 = min(height.shape[0] - 1, int(np.ceil((grid.y0 - (y - crop_m / 2)) / res)))
    sub = height[r0 : r1 + 1, c0 : c1 + 1]
    sub_grid = Grid(grid.x0 + c0 * res, grid.y0 - r0 * res, res, grid.center_x, grid.center_y)
    return sub, sub_grid, r0, c0


def render_camera(work: Path, name: str) -> Path:
    height, color, grid, _model = compile_model(work)
    cameras = {cam.name: cam for cam in _cameras(work)}
    if name not in cameras:
        raise VisionError(f"unknown camera '{name}'")
    cam = cameras[name]
    record = _camera_record(work, name)
    crop_m = record.get("crop_m")
    if crop_m:
        sub, sub_grid, row0, col0 = _crop(height, grid, cam.xyz[0], cam.xyz[1], float(crop_m))
        image = render_mesh(sub, color, cam, sub_grid, row0, col0, height.shape)
    else:
        image = render_mesh(height, color, cam, grid)
    out = work / "renders" / f"{name}.png"
    _save_rgb(out, image)
    write_glb(work / "mesh.glb", height, color, grid)
    if cam.image:
        ref = np.asarray(Image.open(work / cam.image).convert("RGB"))
        if ref.shape != image.shape:
            raise VisionError(f"{name} reference is {ref.shape} and the render is {image.shape}")
        strip = np.concatenate([ref, image], axis=1)
        _save_rgb(work / "compare" / f"{name}.png", strip)
    return out


def write_coverage(work: Path) -> np.ndarray:
    dem, _texture, grid = load_baseline(work)
    count = coverage_count(dem, grid, _cameras(work))
    picture = np.zeros((*count.shape, 3), dtype=np.uint8)
    picture[count == 1] = (90, 90, 90)
    picture[count >= 2] = (240, 240, 240)
    _save_rgb(work / "coverage.png", picture)
    np.save(work / "coverage.npy", count)
    return count


def gap_report(work: Path) -> dict:
    dem, _texture, grid = load_baseline(work)
    count = write_coverage(work)
    low = low_coverage_mask(count)
    rows, cols = np.nonzero(low)
    samples = []
    if len(rows):
        step = max(1, len(rows) // 5)
        for index in range(0, len(rows), step)[:5]:
            r, c = int(rows[index]), int(cols[index])
            samples.append({
                "x": float(grid.x0 + c * grid.res_m),
                "y": float(grid.y0 - r * grid.res_m),
                "views": int(count[r, c]),
            })
    report = {
        "low_fraction": float(low.mean()),
        "minimum_views": 2,
        "samples": samples,
    }
    (work / "gap_report.json").write_text(json.dumps(report, indent=2) + "\n")
    _render_gap_views(work, dem, grid)
    return report


def _render_gap_views(work: Path, dem, grid) -> None:
    rows, cols = dem.shape
    half_x = (cols - 1) * grid.res_m / 2.0
    half_y = (rows - 1) * grid.res_m / 2.0
    center = np.array([grid.x0 + half_x, grid.y0 - half_y, float(np.nanmean(dem))], dtype=np.float64)
    offsets = {
        "gap_north": (0.0, half_y * 0.7),
        "gap_east": (half_x * 0.7, 0.0),
        "gap_south": (0.0, -half_y * 0.7),
        "gap_west": (-half_x * 0.7, 0.0),
    }
    height, color, grid, _model = compile_model(work)
    for name, (dx, dy) in offsets.items():
        origin = np.array([center[0] + dx, center[1] + dy, center[2] + 18.0])
        heading, pitch = _look_at(origin, center)
        cam = Camera(name, "gap", tuple(origin.tolist()), heading, pitch, 55.0, 160, 120, None)
        sub, sub_grid, row0, col0 = _crop(height, grid, float(center[0]), float(center[1]), min(700.0, half_x * 2))
        image = render_mesh(sub, color, cam, sub_grid, row0, col0, height.shape)
        _save_rgb(work / "compare" / f"{name}.png", image)


def _look_at(origin: np.ndarray, target: np.ndarray) -> tuple[float, float]:
    delta = target - origin
    heading = math.degrees(math.atan2(float(delta[0]), float(delta[1])))
    horizontal = math.hypot(float(delta[0]), float(delta[1]))
    pitch = math.degrees(math.atan2(float(delta[2]), horizontal))
    return heading, pitch


def enhance_views(work: Path) -> list[str]:
    """Zoomed renders beside Mastcam close-ups. The photos are texture references, not the same place."""
    raw = json.loads((work / "viewpoints.json").read_text())
    targets = raw.get("enhance_targets") or []
    if not targets:
        raise VisionError("viewpoints.json has no enhance_targets")
    height, color, grid, _model = compile_model(work)
    written = []
    for target in targets:
        cam = Camera(
            target["name"], "fit",
            (float(target["x"]), float(target["y"]), float(target["z"])),
            float(target["heading_deg"]), float(target["pitch_deg"]),
            float(target.get("fov_deg", 35)), int(target.get("width", 220)), int(target.get("height", 220)),
            None,
        )
        sub, sub_grid, row0, col0 = _crop(height, grid, cam.xyz[0], cam.xyz[1], float(target.get("crop_m", 80)))
        image = render_mesh(sub, color, cam, sub_grid, row0, col0, height.shape)
        photo = np.asarray(Image.open(work / target["detail"]).convert("RGB").resize((image.shape[1], image.shape[0]), Image.Resampling.LANCZOS))
        _save_rgb(work / "compare" / f"enhance_{target['name']}.png", np.concatenate([photo, image], axis=1))
        written.append(target["name"])
    return written


def check_inferred(work: Path) -> list[str]:
    dem, _texture, grid = load_baseline(work)
    count = coverage_count(dem, grid, _cameras(work))
    model = load_model(work / "model.json")
    return inferred_ops_on_high_coverage(model["ops"], dem, grid, count)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="vision_loop")
    sub = parser.add_subparsers(dest="cmd", required=True)
    init = sub.add_parser("init")
    init.add_argument("--bundle", type=Path, required=True)
    init.add_argument("--work", type=Path, required=True)
    render = sub.add_parser("render")
    render.add_argument("--work", type=Path, required=True)
    render.add_argument("--camera", required=True)
    revert = sub.add_parser("revert")
    revert.add_argument("--work", type=Path, required=True)
    gap = sub.add_parser("gap")
    gap.add_argument("--work", type=Path, required=True)
    check = sub.add_parser("check")
    check.add_argument("--work", type=Path, required=True)
    drive = sub.add_parser("drive")
    drive.add_argument("--work", type=Path, required=True)
    enhance = sub.add_parser("enhance")
    enhance.add_argument("--work", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.cmd == "init":
        summary = init_workspace(args.bundle, args.work)
        print(json.dumps(summary, indent=2))
    elif args.cmd == "render":
        path = render_camera(args.work, args.camera)
        print(path)
    elif args.cmd == "revert":
        model = pop_last(load_model(args.work / "model.json"))
        save_model(args.work / "model.json", model)
        print(f"ops {len(model['ops'])}")
    elif args.cmd == "gap":
        print(json.dumps(gap_report(args.work), indent=2))
    elif args.cmd == "drive":
        print(write_drive(args.work))
    elif args.cmd == "enhance":
        print(" ".join(enhance_views(args.work)))
    elif args.cmd == "check":
        problems = check_inferred(args.work)
        if problems:
            raise SystemExit("inferred op on surveyed ground:\n" + "\n".join(problems))
        print("inferred ops are only on low-coverage ground")


if __name__ == "__main__":
    main()

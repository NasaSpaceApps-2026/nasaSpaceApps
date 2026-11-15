"""Editable text model. Ops compile onto a copy of the baseline height and texture."""

from __future__ import annotations

from pathlib import Path
import json

import numpy as np
from PIL import Image

from bubble.geom import grid_xy
from vision_loop.errors import VisionError

MAX_RADIUS_M = 8.0
MAX_ABS_HEIGHT_M = 1.5


def empty_model() -> dict:
    return {"ops": []}


def load_model(path: Path) -> dict:
    raw = json.loads(path.read_text())
    if "ops" not in raw or not isinstance(raw["ops"], list):
        raise VisionError("model.json needs an ops list")
    return raw


def save_model(path: Path, model: dict) -> None:
    path.write_text(json.dumps(model, indent=2) + "\n")


def pop_last(model: dict) -> dict:
    ops = list(model.get("ops", []))
    if not ops:
        raise VisionError("model.json has no ops to revert")
    ops.pop()
    return {"ops": ops}


def _falloff(x, y, cx: float, cy: float, radius: float):
    if radius <= 0 or radius > MAX_RADIUS_M:
        raise VisionError(f"radius_m must be in (0, {MAX_RADIUS_M}]")
    distance = np.hypot(x - cx, y - cy)
    t = np.clip(distance / radius, 0.0, 1.0)
    smooth = t * t * (3.0 - 2.0 * t)
    return 1.0 - smooth


def _check_op(op: dict) -> None:
    kind = op.get("op")
    if kind not in {"bump", "paint", "smooth"}:
        raise VisionError(f"unknown op '{kind}'")
    if kind == "bump" and abs(float(op["height_m"])) > MAX_ABS_HEIGHT_M:
        raise VisionError(f"height_m must be within ±{MAX_ABS_HEIGHT_M}")
    if kind in {"paint", "smooth"}:
        strength = float(op["strength"])
        if not 0.0 <= strength <= 1.0:
            raise VisionError("strength must be in [0, 1]")
    if kind == "paint":
        rgb = op["rgb"]
        if len(rgb) != 3 or any(not 0 <= int(channel) <= 255 for channel in rgb):
            raise VisionError("rgb must be three channels in 0..255")


def apply_ops(dem: np.ndarray, texture: np.ndarray, grid, ops: list[dict]):
    height = np.array(dem, dtype=np.float64, copy=True)
    color = np.array(texture, dtype=np.float64, copy=True)
    if color.ndim == 2:
        color = np.repeat(color[..., None], 3, axis=2)
    rows, cols = height.shape
    x, y = grid_xy(grid, rows, cols)
    for op in ops:
        _check_op(op)
        kind = op["op"]
        if kind == "paint" and color.shape[:2] != height.shape:
            weight = _paint_weight(grid, height.shape, color.shape, float(op["x"]), float(op["y"]), float(op["radius_m"]))
        else:
            weight = _falloff(x, y, float(op["x"]), float(op["y"]), float(op["radius_m"]))
        if kind == "bump":
            height = height + float(op["height_m"]) * weight
        elif kind == "paint":
            rgb = np.array(op["rgb"], dtype=np.float64)
            strength = float(op["strength"]) * weight[..., None]
            color = color * (1.0 - strength) + rgb * strength
        else:
            blurred = _box_mean(height)
            strength = float(op["strength"]) * weight
            height = height * (1.0 - strength) + blurred * strength
    return height.astype(np.float32), np.clip(np.rint(color), 0, 255).astype(np.uint8)


def _paint_weight(grid, dem_shape, texture_shape, cx: float, cy: float, radius: float):
    """Paint on a texture that is a finer sampling of the same ground as the height grid."""
    dem_rows, dem_cols = dem_shape
    tex_rows, tex_cols = texture_shape[:2]
    res = float(grid.res_m) * (dem_cols - 1) / (tex_cols - 1)
    from bubble.geom import Grid

    fine = Grid(grid.x0, grid.y0, res, grid.center_x, grid.center_y)
    xs, ys = grid_xy(fine, tex_rows, tex_cols)
    return _falloff(xs, ys, cx, cy, radius)


def _box_mean(height: np.ndarray) -> np.ndarray:
    padded = np.pad(height, 1, mode="edge")
    total = np.zeros_like(height)
    for dr in range(3):
        for dc in range(3):
            total += padded[dr : dr + height.shape[0], dc : dc + height.shape[1]]
    return total / 9.0


def load_baseline(work: Path):
    dem = np.load(work / "baseline" / "dem.npy", allow_pickle=False).astype(np.float32)
    texture = np.asarray(Image.open(work / "baseline" / "texture.png").convert("RGB"))
    grid_raw = json.loads((work / "grid.json").read_text())
    from bubble.geom import Grid

    grid = Grid(
        x0=float(grid_raw["x0"]),
        y0=float(grid_raw["y0"]),
        res_m=float(grid_raw["res_m"]),
        center_x=float(grid_raw.get("center_x", 0.0)),
        center_y=float(grid_raw.get("center_y", 0.0)),
    )
    return dem, texture, grid


def compile_model(work: Path):
    dem, texture, grid = load_baseline(work)
    model = load_model(work / "model.json")
    height, color = apply_ops(dem, texture, grid, model["ops"])
    return height, color, grid, model

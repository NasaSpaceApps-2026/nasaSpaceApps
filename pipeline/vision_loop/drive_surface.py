"""Import Cesium dem2m heights into the JSON the vision agent edits."""

from __future__ import annotations

from pathlib import Path
import json
import shutil

import numpy as np
from PIL import Image

from bubble.geom import grid_xy
from vision_loop.errors import VisionError
from vision_loop.model import _falloff, compile_model, load_baseline

STEP = 0.01


def encode_heights(elev: np.ndarray) -> tuple[np.ndarray, float]:
    elev = np.asarray(elev, dtype=np.float64)
    finite = elev[np.isfinite(elev)]
    if finite.size == 0:
        raise VisionError("no finite heights to encode")
    base = float(np.floor(finite.min()))
    scaled = np.rint((elev - base) / STEP)
    if float(np.nanmax(scaled)) > 65535 or float(np.nanmin(scaled)) < 0:
        raise VisionError("relief does not fit uint16 at 0.01 m")
    values = np.zeros(elev.shape, dtype=np.uint16)
    good = np.isfinite(elev)
    values[good] = scaled[good].astype(np.uint16)
    return values, base


def _bilinear(values: np.ndarray, cf: float, rf: float) -> float:
    rows, cols = values.shape
    if cf < 0 or rf < 0 or cf > cols - 1 or rf > rows - 1:
        return float("nan")
    c0 = int(np.floor(cf))
    r0 = int(np.floor(rf))
    c1 = min(c0 + 1, cols - 1)
    r1 = min(r0 + 1, rows - 1)
    fc = cf - c0
    fr = rf - r0
    v00 = float(values[r0, c0])
    v01 = float(values[r0, c1])
    v10 = float(values[r1, c0])
    v11 = float(values[r1, c1])
    return (v00 * (1 - fc) + v01 * fc) * (1 - fr) + (v10 * (1 - fc) + v11 * fc) * fr


def sample_vertex(x: float, y: float, *, values, base: float, res_m: float, x0: float, y0: float) -> float:
    cf = (x - x0) / res_m
    rf = (y0 - y) / res_m
    value = _bilinear(values, cf, rf)
    if value != value:
        return value
    return base + STEP * value


def sample_pixel_center(
    x: float,
    y: float,
    *,
    values,
    base: float,
    res_m: float,
    size_m: float,
    center_x: float,
    center_y: float,
) -> float:
    half = size_m / 2.0
    cf = ((x - center_x) + half) / res_m - 0.5
    rf = (half - (y - center_y)) / res_m - 0.5
    value = _bilinear(values, cf, rf)
    if value != value:
        return value
    return base + STEP * value


def guess_mask(dem_shape, grid, ops) -> np.ndarray:
    rows, cols = int(dem_shape[0]), int(dem_shape[1])
    guessed = np.zeros((rows, cols), dtype=np.uint8)
    x, y = grid_xy(grid, rows, cols)
    for op in ops:
        if op.get("inferred") is not True:
            continue
        weight = _falloff(x, y, float(op["x"]), float(op["y"]), float(op["radius_m"]))
        guessed[weight > 0.05] = 1
    return guessed


def compile_drive(work: Path) -> dict:
    work = Path(work)
    height, texture, grid, model = compile_model(work)
    dem, _baseline_texture, _baseline_grid = load_baseline(work)
    values, base = encode_heights(height)
    baseline_values, baseline_base = encode_heights(dem)
    layout = json.loads((work / "grid.json").read_text())
    return {
        "values": values,
        "base": base,
        "res_m": float(layout["res_m"]),
        "x0": float(layout["x0"]),
        "y0": float(layout["y0"]),
        "texture": texture,
        "guessed": guess_mask(height.shape, grid, model.get("ops", [])),
        "baseline_values": baseline_values,
        "baseline_base": baseline_base,
    }


def write_drive(work: Path) -> Path:
    work = Path(work)
    drive = compile_drive(work)
    out = work / "drive"
    out.mkdir(parents=True, exist_ok=True)
    values = np.ascontiguousarray(drive["values"], dtype="<u2")
    values.tofile(out / "height.u16")
    rows, cols = values.shape
    header = {
        "layout": "vertex",
        "width": int(cols),
        "height": int(rows),
        "res_m": float(drive["res_m"]),
        "x0": float(drive["x0"]),
        "y0": float(drive["y0"]),
        "base": float(drive["base"]),
        "step": STEP,
    }
    (out / "height.json").write_text(json.dumps(header, indent=2) + "\n")
    Image.fromarray(np.asarray(drive["texture"], dtype=np.uint8)).save(out / "texture.png")
    np.ascontiguousarray(drive["guessed"], dtype=np.uint8).tofile(out / "guessed.u8")
    return out


def import_cesium_height(
    u16_path,
    json_path,
    work: Path,
    size_m: float | None = None,
) -> dict:
    header = json.loads(Path(json_path).read_text())
    width = int(header["width"])
    height = int(header["height"])
    res_m = float(header["res_m"])
    values = np.fromfile(u16_path, dtype="<u2")
    if values.size != width * height:
        raise VisionError(
            f"uint16 length {values.size} does not match {width}×{height}"
        )

    if size_m is None:
        size_m = width * res_m
    center_x = float(header.get("center_x", 0.0))
    center_y = float(header.get("center_y", 0.0))
    x0 = center_x - size_m / 2.0 + res_m / 2.0
    y0 = center_y + size_m / 2.0 - res_m / 2.0

    work = Path(work)
    baseline = work / "baseline"
    baseline.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(u16_path, baseline / "height.u16")

    model = {
        "source": "cesium_dem2m",
        "layout": "pixel_center",
        "width": width,
        "height": height,
        "res_m": res_m,
        "size_m": size_m,
        "center_x": center_x,
        "center_y": center_y,
        "base": float(header["base"]),
        "step": float(header["step"]),
        "heights": "baseline/height.u16",
        "ops": [],
        "x0": x0,
        "y0": y0,
    }
    (work / "model.json").write_text(json.dumps(model, indent=2) + "\n")

    baseline_header = dict(header)
    baseline_header.update({
        "layout": "pixel_center",
        "size_m": size_m,
        "center_x": center_x,
        "center_y": center_y,
        "x0": x0,
        "y0": y0,
    })
    (baseline / "height.json").write_text(json.dumps(baseline_header, indent=2) + "\n")
    return model

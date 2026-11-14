"""Import Cesium dem2m heights into the JSON the vision agent edits."""

from __future__ import annotations

from pathlib import Path
import json
import shutil

import numpy as np

from vision_loop.errors import VisionError

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

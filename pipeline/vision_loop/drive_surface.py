"""Import Cesium dem2m heights into the JSON the vision agent edits."""

from __future__ import annotations

from pathlib import Path
import json
import shutil

import numpy as np

from vision_loop.errors import VisionError


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

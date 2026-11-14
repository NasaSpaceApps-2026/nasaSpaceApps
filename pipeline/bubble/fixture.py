from __future__ import annotations

from pathlib import Path
import json

import numpy as np
from PIL import Image

from bubble.geom import Camera, Grid
from bubble.render import render_mesh

PEAK_ROW = 14
PEAK_COL = 20


def box_blur(image: np.ndarray, radius: int) -> np.ndarray:
    k = 2 * radius + 1
    pad = np.pad(image.astype(np.float64), ((radius, radius), (radius, radius), (0, 0)), mode="edge")
    cumulative = np.cumsum(np.cumsum(pad, axis=0), axis=1)
    cumulative = np.pad(cumulative, ((1, 0), (1, 0), (0, 0)))
    window = (
        cumulative[k:, k:, :]
        - cumulative[:-k, k:, :]
        - cumulative[k:, :-k, :]
        + cumulative[:-k, :-k, :]
    )
    return window / (k * k)


def _save_rgb(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.asarray(image, dtype=np.uint8), mode="RGB").save(path)


def make_fixture(out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    height = width = 33
    yy, xx = np.mgrid[0:height, 0:width]
    dem = (0.15 * (xx - width / 2) / width).astype(np.float64)
    dem += 1.2 * np.exp(-((xx - PEAK_COL) ** 2 + (yy - PEAK_ROW) ** 2) / 18.0)
    checks = ((yy // 2 + xx // 2) % 2).astype(bool)
    albedo = np.zeros((height, width, 3), np.uint8)
    albedo[checks] = (220, 180, 80)
    albedo[~checks] = (40, 50, 70)
    ortho_f = albedo.astype(np.float64)
    for _ in range(6):
        ortho_f = box_blur(ortho_f, 3)
    ortho = np.clip(np.rint(ortho_f), 0, 255).astype(np.uint8)
    grid = Grid(x0=-16, y0=16, res_m=1, center_x=0, center_y=0)
    fit = Camera("fit_0", "fit", (1.0, -1.0, 2.4), 45, -20, 60, 96, 72, "refs/fit_0.png")
    hold = Camera("holdout_0", "holdout", (2.5, -2.5, 2.6), 18, -22, 60, 96, 72, "refs/holdout_0.png")
    nadir = Camera("nadir", "nadir", (0.0, 0.0, 40.0), 0, -90, 50, 64, 64, None)
    np.save(out / "dem.npy", dem.astype(np.float32))
    np.save(out / "albedo.npy", albedo)
    _save_rgb(out / "ortho.png", ortho)
    _save_rgb(out / "refs" / "fit_0.png", render_mesh(dem, albedo, fit, grid))
    _save_rgb(out / "refs" / "holdout_0.png", render_mesh(dem, albedo, hold, grid))
    (out / "grid.json").write_text(json.dumps({
        "x0": grid.x0, "y0": grid.y0, "res_m": grid.res_m,
        "center_x": grid.center_x, "center_y": grid.center_y,
    }, indent=2))

    def dump(cam: Camera) -> dict:
        return {
            "name": cam.name,
            "role": cam.role,
            "xyz": list(cam.xyz),
            "heading_deg": cam.heading_deg,
            "pitch_deg": cam.pitch_deg,
            "fov_deg": cam.fov_deg,
            "width": cam.width,
            "height": cam.height,
            "image": cam.image,
        }

    (out / "viewpoints.json").write_text(json.dumps({
        "frame": "site_enu",
        "poses_trusted": True,
        "requires_human": False,
        "registration_rms_m": 0.0,
        "bubble_id": "fixture_mound",
        "cameras": [dump(nadir), dump(fit), dump(hold)],
    }, indent=2))
    return out

"""Build a vision-loop workspace from the same bundle the projection loop uses."""

from __future__ import annotations

from pathlib import Path
import json
import shutil

import numpy as np
from PIL import Image

from bubble.bundle import load_bundle
from vision_loop.model import empty_model, save_model
from vision_loop.sets import (
    MAX_DIRECTION_DEG,
    MAX_IMAGES,
    MAX_POSITION_M,
    build_sets,
)

LOOP_HEADER = """# Vision loop state

The judge is the render PNG next to the reference photo. model.json is wrong when the PNG is wrong.

## Next
- set_01, first image in focus_order, tick 0
"""


def init_workspace(bundle_dir: Path, work: Path) -> dict:
    if work.exists():
        shutil.rmtree(work)
    bundle = load_bundle(bundle_dir)
    baseline = work / "baseline"
    baseline.mkdir(parents=True)
    np.save(baseline / "dem.npy", bundle.dem)
    Image.fromarray(bundle.ortho, mode="RGB").save(baseline / "texture.png")
    (work / "refs").mkdir()
    for cam in bundle.cameras:
        if cam.image:
            shutil.copy(bundle.root / cam.image, work / "refs" / Path(cam.image).name)
    (work / "grid.json").write_text(json.dumps({
        "x0": bundle.grid.x0,
        "y0": bundle.grid.y0,
        "res_m": bundle.grid.res_m,
        "center_x": bundle.grid.center_x,
        "center_y": bundle.grid.center_y,
    }, indent=2) + "\n")
    cameras = []
    for cam in bundle.cameras:
        image = None if cam.image is None else "refs/" + Path(cam.image).name
        cameras.append({
            "name": cam.name,
            "role": cam.role,
            "xyz": list(cam.xyz),
            "heading_deg": cam.heading_deg,
            "pitch_deg": cam.pitch_deg,
            "fov_deg": cam.fov_deg,
            "width": cam.width,
            "height": cam.height,
            "image": image,
        })
    (work / "viewpoints.json").write_text(json.dumps({
        "frame": "site_enu",
        "bubble_id": bundle.bubble_id,
        "cameras": cameras,
    }, indent=2) + "\n")
    sets = build_sets(bundle.cameras)
    (work / "sets.json").write_text(json.dumps({
        "max_images": MAX_IMAGES,
        "max_position_m": MAX_POSITION_M,
        "max_direction_deg": MAX_DIRECTION_DEG,
        "sets": sets,
    }, indent=2) + "\n")
    save_model(work / "model.json", empty_model())
    (work / "LOOP_STATE.md").write_text(LOOP_HEADER)
    (work / "renders").mkdir()
    (work / "compare").mkdir()
    return {"bubble_id": bundle.bubble_id, "sets": sets}

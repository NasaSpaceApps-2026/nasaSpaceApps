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
    hires = bundle.root / "texture.png"
    if hires.exists():
        shutil.copy(hires, baseline / "texture.png")
    else:
        Image.fromarray(bundle.ortho).save(baseline / "texture.png")
    if (bundle.root / "refs").exists():
        shutil.copytree(bundle.root / "refs", work / "refs")
    else:
        (work / "refs").mkdir()
    (work / "grid.json").write_text(json.dumps({
        "x0": bundle.grid.x0,
        "y0": bundle.grid.y0,
        "res_m": bundle.grid.res_m,
        "center_x": bundle.grid.center_x,
        "center_y": bundle.grid.center_y,
    }, indent=2) + "\n")
    raw_view = json.loads((bundle.root / "viewpoints.json").read_text())
    for item in raw_view.get("cameras", []):
        if item.get("image"):
            item["image"] = "refs/" + Path(item["image"]).name
    raw_view["bubble_id"] = bundle.bubble_id
    (work / "viewpoints.json").write_text(json.dumps(raw_view, indent=2) + "\n")
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

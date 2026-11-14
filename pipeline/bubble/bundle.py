from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json

import numpy as np
from PIL import Image

from bubble.errors import BubbleError
from bubble.geom import Camera, Grid

ROLES = {"nadir", "fit", "holdout"}


@dataclass(frozen=True)
class Bundle:
    root: Path
    dem: np.ndarray
    ortho: np.ndarray
    grid: Grid
    cameras: tuple[Camera, ...]
    images: dict
    poses_trusted: bool
    requires_human: bool
    registration_rms_m: float | None
    bubble_id: str
    size_m: float


def _rgb(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB"))


def load_bundle(path: Path) -> Bundle:
    path = path.resolve()
    dem = np.load(path / "dem.npy", allow_pickle=False)
    if dem.ndim != 2:
        raise BubbleError("dem.npy must be a 2D array")
    dem = dem.astype(np.float32)
    ortho = _rgb(path / "ortho.png")
    if ortho.shape[:2] != dem.shape:
        raise BubbleError("ortho.png shape does not match dem.npy")
    grid_raw = json.loads((path / "grid.json").read_text())
    grid = Grid(
        x0=float(grid_raw["x0"]),
        y0=float(grid_raw["y0"]),
        res_m=float(grid_raw["res_m"]),
        center_x=float(grid_raw.get("center_x", 0.0)),
        center_y=float(grid_raw.get("center_y", 0.0)),
    )
    if grid.res_m <= 0:
        raise BubbleError("res_m must be positive")
    raw = json.loads((path / "viewpoints.json").read_text())
    cameras = []
    images = {}
    for item in raw["cameras"]:
        if item["role"] not in ROLES:
            raise BubbleError(f"unknown role '{item['role']}'")
        image_name = item["image"]
        cam = Camera(
            name=item["name"],
            role=item["role"],
            xyz=(float(item["xyz"][0]), float(item["xyz"][1]), float(item["xyz"][2])),
            heading_deg=float(item["heading_deg"]),
            pitch_deg=float(item["pitch_deg"]),
            fov_deg=float(item["fov_deg"]),
            width=int(item["width"]),
            height=int(item["height"]),
            image=image_name,
        )
        if cam.fov_deg <= 0 or cam.width < 2 or cam.height < 2:
            raise BubbleError(f"bad camera '{cam.name}'")
        cameras.append(cam)
        if image_name is not None:
            image = _rgb(path / image_name)
            if image.shape[1] != cam.width or image.shape[0] != cam.height:
                raise BubbleError(
                    f"{cam.name} image is {image.shape[1]}x{image.shape[0]}, "
                    f"camera says {cam.width}x{cam.height}"
                )
            images[cam.name] = image
        elif cam.role != "nadir":
            raise BubbleError(f"{cam.role} camera '{cam.name}' needs an image")
    if not any(cam.role == "nadir" for cam in cameras):
        raise BubbleError("bundle needs one nadir camera")
    if not any(cam.role == "fit" for cam in cameras):
        raise BubbleError("bundle needs a fit camera")
    height, width = dem.shape
    size_m = float(max((height - 1) * grid.res_m, (width - 1) * grid.res_m))
    rms = raw["registration_rms_m"]
    return Bundle(
        root=path,
        dem=dem,
        ortho=ortho,
        grid=grid,
        cameras=tuple(cameras),
        images=images,
        poses_trusted=bool(raw["poses_trusted"]),
        requires_human=bool(raw["requires_human"]),
        registration_rms_m=None if rms is None else float(rms),
        bubble_id=str(raw["bubble_id"]),
        size_m=size_m,
    )

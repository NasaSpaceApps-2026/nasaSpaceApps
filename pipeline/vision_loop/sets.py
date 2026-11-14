"""Group posed photos that see the same ground from similar directions."""

from __future__ import annotations

import numpy as np

from bubble.geom import camera_basis

MAX_IMAGES = 4
MAX_POSITION_M = 40.0
MAX_DIRECTION_DEG = 35.0


def forward(cam) -> np.ndarray:
    _right, _down, ahead = camera_basis(cam)
    return ahead


def angle_deg(a: np.ndarray, b: np.ndarray) -> float:
    a = a / np.linalg.norm(a)
    b = b / np.linalg.norm(b)
    cosine = float(np.clip(np.dot(a, b), -1.0, 1.0))
    return float(np.degrees(np.arccos(cosine)))


def _xy_distance(a, b) -> float:
    return float(np.linalg.norm(np.array(a.xyz, dtype=np.float64) - np.array(b.xyz, dtype=np.float64)))


def build_sets(
    cameras,
    max_images: int = MAX_IMAGES,
    max_position_m: float = MAX_POSITION_M,
    max_direction_deg: float = MAX_DIRECTION_DEG,
) -> list[dict]:
    """Cameras with an image file, clustered by place and facing.

    Membership is tested against the set's first camera, not against a chain,
    so a slow turn cannot pull a view from the opposite direction into the set.
    """
    photos = sorted((cam for cam in cameras if cam.image), key=lambda cam: cam.name)
    unused = list(photos)
    sets = []
    while unused:
        seed = unused.pop(0)
        seed_forward = forward(seed)
        members = [seed]
        held = []
        for cam in unused:
            if len(members) >= max_images:
                held.append(cam)
                continue
            close = _xy_distance(cam, seed) <= max_position_m
            aligned = angle_deg(forward(cam), seed_forward) <= max_direction_deg
            if close and aligned:
                members.append(cam)
            else:
                held.append(cam)
        unused = held
        center = np.mean([cam.xyz for cam in members], axis=0)
        ordered = sorted(members, key=lambda cam: (_xy_distance_xyz(cam.xyz, center), cam.name))
        sets.append(
            {
                "id": f"set_{len(sets) + 1:02d}",
                "cameras": [cam.name for cam in ordered],
                "focus_order": [cam.name for cam in ordered],
            }
        )
    return sets


def _xy_distance_xyz(xyz, center) -> float:
    return float(np.linalg.norm(np.array(xyz, dtype=np.float64) - np.array(center, dtype=np.float64)))

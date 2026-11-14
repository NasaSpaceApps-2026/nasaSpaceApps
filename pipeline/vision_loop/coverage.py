"""Count how many photos in the sets see each texel."""

from __future__ import annotations

import numpy as np

from bubble.geom import dem_normals, grid_xy, project_points

FRONT_CUTOFF = 0.05


def coverage_count(dem, grid, cameras) -> np.ndarray:
    rows, cols = dem.shape
    x, y = grid_xy(grid, rows, cols)
    z = np.asarray(dem, dtype=np.float64)
    nx, ny, nz = dem_normals(dem, grid.res_m)
    count = np.zeros((rows, cols), dtype=np.uint8)
    for cam in cameras:
        if not cam.image:
            continue
        u, v, _depth, valid = project_points(cam, x, y, z)
        inside = valid & (u >= 0) & (v >= 0) & (u <= cam.width - 1) & (v <= cam.height - 1)
        to_x = cam.xyz[0] - x
        to_y = cam.xyz[1] - y
        to_z = cam.xyz[2] - z
        dist = np.maximum(np.sqrt(to_x * to_x + to_y * to_y + to_z * to_z), 1e-6)
        facing = (nx * to_x + ny * to_y + nz * to_z) / dist
        seen = inside & (facing > FRONT_CUTOFF)
        count += seen.astype(np.uint8)
    return count


def low_coverage_mask(count: np.ndarray, minimum_views: int = 2) -> np.ndarray:
    return count < minimum_views


def inferred_ops_on_high_coverage(ops: list[dict], dem, grid, count: np.ndarray) -> list[str]:
    """Inferred guesses may only be centered on texels seen from fewer than two photos."""
    rows, cols = dem.shape
    x, y = grid_xy(grid, rows, cols)
    problems = []
    for index, op in enumerate(ops):
        if not op.get("inferred"):
            continue
        col = int(np.clip(round((float(op["x"]) - grid.x0) / grid.res_m), 0, cols - 1))
        row = int(np.clip(round((grid.y0 - float(op["y"])) / grid.res_m), 0, rows - 1))
        if int(count[row, col]) >= 2:
            problems.append(f"op {index} ({op['op']}) is inferred on a texel seen by {int(count[row, col])} photos")
    return problems

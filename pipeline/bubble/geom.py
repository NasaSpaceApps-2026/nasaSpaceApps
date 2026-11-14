from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from bubble.errors import BubbleError


@dataclass(frozen=True)
class Camera:
    name: str
    role: str
    xyz: tuple[float, float, float]
    heading_deg: float
    pitch_deg: float
    fov_deg: float
    width: int
    height: int
    image: str | None


@dataclass(frozen=True)
class Grid:
    x0: float
    y0: float
    res_m: float
    center_x: float = 0.0
    center_y: float = 0.0


def camera_basis(cam: Camera):
    yaw = np.deg2rad(cam.heading_deg)
    pitch = np.deg2rad(cam.pitch_deg)
    forward = np.array(
        [
            np.sin(yaw) * np.cos(pitch),
            np.cos(yaw) * np.cos(pitch),
            np.sin(pitch),
        ],
        dtype=np.float64,
    )
    right = np.array([np.cos(yaw), -np.sin(yaw), 0.0], dtype=np.float64)
    forward = forward / np.linalg.norm(forward)
    right = right / np.linalg.norm(right)
    down = np.cross(forward, right)
    down = down / np.linalg.norm(down)
    return right, down, forward


def project_points(cam: Camera, x, y, z):
    """Pixel x right, y down, depth forward. heading 0 looks north. pitch < 0 looks down."""
    right, down, forward = camera_basis(cam)
    px = np.asarray(x, dtype=np.float64) - cam.xyz[0]
    py = np.asarray(y, dtype=np.float64) - cam.xyz[1]
    pz = np.asarray(z, dtype=np.float64) - cam.xyz[2]
    depth = px * forward[0] + py * forward[1] + pz * forward[2]
    xr = px * right[0] + py * right[1] + pz * right[2]
    yd = px * down[0] + py * down[1] + pz * down[2]
    fx = (cam.width / 2.0) / np.tan(np.deg2rad(cam.fov_deg) / 2.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        u = cam.width / 2.0 + fx * xr / depth
        v = cam.height / 2.0 + fx * yd / depth
    valid = np.isfinite(u) & np.isfinite(v) & (depth > 0.05)
    return u, v, depth, valid


def grid_xy(grid: Grid, height: int, width: int):
    cols = np.arange(width, dtype=np.float64)
    rows = np.arange(height, dtype=np.float64)
    x = grid.x0 + cols[None, :] * grid.res_m
    y = grid.y0 - rows[:, None] * grid.res_m
    x = np.broadcast_to(x, (height, width)).copy()
    y = np.broadcast_to(y, (height, width)).copy()
    return x, y


def dem_normals(dem: np.ndarray, res: float):
    dem = np.asarray(dem, dtype=np.float64)
    dzdx = np.empty_like(dem)
    dzdy = np.empty_like(dem)
    dzdx[:, 1:-1] = (dem[:, 2:] - dem[:, :-2]) / (2 * res)
    dzdx[:, 0] = (dem[:, 1] - dem[:, 0]) / res
    dzdx[:, -1] = (dem[:, -1] - dem[:, -2]) / res
    dzdy[1:-1, :] = (dem[:-2, :] - dem[2:, :]) / (2 * res)
    dzdy[0, :] = (dem[0, :] - dem[1, :]) / res
    dzdy[-1, :] = (dem[-2, :] - dem[-1, :]) / res
    nx = -dzdx
    ny = -dzdy
    nz = np.ones_like(dem)
    nrm = np.sqrt(nx * nx + ny * ny + nz * nz)
    return nx / nrm, ny / nrm, nz / nrm


def sample_rgb(image: np.ndarray, u, v):
    rgb = image
    if rgb.ndim == 2:
        rgb = np.repeat(rgb[..., None], 3, axis=2)
    ih, iw = rgb.shape[:2]
    x = np.clip(np.asarray(u, dtype=np.float64), 0, iw - 1.001)
    y = np.clip(np.asarray(v, dtype=np.float64), 0, ih - 1.001)
    x0 = np.floor(x).astype(np.int32)
    y0 = np.floor(y).astype(np.int32)
    x1 = np.clip(x0 + 1, 0, iw - 1)
    y1 = np.clip(y0 + 1, 0, ih - 1)
    fx = (x - x0)[..., None]
    fy = (y - y0)[..., None]
    c00 = rgb[y0, x0].astype(np.float64)
    c10 = rgb[y0, x1].astype(np.float64)
    c01 = rgb[y1, x0].astype(np.float64)
    c11 = rgb[y1, x1].astype(np.float64)
    return (
        c00 * (1 - fx) * (1 - fy)
        + c10 * fx * (1 - fy)
        + c01 * (1 - fx) * fy
        + c11 * fx * fy
    )


def sample_dem(dem, row, col):
    height, width = dem.shape
    r = np.clip(row, 0, height - 1.001)
    c = np.clip(col, 0, width - 1.001)
    r0 = np.floor(r).astype(np.int32)
    c0 = np.floor(c).astype(np.int32)
    r1 = np.clip(r0 + 1, 0, height - 1)
    c1 = np.clip(c0 + 1, 0, width - 1)
    fr = r - r0
    fc = c - c0
    return (
        dem[r0, c0] * (1 - fr) * (1 - fc)
        + dem[r0, c1] * (1 - fr) * fc
        + dem[r1, c0] * fr * (1 - fc)
        + dem[r1, c1] * fr * fc
    )


def visibility_mask(cam: Camera, x, y, z, dem, grid: Grid, steps: int = 32, eps: float = 0.05):
    height, width = dem.shape
    ox, oy, oz = cam.xyz
    ts = np.linspace(0.05, 0.95, steps, dtype=np.float64)
    sx = ox + ts[:, None, None] * (x[None, :, :] - ox)
    sy = oy + ts[:, None, None] * (y[None, :, :] - oy)
    sz = oz + ts[:, None, None] * (z[None, :, :] - oz)
    col = (sx - grid.x0) / grid.res_m
    row = (grid.y0 - sy) / grid.res_m
    inside = (row >= 0) & (row <= height - 1) & (col >= 0) & (col <= width - 1)
    terrain = sample_dem(dem, row, col)
    blocked = inside & (terrain > sz + eps)
    return ~blocked.any(axis=0)


def distance_fade(dist, start: float, end: float):
    if end <= start:
        raise BubbleError("fade_end_m must be greater than fade_start_m")
    t = np.clip((dist - start) / (end - start), 0, 1)
    s = t * t * (3 - 2 * t)
    return 1 - s

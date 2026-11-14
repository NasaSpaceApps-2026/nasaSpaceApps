from __future__ import annotations

import numpy as np

from bubble.errors import BubbleError
from bubble.geom import distance_fade, dem_normals, grid_xy, project_points, sample_rgb, visibility_mask

FRONT_CUTOFF = 0.05


def as_rgb(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return np.repeat(image[..., None], 3, axis=2)
    if image.shape[2] != 3:
        raise BubbleError("ortho must be gray or RGB")
    return image


def bake_texture(dem, ortho, grid, cameras, images, params, poses_trusted: bool):
    height, width = dem.shape
    if ortho.shape[:2] != (height, width):
        raise BubbleError(f"ortho shape {ortho.shape[:2]} != dem {(height, width)}")
    base = as_rgb(ortho)
    out = base.astype(np.float64).copy()
    x, y = grid_xy(grid, height, width)
    z = np.asarray(dem, dtype=np.float64)
    nx, ny, nz = dem_normals(dem, grid.res_m)
    touched = np.zeros((height, width), dtype=bool)
    if params.enabled_frames and not poses_trusted:
        raise BubbleError("poses_trusted is false; refusing to project")
    enabled = [cam for cam in cameras if cam.name in params.enabled_frames]
    enabled.sort(key=lambda cam: cam.name)
    for cam in enabled:
        if cam.role != "fit":
            raise BubbleError(f"cannot enable {cam.role} camera '{cam.name}'")
        if cam.name not in images:
            raise BubbleError(f"missing image for '{cam.name}'")
        u, v, _depth, valid = project_points(cam, x, y, z)
        in_image = valid & (u >= 0) & (v >= 0) & (u <= cam.width - 1) & (v <= cam.height - 1)
        visible = visibility_mask(cam, x, y, z, dem, grid) & in_image
        to_x = cam.xyz[0] - x
        to_y = cam.xyz[1] - y
        to_z = cam.xyz[2] - z
        dist = np.maximum(np.sqrt(to_x * to_x + to_y * to_y + to_z * to_z), 1e-6)
        facing = np.clip((nx * to_x + ny * to_y + nz * to_z) / dist, 0, 1)
        if params.angle_power == 0:
            angle = np.where(facing > FRONT_CUTOFF, 1.0, 0.0)
        else:
            angle = np.where(facing > FRONT_CUTOFF, facing ** params.angle_power, 0.0)
        fade = distance_fade(dist, params.fade_start_m, params.fade_end_m)
        weight = params.opacity * angle * fade
        weight = np.where(visible, weight, 0.0)
        sampled = sample_rgb(images[cam.name], u, v)
        sampled = np.clip(sampled * params.luminance_gain + params.luminance_bias, 0, 255)
        use = weight >= 1e-6
        mixed = out * (1 - weight[..., None]) + sampled * weight[..., None]
        out = np.where(use[..., None], mixed, out)
        touched |= use
    texture = np.clip(np.rint(out), 0, 255).astype(np.uint8)
    return texture, touched

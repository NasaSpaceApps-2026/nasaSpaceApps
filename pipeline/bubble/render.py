from __future__ import annotations

import numpy as np

from bubble.geom import grid_xy, project_points, sample_rgb

MAGENTA = (255, 0, 255)


def sample_texture_uv(texture, u, v):
    height, width = texture.shape[:2]
    col = np.asarray(u, dtype=np.float64) * (width - 1)
    row = (1.0 - np.asarray(v, dtype=np.float64)) * (height - 1)
    return sample_rgb(texture, col, row)


def render_mesh(dem, texture, cam, grid, row0: int = 0, col0: int = 0, full_shape=None):
    height, width = dem.shape
    full_h, full_w = full_shape or (height, width)
    xs, ys = grid_xy(grid, height, width)
    u, v, depth, valid = project_points(cam, xs, ys, dem)
    image = np.zeros((cam.height, cam.width, 3), dtype=np.float64)
    image[:] = MAGENTA
    zbuf = np.full((cam.height, cam.width), np.inf)
    for r in range(height - 1):
        for c in range(width - 1):
            tris = (
                ((r, c), (r + 1, c), (r, c + 1)),
                ((r, c + 1), (r + 1, c), (r + 1, c + 1)),
            )
            for tri in tris:
                if any((not bool(valid[rr, cc])) or float(depth[rr, cc]) <= 0.05 for rr, cc in tri):
                    continue
                points = [
                    (float(u[rr, cc]), float(v[rr, cc]), float(depth[rr, cc]))
                    for rr, cc in tri
                ]
                uvs = [((col0 + cc) / (full_w - 1), 1 - (row0 + rr) / (full_h - 1)) for rr, cc in tri]
                _shade(image, zbuf, points[0], points[1], points[2], uvs[0], uvs[1], uvs[2], texture)
    return np.clip(np.rint(image), 0, 255).astype(np.uint8)


def _shade(image, zbuf, p0, p1, p2, uv0, uv1, uv2, texture):
    h, w = image.shape[:2]
    minx = max(int(np.floor(min(p0[0], p1[0], p2[0]))), 0)
    maxx = min(int(np.ceil(max(p0[0], p1[0], p2[0]))), w - 1)
    miny = max(int(np.floor(min(p0[1], p1[1], p2[1]))), 0)
    maxy = min(int(np.ceil(max(p0[1], p1[1], p2[1]))), h - 1)
    if minx > maxx or miny > maxy:
        return
    denom = (p1[1] - p2[1]) * (p0[0] - p2[0]) + (p2[0] - p1[0]) * (p0[1] - p2[1])
    if abs(denom) < 1e-12:
        return
    gx = np.arange(minx, maxx + 1, dtype=np.float64) + 0.5
    gy = np.arange(miny, maxy + 1, dtype=np.float64) + 0.5
    xx, yy = np.meshgrid(gx, gy)
    w0 = ((p1[1] - p2[1]) * (xx - p2[0]) + (p2[0] - p1[0]) * (yy - p2[1])) / denom
    w1 = ((p2[1] - p0[1]) * (xx - p2[0]) + (p0[0] - p2[0]) * (yy - p2[1])) / denom
    w2 = 1.0 - w0 - w1
    depth = w0 * p0[2] + w1 * p1[2] + w2 * p2[2]
    mask = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6) & (depth > 0.05)
    sub_z = zbuf[miny : maxy + 1, minx : maxx + 1]
    mask &= depth < sub_z
    if not np.any(mask):
        return
    uu = w0 * uv0[0] + w1 * uv1[0] + w2 * uv2[0]
    vv = w0 * uv0[1] + w1 * uv1[1] + w2 * uv2[1]
    colors = sample_texture_uv(texture, uu, vv)
    sub = image[miny : maxy + 1, minx : maxx + 1]
    sub[mask] = colors[mask]
    sub_z[mask] = depth[mask]

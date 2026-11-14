"""Terrain derivatives. Shared by the pipeline (to build layers) and tests (to build fixtures).

Definitions (these are what the layer catalogue describes; keep them in sync):
    slope_deg:  Horn (1981) 3x3 finite difference on the DEM grid, degrees. On the 2 m grid
                this is the "slope_2m" layer. NaN wherever any of the 3x3 neighbours is NaN.
    roughness:  plain standard deviation of elevation in a 5x5 window, metres (no plane
                removal, per PRD contract 3). NaN if any cell in the window is NaN.
"""
from __future__ import annotations

import numpy as np


def slope_deg(dem: np.ndarray, res_m: float) -> np.ndarray:
    z = np.pad(dem.astype(np.float64), 1, mode="edge")
    a, b, c = z[:-2, :-2], z[:-2, 1:-1], z[:-2, 2:]
    d, f = z[1:-1, :-2], z[1:-1, 2:]
    g, h, i = z[2:, :-2], z[2:, 1:-1], z[2:, 2:]
    dzdx = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * res_m)
    dzdy = ((g + 2 * h + i) - (a + 2 * b + c)) / (8 * res_m)
    s = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
    return s.astype(np.float32)   # NaNs propagate through the arithmetic


def roughness(dem: np.ndarray, window: int = 5) -> np.ndarray:
    from numpy.lib.stride_tricks import sliding_window_view
    p = window // 2
    z = np.pad(dem.astype(np.float64), p, mode="edge")
    w = sliding_window_view(z, (window, window))
    return w.std(axis=(-1, -2)).astype(np.float32)


def bilinear(arr: np.ndarray, row: float, col: float) -> float:
    """Bilinear sample at fractional (row, col) where integers are cell centres. NaN if any
    contributing cell is NaN or the point is outside the grid."""
    n_r, n_c = arr.shape
    if row < 0 or col < 0 or row > n_r - 1 or col > n_c - 1:
        return float("nan")
    r0, c0 = int(np.floor(row)), int(np.floor(col))
    r1, c1 = min(r0 + 1, n_r - 1), min(c0 + 1, n_c - 1)
    fr, fc = row - r0, col - c0
    v = (arr[r0, c0] * (1 - fr) * (1 - fc) + arr[r0, c1] * (1 - fr) * fc
         + arr[r1, c0] * fr * (1 - fc) + arr[r1, c1] * fr * fc)
    return float(v)

"""Terrain derivatives. Shared by the pipeline (to build layers) and tests (to build fixtures).

Layer: atlas_core, pure numpy functions on DEM arrays (row 0 = north, metres), used by sitebuild/ to write layers and by
visibility/illumination at query time.
References implemented: Horn (1981) slope/aspect; Zevenbergen & Thorne (1987) curvature; Weiss (2001) TPI; Riley et al.
(1999) TRI; local relief as max-min within a disc (cf. Evans 1972 relief measures).

Definitions (these are what the layer catalogue describes; keep them in sync):
    slope_deg:  Horn (1981) 3x3 finite difference on the DEM grid, degrees. On the 2 m grid
                this is the "slope_2m" layer. NaN wherever any of the 3x3 neighbours is NaN.
    roughness:  plain standard deviation of elevation in a 5x5 window, metres (no plane
                removal, per PRD contract 3). NaN if any cell in the window is NaN.
"""
from __future__ import annotations

import numpy as np


def slope_deg(dem: np.ndarray, res_m: float) -> np.ndarray:
    """Slope in degrees (float32) by the Horn (1981) 3x3 finite difference; edge-padded so border cells use replicated neighbours; NaN propagates from any NaN neighbour. `res_m` is the cell size in metres."""
    z = np.pad(dem.astype(np.float64), 1, mode="edge")
    a, b, c = z[:-2, :-2], z[:-2, 1:-1], z[:-2, 2:]
    d, f = z[1:-1, :-2], z[1:-1, 2:]
    g, h, i = z[2:, :-2], z[2:, 1:-1], z[2:, 2:]
    dzdx = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * res_m)
    dzdy = ((g + 2 * h + i) - (a + 2 * b + c)) / (8 * res_m)
    s = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
    return s.astype(np.float32)   # NaNs propagate through the arithmetic


def roughness(dem: np.ndarray, window: int = 5) -> np.ndarray:
    """Standard deviation of elevation (metres, float32) in a `window` x `window` square (default 5x5), no plane removal; edge-padded."""
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


# =====================================================================================
# Geomorphometry layers (sitebuild/geomorph.py builds them; tests/test_geomorph.py checks
# them on analytic surfaces). All grids: row 0 = north, col 0 = west, square cells of res_m.
# NaN = unknown. Windowed measures are NaN where the window is cut by the grid edge, so a
# value is never a silent under-estimate from a truncated window.
# =====================================================================================

def _nanpad(a: np.ndarray, p: int = 1) -> np.ndarray:
    return np.pad(a.astype(np.float64), p, mode="constant", constant_values=np.nan)


def horn_gradient(dem: np.ndarray, res_m: float):
    """Horn (1981) 3x3 gradient. Returns (dz/dx_east, dz/dy_north), both dimensionless (m/m).
    Border cells are NaN (no edge padding, unlike slope_deg), so aspect never rests on invented data."""
    z = _nanpad(dem)
    a, b, c = z[:-2, :-2], z[:-2, 1:-1], z[:-2, 2:]
    d, f = z[1:-1, :-2], z[1:-1, 2:]
    g, h, i = z[2:, :-2], z[2:, 1:-1], z[2:, 2:]
    dzdx = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * res_m)
    dz_north = ((a + 2 * b + c) - (g + 2 * h + i)) / (8 * res_m)    # row 0 is north
    return dzdx, dz_north


def aspect_deg(dem: np.ndarray, res_m: float, min_slope_deg: float = 1.0) -> np.ndarray:
    """Downslope direction, degrees clockwise from north in [0, 360) (Horn 1981). NaN where the
    slope is below min_slope_deg (aspect undefined on flat ground) or any 3x3 neighbour is NaN."""
    gx, gn = horn_gradient(dem, res_m)
    asp = np.degrees(np.arctan2(-gx, -gn)) % 360.0        # downslope = -gradient; (east, north) components
    slope = np.degrees(np.arctan(np.hypot(gx, gn)))
    asp[~(slope >= min_slope_deg)] = np.nan
    asp[asp >= 360.0] = 0.0
    return asp.astype(np.float32)


ASPECT_CLASSES = {0: "N", 1: "NE", 2: "E", 3: "SE", 4: "S", 5: "SW", 6: "W", 7: "NW", 8: "flat"}


def aspect_class(dem: np.ndarray, res_m: float, min_slope_deg: float = 1.0) -> np.ndarray:
    """int16 8-way aspect class (N = 337.5..22.5, ...), 8 = flat (slope < min_slope_deg), -1 = unknown."""
    gx, gn = horn_gradient(dem, res_m)
    slope = np.degrees(np.arctan(np.hypot(gx, gn)))
    asp = np.degrees(np.arctan2(-gx, -gn)) % 360.0
    cls = np.nan_to_num(np.floor(((asp + 22.5) % 360.0) / 45.0), nan=0).astype(np.int16) % 8
    out = np.where(slope >= min_slope_deg, cls, 8).astype(np.int16)
    out[~np.isfinite(slope)] = -1
    return out


def smooth_nan(dem: np.ndarray, sigma_m: float, res_m: float) -> np.ndarray:
    """Gaussian smoothing (truncated at 3 sigma). NaN wherever any cell of the kernel support is NaN or off-grid."""
    from scipy import ndimage
    s = sigma_m / res_m
    z = dem.astype(np.float64)
    ok = np.isfinite(z)
    sm = ndimage.gaussian_filter(np.where(ok, z, 0.0), s, mode="constant", cval=0.0, truncate=3.0)
    fr = ndimage.gaussian_filter(ok.astype(np.float64), s, mode="constant", cval=0.0, truncate=3.0)
    out = np.where(fr > 1 - 1e-9, sm, np.nan)
    return out.astype(np.float32) if dem.dtype != np.float64 else out


def curvature_zt(dem: np.ndarray, res_m: float, smooth_sigma_m: float = 5.0):
    """Profile and plan curvature (Zevenbergen and Thorne 1987) from the 3x3 partial-quartic surface,
    on a Gaussian-smoothed DEM (sigma smooth_sigma_m). Units 1/m (= 2a for z = a r^2).
    Sign convention, same for both: POSITIVE = convex (crest / spur / dome), NEGATIVE = concave
    (hollow / valley / bowl). (Z&T's raw plan formula has the opposite sign to the profile one; plan is flipped
    here so both read the same way.) Profile = along the downslope direction, plan = across it (contour curvature).
    NaN where the smoothed gradient is ~0 (flow direction undefined) or any input is NaN."""
    z = _nanpad(smooth_nan(dem, smooth_sigma_m, res_m) if smooth_sigma_m > 0 else dem)
    L = res_m
    z1, z2, z3 = z[:-2, :-2], z[:-2, 1:-1], z[:-2, 2:]
    z4, z5, z6 = z[1:-1, :-2], z[1:-1, 1:-1], z[1:-1, 2:]
    z7, z8, z9 = z[2:, :-2], z[2:, 1:-1], z[2:, 2:]
    D = ((z4 + z6) / 2 - z5) / L**2
    E = ((z2 + z8) / 2 - z5) / L**2
    F = (-z1 + z3 + z7 - z9) / (4 * L**2)
    G = (-z4 + z6) / (2 * L)
    H = (z2 - z8) / (2 * L)
    g2 = G**2 + H**2
    with np.errstate(divide="ignore", invalid="ignore"):
        prof = -2 * (D * G**2 + E * H**2 + F * G * H) / g2
        plan = -2 * (D * H**2 + E * G**2 - F * G * H) / g2
    bad = ~(g2 > 1e-12)
    prof[bad] = np.nan
    plan[bad] = np.nan
    return prof.astype(np.float32), plan.astype(np.float32)


def tri_riley(dem: np.ndarray) -> np.ndarray:
    """Terrain Ruggedness Index (Riley et al. 1999): sqrt of the sum of squared elevation differences
    between a cell and its 8 neighbours (metres). NaN if the cell or any neighbour is NaN / off-grid."""
    z = _nanpad(dem)
    c = z[1:-1, 1:-1]
    s = np.zeros_like(c)
    H, W = c.shape
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy == 0 and dx == 0:
                continue
            s += (z[1 + dy:1 + dy + H, 1 + dx:1 + dx + W] - c) ** 2
    return np.sqrt(s).astype(np.float32)


def disc_offsets(radius_m: float, res_m: float):
    """List of (dy, half_width_cells) rows of the disc of cells whose centres lie within radius_m of the centre."""
    R = radius_m / res_m
    r = int(np.floor(R + 1e-9))
    return r, [(dy, int(np.floor(np.sqrt(max(R * R - dy * dy, 0.0)) + 1e-9))) for dy in range(-r, r + 1)]


def disc_kernel(radius_m: float, res_m: float, exclude_center: bool = False) -> np.ndarray:
    """Boolean disc kernel of radius `radius_m` (metres) in cells of `res_m`; optionally without the centre cell."""
    r, rows = disc_offsets(radius_m, res_m)
    k = np.zeros((2 * r + 1, 2 * r + 1))
    for dy, w in rows:
        k[dy + r, r - w:r + w + 1] = 1.0
    if exclude_center:
        k[r, r] = 0.0
    return k


def edge_trunc_mask(n_rows: int, n_cols: int, radius_m: float, res_m: float) -> np.ndarray:
    """True where the disc of radius_m is cut by the grid edge."""
    r = int(np.floor(radius_m / res_m + 1e-9))
    m = np.zeros((n_rows, n_cols), bool)
    m[:r], m[n_rows - r:], m[:, :r], m[:, n_cols - r:] = True, True, True, True
    return m


def disc_extremes(dem: np.ndarray, radius_m: float, res_m: float):
    """(min, max) elevation within the disc around each cell, ignoring NaN cells. NaN where the centre is NaN or the
    disc is cut by the grid edge. Exact disc via per-row 1-D van Herk-style filters (no 2-D rank filter)."""
    from scipy import ndimage
    z = dem.astype(np.float32)
    ok = np.isfinite(z)
    r, rows = disc_offsets(radius_m, res_m)
    outs = []
    for sign in (1.0, -1.0):
        a = np.where(ok, sign * z, -np.inf).astype(np.float32)
        out = np.full(a.shape, -np.inf, np.float32)
        nR = a.shape[0]
        by_w: dict[int, list[int]] = {}
        for dy, w in rows:
            by_w.setdefault(w, []).append(dy)
        for w, dys in by_w.items():
            M = ndimage.maximum_filter1d(a, 2 * w + 1, axis=1, mode="constant", cval=-np.inf) if w > 0 else a
            for dy in dys:        # out[row] = max(M[row + dy])
                if dy >= 0:
                    np.maximum(out[:nR - dy], M[dy:], out=out[:nR - dy])
                else:
                    np.maximum(out[-dy:], M[:nR + dy], out=out[-dy:])
        outs.append(sign * out)
    zmax, zmin = outs[0], outs[1]
    bad = ~ok | edge_trunc_mask(*z.shape, radius_m, res_m)
    zmax[bad] = np.nan
    zmin[bad] = np.nan
    return zmin, zmax


def local_relief(dem: np.ndarray, radius_m: float, res_m: float) -> np.ndarray:
    """max - min elevation within the disc (metres)."""
    zmin, zmax = disc_extremes(dem, radius_m, res_m)
    return (zmax - zmin).astype(np.float32)


def disc_mean(values: np.ndarray, radius_m: float, res_m: float, exclude_center: bool = False,
              min_valid_frac: float = 0.9) -> np.ndarray:
    """Mean of values within the disc, NaN-aware (FFT convolution). NaN where the disc is cut by the grid edge or
    fewer than min_valid_frac of its cells are finite."""
    from scipy.signal import fftconvolve
    v = values.astype(np.float64)
    ok = np.isfinite(v)
    off = np.nanmean(v) if ok.any() else 0.0
    zz = np.where(ok, v - off, 0.0)
    k = disc_kernel(radius_m, res_m, exclude_center)
    S = fftconvolve(zz, k, mode="same")
    C = np.rint(fftconvolve(ok.astype(np.float64), k, mode="same"))
    with np.errstate(divide="ignore", invalid="ignore"):
        m = S / C + off
    m[(C < min_valid_frac * k.sum()) | edge_trunc_mask(*v.shape, radius_m, res_m)] = np.nan
    return m.astype(np.float32)


def tpi(dem: np.ndarray, radius_m: float, res_m: float) -> np.ndarray:
    """Topographic Position Index (Weiss 2001): cell elevation minus the mean of the other cells within the disc
    (metres). Positive = above its surroundings (crest / ridge), negative = below (hollow / valley floor)."""
    return (dem.astype(np.float32) - disc_mean(dem, radius_m, res_m, exclude_center=True)).astype(np.float32)


def disc_fraction(mask: np.ndarray, radius_m: float, res_m: float) -> np.ndarray:
    """Fraction of disc cells where mask is True (NaN where the disc is cut by the grid edge)."""
    return disc_mean(mask.astype(np.float32), radius_m, res_m, exclude_center=False, min_valid_frac=0.0)


def distance_to(mask: np.ndarray, res_m: float) -> np.ndarray:
    """Euclidean distance (m, centre to centre) from every cell to the nearest True cell of mask."""
    from scipy import ndimage
    if not mask.any():
        return np.full(mask.shape, np.nan, np.float32)
    return ndimage.distance_transform_edt(~mask, sampling=res_m).astype(np.float32)


def contact_mask(unit: np.ndarray) -> np.ndarray:
    """True for cells that touch (4-neighbourhood) a cell of a DIFFERENT known unit (both >= 0). Cells next to
    unknown (-1) are not contacts."""
    c = np.zeros(unit.shape, bool)
    for a, b in ((unit[:-1], unit[1:]), (unit[:, :-1], unit[:, 1:])):
        d = (a != b) & (a >= 0) & (b >= 0)
        if a.shape[0] != unit.shape[0]:
            c[:-1] |= d; c[1:] |= d
        else:
            c[:, :-1] |= d; c[:, 1:] |= d
    return c


def count_within(indicators, radius_m: float, res_m: float) -> np.ndarray:
    """Number of the boolean grids in `indicators` that have at least one True cell within radius_m (centre to
    centre) of each cell. NaN where the disc is cut by the grid edge."""
    n = None
    for ind in indicators:
        if not ind.any():
            hit = np.zeros(ind.shape, bool)
        else:
            hit = distance_to(ind, res_m) <= radius_m + 1e-6
        n = hit.astype(np.int16) if n is None else n + hit
    out = n.astype(np.float32)
    out[edge_trunc_mask(*out.shape, radius_m, res_m)] = np.nan
    return out

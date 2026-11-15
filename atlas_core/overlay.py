"""Raster helpers for image overlays (used by devapi.routes_overlay). Pure functions over a Site; nothing here changes
the behaviour of any other atlas_core module.

Every grid returned has the site's shape (row 0 = north, col 0 = west). `downsample_*` reduce a grid by an integer factor.
"""
from __future__ import annotations

import math

import numpy as np
from scipy import ndimage

from . import envelope as _env, illumination as _ill, route as _route, travel as _travel, visibility as _vis
from .errors import AtlasError
from .site import Site


# ---- downsampling -----------------------------------------------------------------------------------

def downsample_nearest(a: np.ndarray, f: int) -> np.ndarray:
    """Nearest-neighbour reduction by integer factor f (centre sample of each f x f block): for classes and tiers."""
    if f <= 1:
        return a
    o = f // 2
    return np.ascontiguousarray(a[o::f, o::f])


def downsample_mean(a: np.ndarray, f: int) -> np.ndarray:
    """Block mean by integer factor f. NaN cells are ignored (all-NaN block -> NaN). Booleans give a 0..1 fraction."""
    if f <= 1:
        return a.astype(np.float32) if a.dtype == bool else a
    n0, n1 = a.shape[0] // f, a.shape[1] // f
    b = a[:n0 * f, :n1 * f].reshape(n0, f, n1, f)
    if a.dtype == bool:
        return b.mean(axis=(1, 3), dtype=np.float32)
    fin = np.isfinite(b)
    s = np.where(fin, b, 0).sum(axis=(1, 3), dtype=np.float64)
    c = fin.sum(axis=(1, 3))
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(c > 0, s / np.maximum(c, 1), np.nan).astype(np.float32)


# ---- slope ------------------------------------------------------------------------------------------

def slope_classes(site: Site, profile: str) -> tuple[np.ndarray, dict]:
    """int8 grid: 0 safe, 1 caution, 2 no-go, -1 unknown, from slope_2m and the profile's caution/max slope. Also the params."""
    params, _ = _route.route_params(site, profile)
    s = site.layer("slope_2m").data
    out = np.full(s.shape, -1, np.int8)
    fin = np.isfinite(s)
    out[fin] = 0
    out[fin & (s > params["caution_slope_deg"])] = 1
    out[fin & (s > params["max_slope_deg"])] = 2
    return out, params


# ---- lake -------------------------------------------------------------------------------------------

def lake_depth_grid(site: Site, level_m: float) -> np.ndarray:
    """float32 grid of water depth (m) where elevation <= level and the cell is 4-connected to the lowest known cell (the
    same rule as lake.flood_at_level); NaN elsewhere."""
    z = site.layer("elevation").data
    known = np.isfinite(z)
    out = np.full(z.shape, np.nan, np.float32)
    if not known.any():
        return out
    below = known & (z <= level_m)
    r0, c0 = np.unravel_index(int(np.nanargmin(np.where(known, z, np.inf))), z.shape)
    if not below[r0, c0]:
        return out
    lab, _ = ndimage.label(below)
    water = lab == lab[r0, c0]
    out[water] = (level_m - z[water]).astype(np.float32)
    return out


# ---- sun --------------------------------------------------------------------------------------------

def sun_classes(site: Site, el_deg: float, az_deg: float) -> np.ndarray:
    """int8 grid using illumination's codes (LIT, CAST_SHADOW, SELF_SHADOW, UNKNOWN) for a sun at el/az; el <= 0 -> all
    known cells CAST_SHADOW is NOT assumed: callers handle night themselves."""
    dem = _ill._dem(site)
    zfill = _ill._filled(site)
    s = _ill.sun_vector(el_deg, az_deg)
    cast = _ill.cast_shadow_grid(zfill, site.res_m, el_deg, az_deg)
    ci = _ill.cos_incidence_grid(zfill, site.res_m, s)
    cls = np.where(ci <= 0, _ill.SELF_SHADOW, np.where(cast, _ill.CAST_SHADOW, _ill.LIT)).astype(np.int8)
    cls[~np.isfinite(dem) | ~np.isfinite(_ill._horn_gradients(dem, site.res_m)[0])] = _ill.UNKNOWN
    return cls


# ---- viewshed ---------------------------------------------------------------------------------------

def viewshed_mask(site: Site, x: float, y: float, observer_height_m: float = 1.7, radius_m: float = 3000.0,
                  curvature: bool = True) -> tuple[np.ndarray, dict]:
    """Boolean grid of cells visible from (x, y) (same algorithm as visibility.viewshed) and a stats dict."""
    h = _vis._num(observer_height_m, "observer_height_m", 0, 1000)
    R = _vis._num(radius_m, "radius_m", 10, _vis.MAX_RADIUS_M)
    if not site.in_bounds(x, y):
        raise AtlasError(f"Observer ({x:.0f}, {y:.0f}) is outside the site (+-{site.half_m:.0f} m).")
    dem = _vis._dem(site)
    z0 = _vis._elevation_at(site, x, y)
    if not math.isfinite(z0):
        raise AtlasError(f"The elevation at ({x:.0f}, {y:.0f}) is unknown (no DEM data).", hint="Pick a nearby point with data.")
    f = 2 if R > _vis.COARSE_ABOVE_M else 1
    x0, y0 = _vis._geom(site)
    vis, r_lo, c_lo, st = _vis._sweep(dem[::f, ::f], site.res_m * f, x0, y0, x, y, z0 + h, 0.0, R, curvature)
    n = site.size_px
    full = np.zeros((n, n), bool)
    if vis.size:
        if f == 1:
            full[r_lo:r_lo + vis.shape[0], c_lo:c_lo + vis.shape[1]] = vis
        else:
            up = np.repeat(np.repeat(vis, f, axis=0), f, axis=1)
            r0, c0 = r_lo * f, c_lo * f
            hh, ww = min(up.shape[0], n - r0), min(up.shape[1], n - c0)
            full[r0:r0 + hh, c0:c0 + ww] = up[:hh, :ww]
        full &= np.isfinite(dem)
    area = float(full.sum()) * site.res_m ** 2
    return full, {"visible_area_m2": area, "visible_km2": area / 1e6, "elevation_m": z0,
                  "farthest_visible_m": float(st["farthest_m"]), "known_cells_in_radius": int(st["known"]),
                  "visible_fraction": (float(vis.sum()) / st["known"]) if st["known"] else 0.0}


# ---- envelope ---------------------------------------------------------------------------------------

def envelope_one_way(site: Site, x: float, y: float, profile: str = "eva_walkable", budget_min: float | None = None,
                     reserve_fraction: float | None = None) -> tuple[np.ndarray, dict]:
    """float32 grid of one-way walk time (min) for cells that can be reached and returned from within the usable budget
    (out + back <= budget * (1 - reserve), isotropic model so back = out); NaN elsewhere. Also an info dict."""
    budget, reserve, _, usable, used = _env._resolve_budget(site, profile, budget_min, reserve_fraction, 0.0)
    if not site.in_bounds(x, y):
        raise AtlasError(f"Base ({x:.0f}, {y:.0f}) is outside the site (+-{site.half_m:.0f} m).")
    out_f, notes = _travel._field_and_notes(site, [{"x": float(x), "y": float(y)}], "walk_time_min", profile)
    limit = usable / 2.0
    res = np.where(np.isfinite(out_f) & (out_f <= limit), out_f, np.nan).astype(np.float32)
    return res, {"budget_min": budget, "reserve_fraction": reserve, "usable_min": usable, "one_way_limit_min": limit,
                 "values": used, "notes": notes}

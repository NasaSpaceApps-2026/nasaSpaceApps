"""Line of sight on Mars terrain: viewshed, point-to-point line_of_sight, find_vantage_points.

GEOMETRY (all three tools share it)
    Terrain is the 2 m `elevation` layer, bilinearly interpolated (a sample whose four neighbours are not
    all known is "unknown"). An observer's eye is `observer_height_m` above the terrain; a target point is
    `target_height_m` above the terrain. Point B is visible from A iff no terrain sample strictly between
    them rises above the straight sight line, after correcting for planetary curvature: a sample at
    distance d from the observer is lowered by d^2 / (2 R) (R = 3 396 190 m, Mars mean radius; the exact
    sphere/chord difference is d^4/(8R^3), under a micrometre at 10 km).  Atmospheric refraction is
    NEGLIGIBLE on Mars (surface pressure ~0.6 kPa, about 0.6 % of Earth's, so the refractivity is ~100x
    smaller than Earth's) and is not modelled.  A grazing ray (terrain exactly on the line) counts as clear.
    On a flat plane this gives the horizon distance sqrt(2 R h): 3.40 km for h = 1.7 m.

VIEWSHED ALGORITHM
    Radial sweep (rays from the observer), the classic "line-of-sight sweep" family described by
      Franklin & Ray (1994) "Higher isn't necessarily better: visibility algorithms and experiments",
      Proc. 6th Int. Symp. on Spatial Data Handling; and Izraelevitz (1987) / Wang, Robinson & White
      (2000) "Generating viewsheds without using sightlines", Photogramm. Eng. Remote Sens. 66(1).
    For each of K rays (K = ceil(2 pi R_max / cell) so that rays are at most one cell apart at the
    outer edge) the DEM is sampled every `cell` metres and the running maximum of the elevation angle
    (curvature-corrected slope from the eye) is kept: H[k, j] = max slope over samples 1..j.  A target
    cell at distance D is visible iff the slope to the cell's own elevation (+ target height) is >= the
    running maximum over the samples of its nearest ray that lie strictly closer than D.  The only
    approximation is that a cell's horizon is interpolated linearly in azimuth between its two neighbouring
    rays (rays are at most one cell apart at the outer edge) and the sampling interval along the ray (one cell).  Tests check it
    against an independent brute-force per-cell ray oracle.
    UNKNOWN cells block nothing (visibility behind a data hole is therefore optimistic) and are reported.
    Cells whose own elevation is unknown are never "visible".

PERFORMANCE
    3 km viewshed on the 2 m grid: ~1-2 s.  For radius > 3.5 km the 4 m lattice (every second DEM cell,
    point sampled) is used unless the caller forces otherwise: ~4x faster, with small features (< 8 m)
    sometimes missed; the result reports `grid_m`.  find_vantage_points always works on the 4 m lattice for
    the candidate search and re-checks its top candidates on the full 2 m DEM.

VANTAGE SEARCH
    Line of sight is reciprocal (A sees B iff B sees A when the heights are swapped), so the visibility of
    the target from EVERY candidate observer is one viewshed run *from the target point* with the
    target height as eye height and the observer height as target height.  A region target is represented
    by up to 12 spread sample cells (farthest-point sampling); the score of a candidate is the fraction
    of those samples it sees (the true fraction of the region would need every cell).  Candidates are the
    4 m lattice cells within `max_distance_m` of the target that pass `conditions` and `within`.
    Results are spread apart greedily, the best pool is re-scored by direct forward line of sight on the
    2 m DEM, and re-ranked.
"""
from __future__ import annotations

import copy
import math
import time

import numpy as np
from scipy.spatial import cKDTree

from . import travel
from .conditions import combine, compile_all, expand_aliases, merge_profile
from .errors import AtlasError
from .refs import resolve_ref
from .sets import SetStore, public_member
from .site import MARS_RADIUS_M, Site

R_MARS = MARS_RADIUS_M
MAX_RADIUS_M = 8000.0
COARSE_ABOVE_M = 3500.0           # auto-use the 4 m lattice beyond this radius
MAX_VANTAGE_SAMPLES = 12
MAX_LISTED = 25


# ------------------------------------------------------------------ small helpers

def horizon_distance_m(height_m: float, target_height_m: float = 0.0, radius_m: float = R_MARS) -> float:
    """Distance to the geometric horizon over a smooth sphere: sqrt(2 R h) (+ same for the target)."""
    return math.sqrt(2 * radius_m * max(height_m, 0.0)) + math.sqrt(2 * radius_m * max(target_height_m, 0.0))


def _num(v, name: str, lo: float, hi: float, unit: str = "m") -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float, np.integer, np.floating)) or not math.isfinite(v):
        raise AtlasError(f"{name} must be a number, got {v!r}.", hint=f"Give {name} in {unit}.")
    if not lo <= v <= hi:
        raise AtlasError(f"{name} = {v:g} is outside the allowed range {lo:g} to {hi:g} {unit}.",
                         hint=f"Use a value between {lo:g} and {hi:g}.")
    return float(v)


def _one(site: Site, store: SetStore | None, ref, what: str) -> dict:
    pts = resolve_ref(site, ref, store)
    if len(pts) != 1:
        raise AtlasError(f"`{what}` must be exactly one location, got {len(pts)}.",
                         hint='Use a point, "poi:<id>", "base", "rover", a marker, or ONE set member such as "3.2".')
    return pts[0]


def _bilinear(dem: np.ndarray, row, col) -> np.ndarray:
    """Bilinear interpolation at fractional (row, col) (integers = cell centres). NaN when outside the
    grid or when any of the four neighbours is NaN."""
    nr, nc = dem.shape
    row = np.asarray(row, np.float64)
    col = np.asarray(col, np.float64)
    inside = (row >= 0) & (row <= nr - 1) & (col >= 0) & (col <= nc - 1)
    r0 = np.clip(np.floor(row), 0, nr - 2).astype(np.int64)
    c0 = np.clip(np.floor(col), 0, nc - 2).astype(np.int64)
    fr = row - r0
    fc = col - c0
    z = ((1 - fr) * (1 - fc) * dem[r0, c0] + (1 - fr) * fc * dem[r0, c0 + 1]
         + fr * (1 - fc) * dem[r0 + 1, c0] + fr * fc * dem[r0 + 1, c0 + 1])
    return np.where(inside, z, np.nan)


def _elevation_at(site: Site, x: float, y: float) -> float:
    dem = site.layer("elevation").data
    row, col = site.xy_to_rc(x, y)
    z = float(_bilinear(dem, row, col))
    if not math.isfinite(z):
        r, c = site.rc_index(x, y)
        r, c = int(r), int(c)
        if 0 <= r < site.size_px and 0 <= c < site.size_px:
            z = float(dem[r, c])
    return z


def _dem(site: Site) -> np.ndarray:
    if "elevation" not in site.layers:
        raise AtlasError("This site has no elevation layer, so line of sight cannot be computed.",
                         hint="Line of sight needs the 2 m `elevation` layer.")
    return site.layers["elevation"].data


def _geom(site: Site):
    """x0, y0: site-frame metres of the centre of cell (0, 0)."""
    x0, y0 = site.rc_to_xy(0, 0)
    return float(x0), float(y0)


def _pt(site: Site, x: float, y: float, **extra) -> dict:
    lat, lon = site.xy_to_latlon(x, y)
    d = {"x": round(float(x), 2), "y": round(float(y), 2), "lat": round(lat, 5), "lon": round(lon, 5)}
    d.update(extra)
    return d


# ------------------------------------------------------------------ point-to-point line of sight

def _sight_profile(dem: np.ndarray, x0: float, y0: float, res: float, ax: float, ay: float, za: float,
                   bx: float, by: float, zb: float, step: float, curvature: bool = True):
    """Terrain samples strictly between A and B. za, zb are the eye / target ABSOLUTE elevations.
    Returns d (distance from A), terrain z, margin (sight line minus curvature-corrected terrain; < 0 means
    the terrain is in the way) and the total distance D."""
    D = math.hypot(bx - ax, by - ay)
    n = max(int(math.ceil(D / step)), 1)
    d = D * np.arange(1, n) / n
    if len(d) == 0:
        return d, d, d, D
    t = d / D
    x = ax + (bx - ax) * t
    y = ay + (by - ay) * t
    col = (x - x0) / res
    row = (y0 - y) / res
    z = _bilinear(dem, row, col)
    line = za + (zb - za) * t
    bulge = d * (D - d) / (2.0 * R_MARS) if curvature else 0.0
    return d, z, line - (z + bulge), D


def line_of_sight(site: Site, a, b, height_a: float = 1.7, height_b: float = 1.7, store: SetStore | None = None,
                  curvature: bool = True) -> dict:
    """Is B visible from A (and so A from B)? `a`, `b` are single location references; heights are the
    antenna / eye heights above the terrain at each end (m)."""
    ha = _num(height_a, "height_a", 0, 1000)
    hb = _num(height_b, "height_b", 0, 1000)
    dem = _dem(site)
    A = _one(site, store, a, "a")
    B = _one(site, store, b, "b")
    za0 = _elevation_at(site, A["x"], A["y"])
    zb0 = _elevation_at(site, B["x"], B["y"])
    for P, z, nm in ((A, za0, "a"), (B, zb0, "b")):
        if not math.isfinite(z):
            raise AtlasError(f"The elevation at `{nm}` ({P['x']:.0f}, {P['y']:.0f}) is unknown (no DEM data), "
                             "so line of sight cannot be evaluated from or to it.",
                             hint="Pick a nearby point with data (describe_location shows data quality).")
    x0, y0 = _geom(site)
    res = site.res_m
    step = res / 2.0
    d, z, margin, D = _sight_profile(dem, x0, y0, res, A["x"], A["y"], za0 + ha, B["x"], B["y"], zb0 + hb,
                                     step, curvature)
    known = np.isfinite(margin)
    unknown = int((~known).sum())
    assumptions = [
        {"what": f"height_a = {ha:g} m, height_b = {hb:g} m above terrain", "source": "user-specified or default"},
        {"what": f"terrain = 2 m elevation layer, sampled every {step:g} m, bilinear", "source": "default: algorithm setting"},
        {"what": f"curvature correction with R = {R_MARS:.0f} m" if curvature else "curvature ignored",
         "source": "default: Mars mean radius; refraction negligible (about 0.6 kPa atmosphere), not modelled"},
    ]
    notes = ["Geometric line of sight only: a real radio link also depends on the Fresnel zone clearance, antenna "
             "pattern, frequency, multipath and the roughness below the 2 m grid. None of that is modelled."]
    out = {"clear": True, "distance_m": round(D, 2),
           "a": _pt(site, A["x"], A["y"], id=A.get("id"), elevation_m=round(za0, 2), eye_elevation_m=round(za0 + ha, 2)),
           "b": _pt(site, B["x"], B["y"], id=B.get("id"), elevation_m=round(zb0, 2), eye_elevation_m=round(zb0 + hb, 2)),
           "assumptions": assumptions, "notes": notes}
    if known.any():
        i = int(np.nanargmin(np.where(known, margin, np.inf)))
        worst = float(margin[i])
        out["clear"] = bool(worst >= 0)
        px = A["x"] + (B["x"] - A["x"]) * d[i] / D
        py = A["y"] + (B["y"] - A["y"]) * d[i] / D
        t = d[i] / D
        line_z = za0 + ha + ((zb0 + hb) - (za0 + ha)) * t
        if worst < 0:
            out["blocking_point"] = _pt(site, px, py, distance_from_a_m=round(float(d[i]), 2),
                                        terrain_elevation_m=round(float(z[i]), 2),
                                        sight_line_elevation_m=round(float(line_z), 2),
                                        deficit_m=round(-worst, 3))
            out["clearance_deficit_m"] = round(-worst, 3)
            blocked = np.nonzero(known & (margin < 0))[0]
            out["blocked_span_m"] = [round(float(d[blocked[0]]), 2), round(float(d[blocked[-1]]), 2)]
            out["hint"] = (f"Blocked {d[i]:.0f} m from A by terrain {-worst:.2f} m above the sight line. Raise the "
                           f"antenna(s) (re-run with larger heights), move an end, or use find_vantage_points to find "
                           f"a spot that sees the target.")
        else:
            out["tightest_point"] = _pt(site, px, py, distance_from_a_m=round(float(d[i]), 2),
                                        terrain_elevation_m=round(float(z[i]), 2),
                                        sight_line_elevation_m=round(float(line_z), 2), clearance_m=round(worst, 3))
            out["min_clearance_m"] = round(worst, 3)
    out["profile"] = {"samples": int(len(d)), "step_m": round(D / max(len(d) + 1, 1), 3),
                      "unknown_samples": unknown,
                      "terrain_min_m": round(float(np.nanmin(z)), 2) if known.any() else None,
                      "terrain_max_m": round(float(np.nanmax(z)), 2) if known.any() else None,
                      "terrain_samples_above_line": int((known & (margin < 0)).sum()),
                      "elevation_difference_m": round(zb0 - za0, 2)}
    if unknown:
        notes.append(f"{unknown} of {len(d)} samples along the path have no DEM data and were treated as open, "
                     "so a 'clear' verdict is optimistic there.")
    if D == 0:
        out["clear"] = True
    return out


# ------------------------------------------------------------------ the viewshed core (radial sweep)

def _sweep(dem: np.ndarray, res: float, x0: float, y0: float, ox: float, oy: float, z_eye: float,
           target_h: float, radius: float, curvature: bool = True):
    """Visible mask of one observer on a lattice. dem[r, c] is at x = x0 + c*res, y = y0 - r*res.
    Returns (vis, r_lo, c_lo, stats) where vis is the bool window dem[r_lo: r_lo+h, c_lo: c_lo+w] and stats has
    known / unknown cell counts inside the radius and the farthest visible distance."""
    nr, nc = dem.shape
    ocol = (ox - x0) / res
    orow = (y0 - oy) / res
    rad_c = radius / res
    r_lo = max(int(math.floor(orow - rad_c)) - 1, 0)
    r_hi = min(int(math.ceil(orow + rad_c)) + 2, nr)
    c_lo = max(int(math.floor(ocol - rad_c)) - 1, 0)
    c_hi = min(int(math.ceil(ocol + rad_c)) + 2, nc)
    h, w = max(r_hi - r_lo, 0), max(c_hi - c_lo, 0)
    stats = {"known": 0, "unknown": 0, "farthest_m": 0.0, "far_xy": None}
    if h == 0 or w == 0:
        return np.zeros((0, 0), bool), r_lo, c_lo, stats

    rel = dem[r_lo:r_hi, c_lo:c_hi].astype(np.float64) - z_eye   # window, relative to the eye (precision)
    wocol, worow = ocol - c_lo, orow - r_lo         # observer in window coordinates
    step = res
    J = max(int(math.floor(radius / step)), 1)
    K = max(int(math.ceil(2 * math.pi * radius / res)), 64)
    dalpha = 2 * math.pi / K
    dist = (np.arange(1, J + 1) * step)
    drop = dist ** 2 / (2 * R_MARS) if curvature else np.zeros(J)

    H = np.empty((K, J), np.float64)
    chunk = max(int(1.5e6 // J), 1)
    for k0 in range(0, K, chunk):
        ang = np.arange(k0, min(k0 + chunk, K)) * dalpha
        col = wocol + np.outer(np.cos(ang), dist) / res
        row = worow - np.outer(np.sin(ang), dist) / res
        z = _bilinear(rel, row, col)
        s = (z - drop) / dist
        s = np.where(np.isfinite(s), s, -np.inf)          # unknown samples block nothing
        np.maximum.accumulate(s, axis=1, out=s)
        H[k0:k0 + len(ang)] = s

    Hflat = H.ravel()
    vis = np.zeros((h, w), bool)
    cols = np.arange(c_lo, c_hi)
    dx_row = (cols - ocol) * res
    strip = max(int(2.0e6 // max(w, 1)), 8)
    for s0 in range(0, h, strip):
        rows = np.arange(r_lo + s0, min(r_lo + s0 + strip, r_hi))
        dy = (orow - rows)[:, None] * res
        dx = dx_row[None, :]
        D = np.hypot(dx, dy)
        z = rel[rows[0] - r_lo:rows[-1] - r_lo + 1]
        known = np.isfinite(z)
        inside = D <= radius
        stats["known"] += int((inside & known).sum())
        stats["unknown"] += int((inside & ~known).sum())
        sel = np.nonzero((inside & known).ravel())[0]          # work only on known cells inside the radius
        v = np.zeros(inside.size, bool)
        if len(sel):
            dxs = np.broadcast_to(dx, inside.shape).ravel()[sel]
            dys = np.broadcast_to(dy, inside.shape).ravel()[sel]
            Ds = D.ravel()[sel]
            zs = z.ravel()[sel]
            pos = np.mod(np.arctan2(dys, dxs), 2 * math.pi) / dalpha
            k0 = np.floor(pos).astype(np.int64)
            fk = pos - k0
            k0 %= K
            k1 = (k0 + 1) % K
            idx = np.ceil(Ds / step).astype(np.int64) - 2          # last sample strictly closer than D
            flat = np.clip(idx, 0, J - 1)
            h0, h1 = Hflat[k0 * J + flat], Hflat[k1 * J + flat]
            # horizon between the two neighbouring rays: linear in azimuth (a ray with no blocker adds nothing)
            both = np.isfinite(h0) & np.isfinite(h1)
            Hv = np.where(both, h0 + fk * (np.where(both, h1, 0.0) - np.where(both, h0, 0.0)), np.maximum(h0, h1))
            Hv = np.where(idx < 0, -np.inf, Hv)
            Dsafe = np.where(Ds > 0, Ds, 1.0)
            dr = (Dsafe ** 2 / (2 * R_MARS)) if curvature else 0.0
            v[sel] = ((zs + target_h - dr) / Dsafe >= Hv) | (Ds == 0)
        v = v.reshape(inside.shape)
        vis[rows[0] - r_lo:rows[-1] - r_lo + 1] = v
        if v.any():
            Dv = np.where(v, D, -1.0)
            j = int(np.argmax(Dv))
            if Dv.flat[j] > stats["farthest_m"]:
                stats["farthest_m"] = float(Dv.flat[j])
                rr, cc = divmod(j, w)
                stats["far_xy"] = (x0 + (c_lo + cc) * res, y0 - (rows[0] + rr) * res)
    return vis, r_lo, c_lo, stats


# ------------------------------------------------------------------ viewshed

def _members_for_check(site: Site, store: SetStore, check) -> list[dict]:
    if check is None:
        return []
    return [public_member(m) for m in resolve_ref(site, check, store)]


def viewshed(site: Site, store: SetStore, at, observer_height_m: float = 1.7, target_height_m: float = 0.0,
             max_radius_m: float = 3000.0, curvature: bool = True, check=None, coarse: bool | None = None) -> dict:
    """What can an observer at `at` see? See the module docstring for the algorithm and assumptions.
    `check`: extra location refs (e.g. a set) whose visibility is reported individually."""
    t_start = time.perf_counter()
    h_o = _num(observer_height_m, "observer_height_m", 0, 1000)
    h_t = _num(target_height_m, "target_height_m", 0, 1000)
    R = _num(max_radius_m, "max_radius_m", 10, MAX_RADIUS_M)
    O = _one(site, store, at, "at")
    dem = _dem(site)
    z0 = _elevation_at(site, O["x"], O["y"])
    if not math.isfinite(z0):
        raise AtlasError(f"The elevation at ({O['x']:.0f}, {O['y']:.0f}) is unknown (no DEM data), so a viewshed "
                         "cannot be computed from there.", hint="Pick a nearby point with data.")
    res = site.res_m
    use_coarse = (R > COARSE_ABOVE_M) if coarse is None else bool(coarse)
    f = 2 if use_coarse else 1
    x0, y0 = _geom(site)
    sub = dem[::f, ::f]
    vis, r_lo, c_lo, st = _sweep(sub, res * f, x0, y0, O["x"], O["y"], z0 + h_o, h_t, R, curvature)

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
        # never claim a cell whose own elevation is unknown
        full &= np.isfinite(dem)
    cells = np.flatnonzero(full).astype(np.int32)
    cell_area = res * res
    vis_area = float(len(cells)) * cell_area
    known = max(st["known"], 1)
    vis_count = int(vis.sum()) if vis.size else 0
    frac = vis_count / known if st["known"] else 0.0
    lat, lon = site.xy_to_latlon(O["x"], O["y"])

    # bbox
    bbox = None
    if len(cells):
        rr, cc = cells // n, cells % n
        xs, ys = site.rc_to_xy(rr, cc)
        bbox = [float(xs.min()) - res / 2, float(ys.min()) - res / 2, float(xs.max()) + res / 2, float(ys.max()) + res / 2]
        member = {"x": float(O["x"]), "y": float(O["y"]), "lat": round(lat, 5), "lon": round(lon, 5),
                  "score": vis_area, "area_m2": vis_area, "bbox": bbox,
                  "attributes": {"observer_height_m": h_o, "max_radius_m": R, "visible_fraction": round(frac, 4)},
                  "_cells": cells}
    else:
        member = None
    query = {"tool": "viewshed", "at": copy.deepcopy(at) if not isinstance(at, np.ndarray) else None,
             "observer_height_m": h_o, "target_height_m": h_t, "max_radius_m": R, "curvature": curvature}
    s = store.create("regions", "viewshed", query, [member] if member else [])

    # named things inside the radius, tested exactly (full-resolution forward line of sight)
    def test(P) -> dict | None:
        dist = math.hypot(P["x"] - O["x"], P["y"] - O["y"])
        if dist > R:
            return None
        zp = _elevation_at(site, P["x"], P["y"])
        if not math.isfinite(zp):
            return {"id": P.get("id"), "name": P.get("name"), "distance_m": round(dist, 1), "visible": None,
                    "note": "elevation unknown at this point"}
        step = res / 2.0
        _, _, margin, _ = _sight_profile(dem, x0, y0, res, O["x"], O["y"], z0 + h_o, P["x"], P["y"], zp + h_t,
                                         step, curvature)
        ok = np.isfinite(margin)
        vis_ = bool(not ok.any() or float(np.min(margin[ok])) >= 0)
        d = {"id": P.get("id"), "name": P.get("name"), "type": P.get("type"), "x": round(P["x"], 1),
             "y": round(P["y"], 1), "distance_m": round(dist, 1), "visible": vis_}
        if not vis_:
            d["deficit_m"] = round(-float(np.min(margin[ok])), 2)
        return {k: v for k, v in d.items() if v is not None or k == "visible"}

    named = []
    for p in site.pois:
        named.append({"id": f"poi:{p['id']}", "name": p.get("name"), "type": p.get("type"), "x": p["x"], "y": p["y"]})
    for mid, m in (getattr(site, "markers", None) or {}).items():
        named.append({"id": f"marker:{mid}", "name": m.get("label"), "type": "marker", "x": m["x"], "y": m["y"]})
    checked = _members_for_check(site, store, check)
    results = []
    for P in named + checked:
        r = test(P)
        if r is not None:
            results.append(r)
    results.sort(key=lambda r: (r["distance_m"], str(r.get("id"))))
    vis_list = [r for r in results if r["visible"] is True]
    hid_list = [r for r in results if r["visible"] is False]

    horizon = horizon_distance_m(h_o, h_t)
    half_edge = site.half_m
    edge_limited = (abs(O["x"]) + R > half_edge) or (abs(O["y"]) + R > half_edge)
    notes = [
        "Visibility behind cells with no DEM data is optimistic (unknown cells block nothing).",
        f"On perfectly flat ground this observer would see out to {horizon:.0f} m (curvature horizon); beyond that "
        "only terrain that rises above the horizon curve can be seen.",
    ]
    if f == 2:
        notes.append("Computed on a 4 m lattice (every second DEM cell) for speed; features smaller than about 8 m "
                     "can be missed or added. Use max_radius_m <= 3500 for the full 2 m grid.")
    if edge_limited:
        notes.append("The radius reaches beyond the site edge; the fraction counts only cells inside the site.")
    if st["unknown"]:
        notes.append(f"{st['unknown']} cells inside the radius have no elevation data and are not counted as visible.")
    out = {
        "set_id": s.set_id,
        "observer": _pt(site, O["x"], O["y"], id=O.get("id"), elevation_m=round(z0, 2), eye_elevation_m=round(z0 + h_o, 2)),
        "visible_area_m2": round(vis_area, 1),
        "visible_fraction": round(frac, 4),
        "max_radius_m": R,
        "farthest_visible_m": round(st["farthest_m"], 1),
        "farthest_visible_point": (_pt(site, *st["far_xy"]) if st["far_xy"] else None),
        "bbox": bbox,
        "known_cells_in_radius": st["known"],
        "unknown_cells_in_radius": st["unknown"],
        "visible_cells": int(len(cells)),
        "grid_m": res * f,
        "flat_ground_horizon_m": round(horizon, 1),
        "visible_things": vis_list[:MAX_LISTED],
        "hidden_things": hid_list[:MAX_LISTED],
        "visible_things_count": len(vis_list),
        "hidden_things_count": len(hid_list),
        "assumptions": [
            {"what": f"observer eye {h_o:g} m above terrain", "source": "user-specified or default (about a standing astronaut)"},
            {"what": f"target height {h_t:g} m above terrain", "source": "user-specified or default (ground level)"},
            {"what": f"curvature correction, R = {R_MARS:.0f} m" if curvature else "curvature ignored",
             "source": "default: Mars mean radius; refraction negligible (about 0.6 kPa atmosphere), not modelled"},
            {"what": f"2 m elevation layer, radial sweep on a {res * f:g} m lattice", "source": "default: algorithm setting"},
        ],
        "notes": notes,
        "elapsed_s": round(time.perf_counter() - t_start, 2),
    }
    if len(cells):
        out["hint"] = (f"The visible area is stored as {s.set_id} (a region). Use it as a location: "
                       f"nearest(from_ref=\"<spot>\", to_ref=\"{s.set_id}\") to see how far a place is from it, or "
                       f"find_locations(... within=...) to combine it with other searches. Use "
                       f"find_vantage_points to find where a given place can be seen from.")
    else:
        out["hint"] = "Nothing is visible; check the observer position and radius."
    return out


# ------------------------------------------------------------------ vantage points

def _farthest_points(xy: np.ndarray, k: int) -> np.ndarray:
    """Indices of up to k spread-out points (greedy farthest-point sampling from the point nearest the centroid)."""
    n = len(xy)
    if n <= k:
        return np.arange(n)
    c = xy.mean(axis=0)
    first = int(np.argmin(((xy - c) ** 2).sum(axis=1)))
    chosen = [first]
    dmin = ((xy - xy[first]) ** 2).sum(axis=1)
    for _ in range(k - 1):
        j = int(np.argmax(dmin))
        chosen.append(j)
        dmin = np.minimum(dmin, ((xy - xy[j]) ** 2).sum(axis=1))
    return np.array(chosen)


def _target_samples(site: Site, members: list[dict], budget: int):
    """-> (sample xy array (n, 2), centroid (x, y), description)."""
    pts = []
    per = max(budget // max(len(members), 1), 1)
    n = site.size_px
    for m in members:
        cells = m.get("_cells")
        if cells is not None and len(cells) > 0:
            cells = np.asarray(cells, np.int64)
            x, y = site.rc_to_xy(cells // n, cells % n)
            xy = np.column_stack([x, y])
            pts.append(xy[_farthest_points(xy, per)])
        else:
            pts.append(np.array([[float(m["x"]), float(m["y"])]]))
    xy = np.vstack(pts)
    if len(xy) > budget:
        xy = xy[_farthest_points(xy, budget)]
    return xy, (float(xy[:, 0].mean()), float(xy[:, 1].mean()))


def find_vantage_points(site: Site, store: SetStore, target, observer_height_m: float = 1.7, within: dict | None = None,
                        conditions: list[dict] | None = None, limit: int = 5, target_height_m: float = 0.0,
                        max_distance_m: float = 1500.0, spacing_m: float | None = None, curvature: bool = True,
                        min_distance_m: float = 0.0) -> dict:
    t_start = time.perf_counter()
    h_o = _num(observer_height_m, "observer_height_m", 0, 1000)
    h_t = _num(target_height_m, "target_height_m", 0, 1000)
    maxd = _num(max_distance_m, "max_distance_m", 20, 4000)
    mind = _num(min_distance_m, "min_distance_m", 0, maxd)
    if isinstance(limit, bool) or not isinstance(limit, (int, np.integer)) or not 1 <= limit <= 50:
        raise AtlasError(f"limit must be an integer from 1 to 50, got {limit!r}.", hint="Use limit between 1 and 50.")
    limit = int(limit)
    dem = _dem(site)
    res = site.res_m
    members = [public_member(m) if "_cells" not in m else m for m in resolve_ref(site, target, store)]
    if not members:
        raise AtlasError("`target` resolved to no locations.", hint="Check that the set is not empty.")
    txy, (cx, cy) = _target_samples(site, members, MAX_VANTAGE_SAMPLES)
    x0, y0 = _geom(site)
    n = site.size_px
    f = 2
    lres = res * f
    spread = float(np.max(np.hypot(txy[:, 0] - cx, txy[:, 1] - cy))) if len(txy) else 0.0

    # candidate filters on the whole grid (same condition language as find_locations)
    conditions, alias_notes = expand_aliases(site, conditions)
    conds, _ = merge_profile(site, conditions, None)
    compiled = compile_all(site, conds) if conds else []
    allmask = combine(compiled, (n, n)) & np.isfinite(dem)
    diagnostics = [{"step": c.label, "pass_fraction": float(c.mask.mean())} for c in compiled]
    diagnostics.append({"step": "all conditions and known elevation", "pass_fraction": float(allmask.mean())})
    wlabel = None
    if within is not None:
        wmask, _field, wlabel = travel.within_mask(site, within, store)
        allmask &= wmask
        diagnostics.append({"step": "+ " + wlabel, "pass_fraction": float(allmask.mean())})

    # crop of the lattice around the target
    pad = maxd + spread + 4 * lres
    r_a = max(int(math.floor((y0 - (txy[:, 1].max() + pad)) / res)) // f * f, 0)
    r_b = min(int(math.ceil((y0 - (txy[:, 1].min() - pad)) / res)) + 1, n)
    c_a = max(int(math.floor(((txy[:, 0].min() - pad) - x0) / res)) // f * f, 0)
    c_b = min(int(math.ceil(((txy[:, 0].max() + pad) - x0) / res)) + 1, n)
    ldem = dem[r_a:r_b:f, c_a:c_b:f]
    lmask = allmask[r_a:r_b:f, c_a:c_b:f]
    lx0 = x0 + c_a * res
    ly0 = y0 - r_a * res
    lh, lw = ldem.shape
    LX = lx0 + np.arange(lw) * lres
    LY = ly0 - np.arange(lh) * lres
    XX, YY = np.meshgrid(LX, LY)
    # distance to the nearest target sample; candidates must be within max_distance of it
    dnear = np.full(XX.shape, np.inf)
    for sx, sy in txy:
        dnear = np.minimum(dnear, np.hypot(XX - sx, YY - sy))
    cand = lmask & (dnear <= maxd) & (dnear >= mind)
    diagnostics.append({"step": f"+ within {maxd:g} m of the target", "pass_fraction": float(cand.sum()) / max(allmask.size / (f * f), 1)})

    acc = np.zeros(XX.shape, np.float32)
    used = skipped = 0
    for sx, sy in txy:
        zt = _elevation_at(site, sx, sy)
        if not math.isfinite(zt):
            skipped += 1
            continue
        # reciprocity: the target is the eye (height h_t), candidate cells are the targets (height h_o)
        vis, r0, c0, _st = _sweep(ldem, lres, lx0, ly0, sx, sy, zt + h_t, h_o, maxd + spread + lres, curvature)
        if vis.size:
            acc[r0:r0 + vis.shape[0], c0:c0 + vis.shape[1]] += vis
        used += 1
    if used == 0:
        raise AtlasError("The elevation at the target is unknown (no DEM data), so visibility cannot be computed.",
                         hint="Pick a target with data.")
    frac = acc / used
    seen = cand & (frac > 0)
    diagnostics.append({"step": "+ sees at least part of the target", "pass_fraction": float(seen.sum()) / max(allmask.size / (f * f), 1)})

    assumptions = [
        {"what": f"observer eye {h_o:g} m above terrain", "source": "user-specified or default (standing astronaut)"},
        {"what": f"target height {h_t:g} m above terrain", "source": "user-specified or default (ground level)"},
        {"what": f"candidates on a {lres:g} m lattice within {maxd:g} m of the target", "source": "default: algorithm setting"},
        {"what": f"target represented by {used} spread sample point(s)", "source": "default: algorithm setting"},
        {"what": f"curvature correction, R = {R_MARS:.0f} m; refraction negligible" if curvature else "curvature ignored",
         "source": "default: Mars mean radius; refraction negligible"},
    ]
    for c in compiled:
        assumptions.append({"what": c.label, "source": c.source or "user-specified"})
    if wlabel:
        assumptions.append({"what": wlabel, "source": "user-specified"})
    notes = []
    if skipped:
        notes.append(f"{skipped} target sample point(s) have unknown elevation and were skipped.")
    if len(txy) == 1:
        notes.append("A point target is either seen or not (score 0 or 1), so spots that see it are ranked by height "
                     "(higher ground first), then distance. Use min_distance_m to keep them away from the target itself.")

    # rank, then spread greedily
    ii, jj = np.nonzero(seen)
    members_out: list[dict] = []
    pool_info = []
    if len(ii):
        fr = frac[ii, jj].astype(np.float64)
        px, py = LX[jj], LY[ii]
        dist = dnear[ii, jj]
        elev = ldem[ii, jj].astype(np.float64)
        # best fraction (0.05 bins) first, then higher ground ("overlooking"), then nearer
        order = np.lexsort((np.round(px, 3), np.round(py, 3), np.round(dist, 1), -np.round(elev, 0),
                            -np.round(np.round(fr / 0.05) * 0.05, 2)))
        sp = float(spacing_m) if spacing_m is not None else max(50.0, maxd / 10.0)
        if sp < 0:
            raise AtlasError("spacing_m must be >= 0.")
        tree = cKDTree(np.column_stack([px, py]))
        avail = np.ones(len(ii), bool)
        pool_n = min(max(limit * 4, 12), 200)
        pool = []
        for k in order:
            if not avail[k]:
                continue
            pool.append(int(k))
            if len(pool) >= pool_n:
                break
            avail[tree.query_ball_point([px[k], py[k]], sp)] = False
        # re-score the pool by direct forward line of sight on the full 2 m DEM
        sample_z = np.array([_elevation_at(site, sx, sy) for sx, sy in txy])
        ok_t = np.isfinite(sample_z)
        for k in pool:
            ox, oy = float(px[k]), float(py[k])
            zo = _elevation_at(site, ox, oy)
            if not math.isfinite(zo):
                continue
            seen_n = 0
            for (sx, sy), zt, okz in zip(txy, sample_z, ok_t):
                if not okz:
                    continue
                _, _, margin, _ = _sight_profile(dem, x0, y0, res, ox, oy, zo + h_o, sx, sy, zt + h_t, res / 2.0, curvature)
                good = np.isfinite(margin)
                if not good.any() or float(np.min(margin[good])) >= 0:
                    seen_n += 1
            vf = seen_n / max(int(ok_t.sum()), 1)
            pool_info.append((vf, float(dist[k]), ox, oy, zo, float(fr[k])))
        pool_info.sort(key=lambda t: (-round(round(t[0] / 0.05) * 0.05, 2), -round(t[4]), round(t[1], 1), t[2], t[3]))
        slope = site.layers["slope_2m"].data if "slope_2m" in site.layers else None
        zc = _elevation_at(site, cx, cy)
        for vf, dd, ox, oy, zo, cf in pool_info:
            r_i, c_i = site.rc_index(ox, oy)
            attrs = {"visible_fraction": round(vf, 3), "distance_m": round(dd, 1),
                     "elevation_m": round(zo, 2), "screening_fraction_4m_grid": round(cf, 3)}
            if math.isfinite(zc):
                attrs["elevation_above_target_m"] = round(zo - zc, 2)
            if slope is not None:
                sv = float(slope[int(r_i), int(c_i)])
                attrs["slope_2m"] = round(sv, 1) if math.isfinite(sv) else None
            lat, lon = site.xy_to_latlon(ox, oy)
            members_out.append({"x": ox, "y": oy, "lat": round(lat, 5), "lon": round(lon, 5), "score": round(vf, 3),
                                "name": f"viewpoint {dd:.0f} m from target", "attributes": attrs})
        members_out = [m for m in members_out if m["score"] > 0]
    query = {"tool": "find_vantage_points", "target": copy.deepcopy(target) if not isinstance(target, np.ndarray) else None,
             "observer_height_m": h_o, "target_height_m": h_t, "within": copy.deepcopy(within),
             "conditions": copy.deepcopy(conds), "max_distance_m": maxd, "min_distance_m": mind, "limit": limit}
    s = store.create("spots", "find_vantage_points", query, members_out)
    if s.count:
        best = s.members[0]
        hint = (f"Best: {best['id']} sees {best['score']:.0%} of the target from {best['attributes']['distance_m']:.0f} m. "
                f"Check the route with plan_route(goal=\"{best['id']}\") or the sight line with line_of_sight; "
                f"all {s.count} stored as {s.set_id}.")
    else:
        if not cand.any():
            hint = ("No candidate spot passes the conditions/within limit inside max_distance_m of the target. "
                    "Relax the conditions (see diagnostics) or raise max_distance_m.")
        else:
            hint = ("No candidate within max_distance_m sees the target. Raise max_distance_m, relax conditions or "
                    "raise observer_height_m.")
    out = {"set_id": s.set_id, "count": s.count,
           "results": [copy.deepcopy(public_member(m)) for m in s.members[:limit]],
           "target": {"x": round(cx, 2), "y": round(cy, 2), "samples": used,
                      "ids": [m.get("id") for m in members][:10]},
           "objective": "max visible fraction of the target (5% bins), then higher ground, then nearer", "assumptions": assumptions,
           "diagnostics": diagnostics, "notes": notes, "hint": hint,
           "elapsed_s": round(time.perf_counter() - t_start, 2)}
    if alias_notes:
        out["alias_notes"] = alias_notes
    return out

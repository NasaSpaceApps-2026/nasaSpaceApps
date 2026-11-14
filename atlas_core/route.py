"""Route planning, science traverse, elevation profiles and rover pose helpers (PRD "Route planner",
"Science traverse", "Traveller profiles"). Additive module: it reuses the planning grid, cost and
snapping of atlas_core.travel so that routes agree with nearest()/within walk metrics.

Cost: seconds per metre, 1 / speed(slope), speed = flat_speed * exp(-3.5 * tan(slope)) (a PLACEHOLDER
Tobler-shaped curve, isotropic). Cells above the profile's max_slope_deg (or the max_slope_deg override)
are impassable; no-data cells are impassable. Roughness is not in the cost (travel.py does not use it).

Profile extras (all optional inside profile.travel; defaults are labelled "assumed" in `assumptions`):
    caution_slope_deg (15), eva_limit_min (480), reserve_fraction (0.2)
"""
from __future__ import annotations

import math

import numpy as np
from skimage.graph import MCP_Geometric

from . import travel as _travel
from .errors import AtlasError
from .refs import resolve_ref
from .sets import SetStore
from .site import TIER_NAMES, Site
from .terrain import bilinear

SPACING_M = 5.0
DEFAULTS = {"caution_slope_deg": 15.0, "eva_limit_min": 480.0, "reserve_fraction": 0.2}
SCIENCE_TYPES = ("science_target", "sample_site", "landmark")
DEFAULT_SCIENCE_VALUE = 0.5
MAX_CANDIDATES = 20


# ---- parameters ------------------------------------------------------------------------------

def route_params(site: Site, profile: str, max_slope_deg: float | None = None) -> tuple[dict, list[str]]:
    """Travel parameters for `profile` (+ optional slope override). Returns (params, assumptions)."""
    t = dict(_travel._travel_params(site, profile))
    assumptions: list[str] = []
    t["profile_max_slope_deg"] = float(t["max_slope_deg"])
    if max_slope_deg is not None:
        if isinstance(max_slope_deg, bool) or not isinstance(max_slope_deg, (int, float)) \
                or not math.isfinite(max_slope_deg) or not 0 < max_slope_deg < 90:
            raise AtlasError(f"max_slope_deg must be a number between 0 and 90, got {max_slope_deg!r}.",
                             hint="Use degrees, e.g. 15.")
        t["max_slope_deg"] = float(max_slope_deg)
    for k, v in DEFAULTS.items():
        if k not in t:
            t[k] = v
            assumptions.append(f"{k} = {v:g} [assumed]")
        else:
            t[k] = float(t[k])
    t["caution_slope_deg"] = min(t["caution_slope_deg"], t["max_slope_deg"])
    t["profile"] = profile
    return t, assumptions


def speed_mps(params: dict, slope_deg) -> np.ndarray:
    return float(params["flat_speed_mps"]) * np.exp(-3.5 * np.tan(np.radians(slope_deg)))


# ---- terrain helpers -------------------------------------------------------------------------

def ground_slope(site: Site, x: float, y: float) -> float:
    """slope_2m of the containing cell; NaN if unknown or outside the site."""
    arr = site.layer("slope_2m").data
    r, c = site.rc_index(x, y)
    r, c = int(r), int(c)
    if not (0 <= r < arr.shape[0] and 0 <= c < arr.shape[1]):
        return float("nan")
    return float(arr[r, c])


def terrain_z(site: Site, x: float, y: float) -> float:
    """Elevation (bilinear). If a neighbour is NaN fall back to the containing cell. NaN if unknown."""
    arr = site.layer("elevation").data
    row, col = site.xy_to_rc(x, y)
    v = bilinear(arr, float(row), float(col))
    if math.isfinite(v):
        return v
    r, c = site.rc_index(x, y)
    r, c = int(r), int(c)
    if 0 <= r < arr.shape[0] and 0 <= c < arr.shape[1] and math.isfinite(float(arr[r, c])):
        return float(arr[r, c])
    return float("nan")


def pose_at(site: Site, x: float, y: float, heading_deg: float,
            wheelbase_m: float = 1.5, track_m: float = 1.2) -> tuple[float, float, float]:
    """Terrain-following pose from four wheel samples: (z, pitch_deg, roll_deg).
    Heading is degrees clockwise from north. Positive pitch = nose up; positive roll = right side up.
    z is the mean wheel height (NaN if no wheel is on known ground; pitch/roll are 0 if any is unknown)."""
    h = math.radians(heading_deg)
    fx, fy = math.sin(h), math.cos(h)
    rx, ry = math.cos(h), -math.sin(h)
    hl, hw = wheelbase_m / 2, track_m / 2
    z = {}
    for name, (a, b) in {"fl": (hl, -hw), "fr": (hl, hw), "bl": (-hl, -hw), "br": (-hl, hw)}.items():
        z[name] = terrain_z(site, x + a * fx + b * rx, y + a * fy + b * ry)
    vals = list(z.values())
    finite = [v for v in vals if math.isfinite(v)]
    if not finite:
        return float("nan"), 0.0, 0.0
    if len(finite) < 4:
        return float(np.mean(finite)), 0.0, 0.0
    pitch = math.degrees(math.atan2((z["fl"] + z["fr"]) / 2 - (z["bl"] + z["br"]) / 2, wheelbase_m))
    roll = math.degrees(math.atan2((z["fr"] + z["br"]) / 2 - (z["fl"] + z["bl"]) / 2, track_m))
    return float(np.mean(vals)), pitch, roll


def hazard_class(slope_deg: float, params: dict) -> str:
    """safe | caution | no-go | unknown for a ground slope under `params`."""
    if not math.isfinite(slope_deg):
        return "unknown"
    if slope_deg > params["max_slope_deg"]:
        return "no-go"
    if slope_deg > params["caution_slope_deg"]:
        return "caution"
    return "safe"


# ---- elevation profile -----------------------------------------------------------------------

def elevation_profile(site: Site, store: SetStore, points, spacing_m: float | None = None,
                      max_samples: int = 400) -> dict:
    """Elevation and slope along a polyline of location references (at least 2)."""
    refs = resolve_ref(site, points, store) if isinstance(points, list) else resolve_ref(site, [points], store)
    if len(refs) < 2:
        raise AtlasError("A profile needs at least 2 points.", hint='Pass points=[{"x":0,"y":0},{"x":100,"y":50}].')
    xy = np.array([[float(p["x"]), float(p["y"])] for p in refs])
    seg = np.hypot(*np.diff(xy, axis=0).T)
    total = float(seg.sum())
    if total <= 0:
        raise AtlasError("All profile points are the same place.", hint="Give points that are apart.")
    spacing = float(spacing_m) if spacing_m else max(site.res_m, 5.0)
    if spacing <= 0:
        raise AtlasError("spacing_m must be > 0.")
    spacing = max(spacing, total / (max_samples - 1))
    n = int(math.ceil(total / spacing)) + 1
    d = np.linspace(0.0, total, n)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    xs = np.interp(d, cum, xy[:, 0])
    ys = np.interp(d, cum, xy[:, 1])
    samples = []
    for di, x, y in zip(d, xs, ys):
        z, s = terrain_z(site, x, y), ground_slope(site, x, y)
        samples.append({"d": round(float(di), 2), "x": round(float(x), 2), "y": round(float(y), 2),
                        "z": round(z, 2) if math.isfinite(z) else None,
                        "slope_deg": round(s, 2) if math.isfinite(s) else None})
    zs = [s["z"] for s in samples if s["z"] is not None]
    sl = [s["slope_deg"] for s in samples if s["slope_deg"] is not None]
    dz = np.diff(zs) if len(zs) > 1 else np.array([0.0])
    unknown = sum(1 for s in samples if s["z"] is None or s["slope_deg"] is None)
    return {
        "distance_m": round(total, 2), "spacing_m": round(float(d[1] - d[0]), 2), "count": len(samples),
        "min_z": min(zs) if zs else None, "max_z": max(zs) if zs else None,
        "ascent_m": round(float(dz[dz > 0].sum()), 2), "descent_m": round(float(-dz[dz < 0].sum()), 2),
        "max_slope_deg": max(sl) if sl else None,
        "mean_slope_deg": round(float(np.mean(sl)), 2) if sl else None,
        "unknown_samples": unknown, "samples": samples,
    }


# ---- planning --------------------------------------------------------------------------------

def _single(site: Site, store: SetStore | None, ref, what: str) -> dict:
    pts = resolve_ref(site, ref, store)
    if len(pts) != 1:
        raise AtlasError(f"`{what}` must be one location, but it resolved to {len(pts)}.",
                         hint='Pass a point, a POI id, "marker:<id>", or one set member such as "3.1".')
    return pts[0]


def _centres(plan) -> tuple[np.ndarray, np.ndarray]:
    """x, y (m) of every planning cell centre, as (m, m) arrays."""
    ii = np.arange(plan.m)
    k, n = plan.k, plan.n
    rc = ii * k + (np.minimum(k, n - ii * k) - 1) / 2.0     # cell-centre row/col in analysis cells
    x, _ = plan.site.rc_to_xy(0, rc)
    _, y = plan.site.rc_to_xy(rc, 0)
    return np.broadcast_to(x[None, :], (plan.m, plan.m)), np.broadcast_to(y[:, None], (plan.m, plan.m))


def _resample(pts: list[tuple[float, float]], spacing: float) -> list[tuple[float, float]]:
    out = [pts[0]]
    for a, b in zip(pts[:-1], pts[1:]):
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        if L < 1e-9:
            continue
        n = max(1, int(math.ceil(L / spacing)))
        for k in range(1, n + 1):
            t = k / n
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out


def _fmt_pt(site: Site, p: dict) -> dict:
    z = terrain_z(site, p["x"], p["y"])
    d = {"id": p.get("id"), "x": round(float(p["x"]), 2), "y": round(float(p["y"]), 2),
         "z": round(z, 2) if math.isfinite(z) else None,
         "lat": p.get("lat"), "lon": p.get("lon")}
    if p.get("name"):
        d["name"] = p["name"]
    return d


def _infeasible(site, params, assumptions, start, goal, reason, nearest, warnings, route_id, hint) -> dict:
    return {
        "route_id": route_id, "feasible": False, "reason": reason, "nearest_reachable": nearest,
        "points": [], "distance_m": 0.0, "duration_min": 0.0, "ascent_m": 0.0, "max_slope_deg": None,
        "hazard_cells": 0, "consumables": None, "stops": [], "warnings": warnings,
        "assessment": {"go": False, "reasons": [reason], "caution": []},
        "profile": params["profile"], "max_slope_limit_deg": params["max_slope_deg"],
        "caution_slope_deg": params["caution_slope_deg"], "start": _fmt_pt(site, start),
        "goal": _fmt_pt(site, goal), "assumptions": assumptions, "hint": hint,
        "notes": [_travel.PLACEHOLDER_NOTE],
    }


def _nearest_reachable(site, plan, cost, cx, cy, goal, t_scale):
    reach = np.isfinite(cost)
    if not reach.any():
        return None
    d = np.where(reach, np.hypot(cx - goal["x"], cy - goal["y"]), np.inf)
    i, j = np.unravel_index(int(np.argmin(d)), d.shape)
    x, y = float(cx[i, j]), float(cy[i, j])
    lat, lon = site.xy_to_latlon(x, y)
    z = terrain_z(site, x, y)
    return {"x": round(x, 2), "y": round(y, 2), "z": round(z, 2) if math.isfinite(z) else None,
            "lat": round(lat, 5), "lon": round(lon, 5),
            "distance_to_goal_m": round(float(d[i, j]), 1),
            "travel_min": round(float(cost[i, j]) * t_scale, 1)}


def plan_route(site: Site, store: SetStore | None, start, goal, via=None, profile: str = "rover_scout",
               max_slope_deg: float | None = None, time_budget_min: float | None = None,
               route_id: str | None = None, dwell_min: float = 0.0) -> dict:
    """Least-cost route start -> via... -> goal. Returns the PRD route response:
    {route_id, feasible, reason?, nearest_reachable?, points[{x,y,z}] (~5 m spacing), distance_m (horizontal),
     duration_min, ascent_m, max_slope_deg, hazard_cells (planning cells above the caution slope),
     consumables{eva_limit_min, reserve_fraction, used{minutes, fraction}, margin{minutes, fraction}, ...},
     stops[{id, x, y, z, distance_m, eta_min, kind}], warnings[], assessment{go, reasons, caution}, ...}
    A goal or via point on ground above the slope limit (or with no data), or cut off from the start,
    gives feasible False with the reason and the nearest reachable point. A start on unwalkable ground is
    moved to the nearest walkable cell (up to 20 m) with a warning."""
    params, assumptions = route_params(site, profile, max_slope_deg)
    s_pt = _single(site, store, start, "start")
    g_pt = _single(site, store, goal, "goal")
    v_pts = [_single(site, store, v, "via") for v in (via or [])]
    if time_budget_min is not None and (not isinstance(time_budget_min, (int, float)) or time_budget_min <= 0):
        raise AtlasError("time_budget_min must be a number > 0.", hint="Use minutes, e.g. 120.")

    plan = _travel._Plan(site, params)
    cx, cy = _centres(plan)
    cost_grid = plan.costs("walk_time_min", params)
    t_scale = plan.scale("walk_time_min")
    limit = params["max_slope_deg"]
    warnings: list[str] = []

    # walkability of via/goal (their own analysis cell must be known and within the slope limit)
    wps = [s_pt] + v_pts + [g_pt]
    names = ["start"] + [f"via point {k + 1}" for k in range(len(v_pts))] + ["goal"]
    snaps = []
    for k, (p, nm) in enumerate(zip(wps, names)):
        sl = ground_slope(site, p["x"], p["y"])
        walk = math.isfinite(sl) and sl <= limit
        snap = plan.snap(p["x"], p["y"])
        if k > 0 and not walk:
            if not math.isfinite(sl):
                reason = (f"The {nm} ({p['x']:.0f}, {p['y']:.0f}) has no terrain data (unknown ground); "
                          "unknown ground is never treated as safe.")
            else:
                reason = (f"The {nm} ({p['x']:.0f}, {p['y']:.0f}) is on {sl:.0f} degree ground; the "
                          f"{profile} slope limit is {limit:g} degrees.")
            prev = snaps[-1]
            nearest = None
            if prev is not None:
                mcp = MCP_Geometric(cost_grid, fully_connected=True)
                cost, _ = mcp.find_costs([prev])
                nearest = _nearest_reachable(site, plan, cost, cx, cy, p, t_scale)
            hint = ("Pick a goal on flatter ground" + (f" such as the nearest reachable point "
                    f"({nearest['x']:.0f}, {nearest['y']:.0f}), {nearest['distance_to_goal_m']:.0f} m away"
                    if nearest else "") + f", or raise max_slope_deg above {limit:g} if the rover can take it.")
            return _infeasible(site, params, assumptions, s_pt, g_pt, reason, nearest, warnings, route_id, hint)
        if snap is None:
            if k == 0:
                reason = f"No walkable ground within {_travel.SNAP_RADIUS_M:.0f} m of the start."
            else:
                reason = f"No walkable ground within {_travel.SNAP_RADIUS_M:.0f} m of the {nm}."
            return _infeasible(site, params, assumptions, s_pt, g_pt, reason, None, warnings, route_id,
                               "Choose locations on walkable ground (see sample_terrain).")
        snaps.append((snap[0], snap[1]))
        if k == 0 and snap[2] > 0:
            warnings.append(f"The start is not on walkable ground; the route begins at the nearest walkable "
                            f"cell {snap[2]:.0f} m away.")

    # leg by leg
    xy_pts: list[tuple[float, float]] = []
    leg_ends: list[int] = []        # index into xy_pts of each waypoint after the start
    xy_pts.append((float(s_pt["x"]), float(s_pt["y"])))
    for k in range(len(wps) - 1):
        a, b = snaps[k], snaps[k + 1]
        mcp = MCP_Geometric(cost_grid, fully_connected=True)
        cost, _ = mcp.find_costs([a], [b])
        if not np.isfinite(cost[b]):
            p = wps[k + 1]
            nearest = _nearest_reachable(site, plan, cost, cx, cy, p, t_scale)
            leg = "" if len(wps) == 2 else f" (leg {k + 1} of {len(wps) - 1})"
            reason = (f"No {profile} route exists from the {names[k]} to the {names[k + 1]}{leg} with slopes "
                      f"under {limit:g} degrees: the {names[k + 1]} is cut off by steeper or unknown ground.")
            hint = (f"Nearest reachable point is ({nearest['x']:.0f}, {nearest['y']:.0f}), "
                    f"{nearest['distance_to_goal_m']:.0f} m from the {names[k + 1]}. " if nearest else "")
            hint += f"Raise max_slope_deg above {limit:g} or choose a different goal."
            return _infeasible(site, params, assumptions, s_pt, g_pt, reason, nearest, warnings, route_id, hint)
        cells = mcp.traceback(b)
        leg_pts = [plan.cell_centre_xy(int(i), int(j)) for i, j in cells]
        leg_start = (float(wps[k]["x"]), float(wps[k]["y"]))
        leg_goal = (float(wps[k + 1]["x"]), float(wps[k + 1]["y"]))
        # the path runs through planning-cell centres; begin and end at the real positions
        seg = _resample([leg_start] + leg_pts + [leg_goal], SPACING_M)
        xy_pts.extend(seg[1:])            # seg[0] is the junction already in xy_pts
        leg_ends.append(len(xy_pts) - 1)

    if len(xy_pts) == 1:
        xy_pts = [xy_pts[0], xy_pts[0]]
        warnings.append("Start and goal are the same place; nothing to drive.")
        leg_ends = [1] * (len(wps) - 1)

    P = np.array(xy_pts)
    # dense samples (<= ~1 m) for slope, duration and hazard accounting
    dense_x, dense_y, dense_seg = [P[0, 0]], [P[0, 1]], [0]
    for i in range(len(P) - 1):
        L = float(np.hypot(*(P[i + 1] - P[i])))
        n = max(1, int(math.ceil(L / 1.0)))
        t = np.linspace(0, 1, n + 1)[1:]
        dense_x.extend(P[i, 0] + (P[i + 1, 0] - P[i, 0]) * t)
        dense_y.extend(P[i, 1] + (P[i + 1, 1] - P[i, 1]) * t)
        dense_seg.extend([i + 1] * n)
    dx, dy, dseg = np.array(dense_x), np.array(dense_y), np.array(dense_seg)
    ds = np.hypot(np.diff(dx), np.diff(dy))
    r, c = site.rc_index(dx, dy)
    slope_arr = site.layer("slope_2m").data
    inb = (r >= 0) & (r < slope_arr.shape[0]) & (c >= 0) & (c < slope_arr.shape[1])
    sl = np.full(len(dx), np.nan)
    sl[inb] = slope_arr[r[inb], c[inb]]
    sl_mid = np.fmax(sl[:-1], sl[1:])
    unknown_n = int(np.isnan(sl).sum())
    sl_fill = np.where(np.isnan(sl_mid), limit, sl_mid)
    dt_min = ds / speed_mps(params, sl_fill) / 60.0
    distance = float(ds.sum())
    duration = float(dt_min.sum())
    max_slope = float(np.nanmax(sl)) if np.isfinite(sl).any() else None
    zs = np.array([terrain_z(site, x, y) for x, y in P])
    dz = np.diff(zs[np.isfinite(zs)]) if np.isfinite(zs).sum() > 1 else np.array([0.0])
    ascent = float(dz[dz > 0].sum())
    pi = np.minimum(np.maximum(r, 0) // plan.k, plan.m - 1)
    pj = np.minimum(np.maximum(c, 0) // plan.k, plan.m - 1)
    cells = set(zip(pi.tolist(), pj.tolist()))
    hazard_cells = sum(1 for (i, j) in cells if plan.slope[i, j] > params["caution_slope_deg"])

    # stops (via points and goal) with cumulative distance / ETA
    cum_d = np.concatenate([[0.0], np.cumsum(ds)])
    cum_t = np.concatenate([[0.0], np.cumsum(dt_min)])
    # dense index of each waypoint = last dense index whose segment index <= leg end index
    stops = []
    for k, ei in enumerate(leg_ends):
        di = int(np.max(np.nonzero(dseg == ei)[0])) if (dseg == ei).any() else len(dx) - 1
        p = wps[k + 1]
        st = _fmt_pt(site, p)
        st.update({"distance_m": round(float(cum_d[di]), 1), "eta_min": round(float(cum_t[di]), 1),
                   "kind": "goal" if k == len(leg_ends) - 1 else "via"})
        stops.append(st)

    # consumables (time-based): used = driving + dwell, margin = what remains above the reserve
    eva, reserve = params["eva_limit_min"], params["reserve_fraction"]
    used_min = duration + dwell_min
    used_frac = used_min / eva
    margin_frac = 1.0 - used_frac - reserve
    consumables = {
        "eva_limit_min": eva, "reserve_fraction": reserve,
        "used": {"minutes": round(used_min, 1), "fraction": round(used_frac, 4)},
        "margin": {"minutes": round(margin_frac * eva, 1), "fraction": round(margin_frac, 4)},
        "driving_min": round(duration, 1), "stops_min": round(float(dwell_min), 1),
        "basis": "time only (no power or oxygen model); limits from the profile or assumed",
    }

    reasons, caution = [], []
    if margin_frac < 0:
        reasons.append(f"Uses {used_frac * 100:.0f}% of the {eva:g} min limit, which breaks the "
                       f"{reserve * 100:.0f}% reserve (margin {margin_frac * eva:.0f} min).")
    if time_budget_min is not None and used_min > time_budget_min:
        reasons.append(f"Takes {used_min:.0f} min, over the {time_budget_min:g} min budget.")
    if max_slope is not None and max_slope > params["profile_max_slope_deg"]:
        reasons.append(f"Max slope {max_slope:.0f} degrees exceeds the {profile} no-go limit of "
                       f"{params['profile_max_slope_deg']:g} degrees (route was planned with a looser override).")
    if hazard_cells:
        caution.append(f"{hazard_cells} planning cell(s) (4 m) are steeper than the {params['caution_slope_deg']:g} "
                       "degree caution slope.")
    if unknown_n:
        caution.append(f"{unknown_n} sample(s) along the path fall on unknown ground (start/goal hop).")
    warnings.extend(reasons)
    warnings.extend(caution)
    assessment = {"go": not reasons, "reasons": reasons, "caution": caution}

    return {
        "route_id": route_id, "feasible": True, "reason": None, "nearest_reachable": None,
        "points": [{"x": round(float(x), 2), "y": round(float(y), 2),
                    "z": round(float(z), 2) if math.isfinite(z) else None} for (x, y), z in zip(P, zs)],
        "distance_m": round(distance, 1), "duration_min": round(duration, 1), "ascent_m": round(ascent, 1),
        "max_slope_deg": round(max_slope, 1) if max_slope is not None else None,
        "hazard_cells": hazard_cells, "consumables": consumables, "stops": stops, "warnings": warnings,
        "assessment": assessment, "profile": profile, "max_slope_limit_deg": limit,
        "caution_slope_deg": params["caution_slope_deg"], "start": _fmt_pt(site, s_pt),
        "goal": _fmt_pt(site, g_pt), "assumptions": assumptions,
        "notes": [_travel.PLACEHOLDER_NOTE, "Distances are horizontal; times are approximate "
                  "(4 m planning grid, isotropic speed)."],
    }


# ---- science traverse ------------------------------------------------------------------------

def _poi_matches(site: Site, poi: dict, interests: list[str]) -> list[str]:
    text = " ".join(str(poi.get(k) or "") for k in ("id", "name", "type", "description")).lower()
    out = []
    for it in interests:
        low = it.lower().strip()
        if not low:
            continue
        if low in text:
            out.append(f"'{it}' in POI text")
            continue
        lay = site.layers.get(low)
        r, c = site.rc_index(poi["x"], poi["y"])
        r, c = int(r), int(c)
        inside = lay is not None and 0 <= r < lay.data.shape[0] and 0 <= c < lay.data.shape[1]
        if inside and lay.kind == "evidence" and int(lay.data[r, c]) >= 2:
            out.append(f"{low} evidence: {TIER_NAMES[int(lay.data[r, c])]}")
    # categorical class names (e.g. "crater_floor")
    for lay in site.layers.values():
        if lay.kind != "categorical" or not lay.classes:
            continue
        r, c = site.rc_index(poi["x"], poi["y"])
        r, c = int(r), int(c)
        if 0 <= r < lay.data.shape[0] and 0 <= c < lay.data.shape[1]:
            name = lay.classes.get(int(lay.data[r, c]))
            if name and any(it.lower().strip() == name.lower() for it in interests):
                out.append(f"unit {name}")
    return out


def science_traverse(site: Site, store: SetStore | None, start, time_budget_min: float,
                     interests: list[str] | None = None, profile: str = "eva_walkable",
                     max_slope_deg: float | None = None, stop_minutes: float = 15.0,
                     return_to_start: bool = True, max_stops: int | None = None,
                     route_id: str | None = None) -> dict:
    """Greedy insertion (PRD "Science traverse"): among the (up to) 20 highest-value matching POIs, add the
    stop with the best science value per added minute until the time budget, including stop time
    (`stop_minutes` each) and the return to start, is used. Missing POI science_value is treated as 0.5
    and flagged (`value_assumed`). Returns {stops, route, science_value_total, time, skipped, notes, hint}."""
    if not isinstance(time_budget_min, (int, float)) or isinstance(time_budget_min, bool) or time_budget_min <= 0:
        raise AtlasError("time_budget_min must be a number > 0.", hint="Use minutes, e.g. 240 for a 4 h walk.")
    if stop_minutes < 0:
        raise AtlasError("stop_minutes must be >= 0.")
    interests = [i for i in (interests or []) if isinstance(i, str)]
    params, assumptions = route_params(site, profile, max_slope_deg)
    s_pt = _single(site, store, start, "start")
    plan = _travel._Plan(site, params)
    cost_grid = plan.costs("walk_time_min", params)
    t_scale = plan.scale("walk_time_min")
    notes = [_travel.PLACEHOLDER_NOTE]
    s_snap = plan.snap(s_pt["x"], s_pt["y"])
    if s_snap is None:
        raise AtlasError(f"No walkable ground within {_travel.SNAP_RADIUS_M:.0f} m of the start.",
                         hint="Choose a start on walkable ground.")

    # candidates
    pool = []
    for p in site.pois:
        if p.get("x") is None or p.get("y") is None or p.get("type") == "base":
            continue
        if interests:
            m = _poi_matches(site, p, interests)
            if not m:
                continue
        else:
            if p.get("type") not in SCIENCE_TYPES:
                continue
            m = []
        sv = p.get("science_value")
        assumed = not isinstance(sv, (int, float)) or isinstance(sv, bool)
        pool.append({"poi": p, "value": DEFAULT_SCIENCE_VALUE if assumed else float(sv),
                     "value_assumed": assumed, "matched_by": m})
    pool.sort(key=lambda c: (-c["value"], str(c["poi"].get("id"))))
    pool = pool[:MAX_CANDIDATES]
    if any(c["value_assumed"] for c in pool):
        notes.append(f"Some POIs have no science_value; {DEFAULT_SCIENCE_VALUE} was assumed for them "
                     "(marked value_assumed).")
    skipped: list[dict] = []
    nodes = [{"cell": (s_snap[0], s_snap[1])}]
    cands = []
    for c in pool:
        p = c["poi"]
        sl = ground_slope(site, p["x"], p["y"])
        snap = plan.snap(p["x"], p["y"])
        if not (math.isfinite(sl) and sl <= params["max_slope_deg"]) or snap is None:
            skipped.append({"id": f"poi:{p['id']}", "reason": "not on walkable ground"})
            continue
        c["node"] = len(nodes)
        nodes.append({"cell": (snap[0], snap[1])})
        cands.append(c)
    fields = []
    for nd in nodes:
        mcp = MCP_Geometric(cost_grid, fully_connected=True)
        cost, _ = mcp.find_costs([nd["cell"]])
        fields.append(cost * t_scale)
    T = np.array([[fields[a][nodes[b]["cell"]] for b in range(len(nodes))] for a in range(len(nodes))])
    reachable = []
    for c in cands:
        if np.isfinite(T[0, c["node"]]) and np.isfinite(T[c["node"], 0]):
            reachable.append(c)
        else:
            skipped.append({"id": f"poi:{c['poi']['id']}", "reason": "no walkable path from the start"})

    def seq_time(seq):
        return sum(T[a, b] for a, b in zip(seq[:-1], seq[1:])) + stop_minutes * (len(seq) - (2 if return_to_start else 1))

    def greedy(limit_stops):
        seq = [0, 0] if return_to_start else [0]
        chosen: list[dict] = []
        rest = list(reachable)
        while rest and (limit_stops is None or len(chosen) < limit_stops):
            base = seq_time(seq)
            best = None
            for c in rest:
                for pos in range(1, len(seq) + (0 if return_to_start else 1)):
                    trial = seq[:pos] + [c["node"]] + seq[pos:]
                    t = seq_time(trial)
                    if not math.isfinite(t) or t > time_budget_min:
                        continue
                    ratio = c["value"] / max(t - base, 1e-6)
                    if best is None or ratio > best[0] + 1e-12:
                        best = (ratio, c, trial)
            if best is None:
                break
            _, c, seq = best
            chosen.append(c)
            rest.remove(c)
        return seq, chosen

    seq, chosen = greedy(max_stops)
    route = None
    while chosen:
        order = [c for n in seq[1:] for c in chosen if c["node"] == n]
        if return_to_start:
            goal, vias = s_pt, [f"poi:{c['poi']['id']}" for c in order]
        else:
            goal, vias = f"poi:{order[-1]['poi']['id']}", [f"poi:{c['poi']['id']}" for c in order[:-1]]
        route = plan_route(site, store, s_pt, goal, vias, profile, max_slope_deg, None, route_id,
                           dwell_min=stop_minutes * len(order))
        if not route["feasible"]:
            notes.append("The joined route could not be planned: " + str(route["reason"]))
            drop = chosen[-1]
            skipped.append({"id": f"poi:{drop['poi']['id']}", "reason": "joined route infeasible"})
        elif route["consumables"]["used"]["minutes"] > time_budget_min:
            drop = chosen[-1]
            skipped.append({"id": f"poi:{drop['poi']['id']}",
                            "reason": "dropped: planned route exceeds the budget once resampled"})
        else:
            break
        chosen.remove(drop)
        seq = [n for n in seq if n != drop["node"]]
        route = None
    if route is None:
        route = plan_route(site, store, s_pt, s_pt, None, profile, max_slope_deg, None, route_id)
        order = []
    stops_out, dwell_so_far = [], 0.0
    for k, c in enumerate(order):
        p = c["poi"]
        st = route["stops"][k] if route and k < len(route["stops"]) else {}
        arrive = (st.get("eta_min") or 0.0) + dwell_so_far
        dwell_so_far += stop_minutes
        stops_out.append({"id": f"poi:{p['id']}", "name": p.get("name"), "type": p.get("type"),
                          "x": round(float(p["x"]), 2), "y": round(float(p["y"]), 2),
                          "science_value": c["value"], "value_assumed": c["value_assumed"],
                          "matched_by": c["matched_by"], "arrive_min": round(arrive, 1),
                          "stop_min": stop_minutes})
    travel_min = route["duration_min"] if route else 0.0
    total = travel_min + stop_minutes * len(order)
    recorded = [s for s in stops_out if not s["value_assumed"]]
    all_assumed = bool(stops_out) and not recorded
    # a summed "science value" is only reported over stops that have a recorded value; when every stop's value is
    # assumed there is nothing to sum and the stops were ordered by travel time alone
    value = None if all_assumed else round(sum(s["science_value"] for s in recorded), 3)
    weighting = None
    if all_assumed:
        weighting = (f"No stop has a recorded science value, so every candidate counted equally ({DEFAULT_SCIENCE_VALUE} assumed). "
                     "Stops were chosen and ordered by travel time only, with no science weighting; this is not a ranking of "
                     "the best science targets. Use interests= to narrow by text, mineral layer or unit, or max_stops to cap the count.")
        notes.append(weighting)
    elif stops_out and len(recorded) < len(stops_out):
        weighting = (f"{len(stops_out) - len(recorded)} of {len(stops_out)} stops have an assumed value ({DEFAULT_SCIENCE_VALUE}); "
                     "science_value_total sums only the stops with a recorded value.")
        notes.append(weighting)
    if not order:
        hint = ("No stop fits the budget or matches: try a larger time_budget_min, different interests, or "
                "search_pois to see candidates." if pool or interests else
                "This site has no science POIs; use find_sites or find_regions to pick targets, then plan_route.")
    else:
        hint = "Call assess_route(route_id) for go/no-go and the consumables margin, then follow_route."
    return {
        "stops": stops_out, "route": route, "science_value_total": value, "science_weighting_note": weighting,
        "time": {"travel_min": round(travel_min, 1), "stops_min": round(stop_minutes * len(order), 1),
                 "total_min": round(total, 1), "budget_min": float(time_budget_min),
                 "return_to_start": bool(return_to_start)},
        "candidates_considered": len(pool), "skipped": skipped, "interests": interests,
        "assumptions": assumptions, "notes": notes, "hint": hint,
    }

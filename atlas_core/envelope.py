"""EVA safety envelope and return-margin check.

eva_envelope: every cell an astronaut can reach from `base` AND walk back from (to the nearest haven) with
    walk_out + walk_back + stops <= eva_limit * (1 - reserve_fraction).
    The travel model is ISOTROPIC (uphill costs the same as downhill), so the walk back along the same path
    takes the same time: with a single haven out-and-back = 2 x walk time. With several havens (a rover or shelter
    at a location) the return leg uses the nearest haven: T(base->cell) + T(nearest haven->cell).
check_eva_plan: for each point of a route, remaining walk-back time to the nearest haven against the budget;
    flags the first point where elapsed + walk_back exceeds the usable budget (the "point of no return").

Travel times reuse atlas_core.travel (4 m planning grid, placeholder Tobler-shaped speed, 8-connected paths) and
route.plan_route; they are approximate and every profile value is printed with its cited/assumed label.
"""
from __future__ import annotations

import math

import numpy as np

from . import route as R
from . import travel as T
from .errors import AtlasError
from .refs import resolve_ref
from .sets import SetStore
from .site import Site

SECTORS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
DEFAULT_EVA_LIMIT_MIN = 480.0
DEFAULT_RESERVE = 0.2
MAX_CELLS_STORED = 4_000_000
MODEL_NOTE = ("Out-and-back time = 2 x one-way walk time because the travel model is isotropic (uphill costs the same "
              "as downhill) and the walk back is assumed to retrace the shortest path. Times are approximate (4 m "
              "planning grid, 8-connected paths, placeholder Tobler-shaped speed curve, not measured suit data).")


def _raw_val(d: dict, *keys):
    for k in keys:
        if k in d:
            v = d[k]
            if isinstance(v, dict) and "value" in v:
                return k, v["value"], v.get("source", "unspecified")
            return k, v, "unspecified"
    return None


def profile_values(site: Site, profile: str) -> dict:
    """Speed, slope limits, EVA limit and reserve of `profile`, each {value, unit, source} with the profile's
    own label ("assumed" or a citation) or "default (assumed)" when the profile does not define it."""
    prof = site.profiles.get(profile)
    travel = prof.get("travel") if isinstance(prof, dict) else None
    if not travel:
        T._travel_params(site, profile)       # raises the standard readable error
    out: dict = {}

    def put(name, unit, value, source):
        out[name] = {"value": value, "unit": unit, "source": source}

    for name, unit in (("flat_speed_mps", "m/s"), ("max_slope_deg", "degrees"), ("caution_slope_deg", "degrees")):
        g = _raw_val(travel, name)
        if g:
            put(name, unit, float(g[1]), g[2])
    g = _raw_val(travel, "eva_limit_min", "eva_limit_h")
    if g:
        put("eva_limit_min", "minutes", float(g[1]) * (60.0 if g[0] == "eva_limit_h" else 1.0),
            g[2] + (" (profile gives hours)" if g[0] == "eva_limit_h" else ""))
    else:
        put("eva_limit_min", "minutes", DEFAULT_EVA_LIMIT_MIN, "default (assumed)")
    g = _raw_val(travel, "reserve_fraction", "reserve_margin")
    if g:
        put("reserve_fraction", "fraction", float(g[1]), g[2])
    else:
        put("reserve_fraction", "fraction", DEFAULT_RESERVE, "default (assumed)")
    return out


def _resolve_budget(site, profile, budget_min, reserve_fraction, stops_min):
    pv = profile_values(site, profile)
    used = {k: dict(v) for k, v in pv.items()}
    if budget_min is None:
        budget = pv["eva_limit_min"]["value"]
    else:
        if isinstance(budget_min, bool) or not isinstance(budget_min, (int, float)) or not math.isfinite(budget_min) \
                or budget_min <= 0:
            raise AtlasError(f"budget_min must be a number > 0, got {budget_min!r}.", hint="Use minutes, e.g. 480.")
        budget = float(budget_min)
        used["eva_limit_min"] = {"value": budget, "unit": "minutes", "source": "user-specified"}
    if reserve_fraction is None:
        reserve = pv["reserve_fraction"]["value"]
    else:
        if isinstance(reserve_fraction, bool) or not isinstance(reserve_fraction, (int, float)) \
                or not 0 <= reserve_fraction < 1:
            raise AtlasError(f"reserve_fraction must be a number in [0, 1), got {reserve_fraction!r}.",
                             hint="Use e.g. 0.2 for a 20% reserve.")
        reserve = float(reserve_fraction)
        used["reserve_fraction"] = {"value": reserve, "unit": "fraction", "source": "user-specified"}
    if isinstance(stops_min, bool) or not isinstance(stops_min, (int, float)) or stops_min < 0:
        raise AtlasError(f"stops_min must be a number >= 0, got {stops_min!r}.")
    usable = budget * (1.0 - reserve)
    if stops_min >= usable:
        raise AtlasError(f"stops_min ({stops_min:g}) leaves no walking time within {usable:g} usable minutes.",
                         hint="Reduce stops_min or raise budget_min.")
    return budget, reserve, float(stops_min), usable, used


def _fields(site, base_pts, haven_pts, profile):
    out_f, n1 = T._field_and_notes(site, base_pts, "walk_time_min", profile)
    if haven_pts is None:
        return out_f, out_f, n1
    back_f, n2 = T._field_and_notes(site, haven_pts, "walk_time_min", profile)
    return out_f, back_f, n1 + [x for x in n2 if x not in n1]


def eva_envelope(site: Site, store: SetStore, base, budget_min: float | None = None,
                 reserve_fraction: float | None = None, profile: str = "eva_walkable", stops_min: float = 0.0,
                 havens=None) -> dict:
    budget, reserve, stops, usable, used = _resolve_budget(site, profile, budget_min, reserve_fraction, stops_min)
    bpts = resolve_ref(site, base, store)
    if len(bpts) != 1:
        raise AtlasError(f"`base` must be one location, but it resolved to {len(bpts)}.",
                         hint='Pass "base", a POI id, a point, or one set member such as "3.1".')
    b = bpts[0]
    hpts = resolve_ref(site, havens, store) if havens is not None else None
    if havens is not None and not hpts:
        raise AtlasError("`havens` resolved to no locations.")
    out_f, back_f, notes = _fields(site, [b], hpts, profile)
    total = out_f.astype(np.float64) + back_f.astype(np.float64) + stops
    mask = np.isfinite(total) & (total <= usable)
    n, res = site.size_px, site.res_m
    result: dict = {
        "base": {"id": b.get("id"), "x": b["x"], "y": b["y"], "lat": b.get("lat"), "lon": b.get("lon")},
        "havens": ([{"id": h.get("id"), "x": h["x"], "y": h["y"]} for h in hpts] if hpts else "the base only"),
        "profile": profile, "budget_min": round(budget, 2), "reserve_fraction": reserve,
        "usable_min": round(usable, 2), "stops_min": stops,
        "values": used,
        "method": ("reachable if walk_out + walk_back + stops <= budget x (1 - reserve); " +
                   ("single haven: walk_back = walk_out, so the limit is one-way time <= "
                    f"{(usable - stops) / 2:.1f} min" if hpts is None else
                    "return leg measured to the NEAREST haven")),
        "notes": [MODEL_NOTE] + notes,
    }
    if hpts is None:
        result["one_way_limit_min"] = round((usable - stops) / 2.0, 2)
    if not mask.any():
        result.update({"area_m2": 0.0, "area_km2": 0.0, "max_one_way_radius_m": 0.0, "sectors": [], "set_id": None,
                       "hint": "Nothing is reachable and recoverable: the base may be on unwalkable ground, or the "
                               "budget is too small. Raise budget_min or lower reserve_fraction/stops_min."})
        return result
    idx = np.flatnonzero(mask)
    rr, cc = idx // n, idx % n
    xs, ys = site.rc_to_xy(rr, cc)
    dx, dy = xs - b["x"], ys - b["y"]
    dist = np.hypot(dx, dy)
    bearing = (np.degrees(np.arctan2(dx, dy)) + 360.0) % 360.0
    sec = np.floor((bearing + 22.5) / 45.0).astype(int) % 8
    area = float(len(idx)) * res * res
    sectors = []
    for k, name in enumerate(SECTORS):
        sel = np.flatnonzero(sec == k)
        if not len(sel):
            sectors.append({"sector": name, "reachable": False, "farthest_straight_m": 0.0})
            continue
        j = sel[int(np.argmax(dist[sel]))]
        lat, lon = site.xy_to_latlon(float(xs[j]), float(ys[j]))
        sectors.append({"sector": name, "reachable": True, "farthest_straight_m": round(float(dist[j]), 1),
                        "x": round(float(xs[j]), 1), "y": round(float(ys[j]), 1),
                        "lat": round(lat, 5), "lon": round(lon, 5),
                        "walk_out_min": round(float(out_f[rr[j], cc[j]]), 1),
                        "walk_back_min": round(float(back_f[rr[j], cc[j]]), 1)})
    jm = int(np.argmax(dist))
    wo = out_f[rr, cc]
    member = {"x": float(b["x"]), "y": float(b["y"]), "lat": b.get("lat"), "lon": b.get("lon"), "score": area,
              "area_m2": area, "bbox": [float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())],
              "attributes": {"budget_min": budget, "reserve_fraction": reserve, "profile": profile}}
    if len(idx) <= MAX_CELLS_STORED:
        member["_cells"] = idx.astype(np.int32)
    else:
        result["notes"].append("The envelope is too large to store as a cell set; the stored region has its bounding box only.")
    s = store.create("regions", "eva_envelope", {"base": base, "budget_min": budget, "reserve_fraction": reserve,
                                                 "profile": profile, "stops_min": stops, "havens": havens}, [member])
    edge = bool(mask[0].any() or mask[-1].any() or mask[:, 0].any() or mask[:, -1].any())
    result.update({
        "set_id": s.set_id, "area_m2": round(area, 1), "area_km2": round(area / 1e6, 4), "reachable_cells": int(len(idx)),
        "max_one_way_radius_m": round(float(dist[jm]), 1),
        "max_one_way_walk_min": round(float(wo.max()), 1),
        "farthest_point": {"x": round(float(xs[jm]), 1), "y": round(float(ys[jm]), 1)},
        "sectors": sectors, "touches_site_edge": edge,
    })
    if edge:
        result["notes"].append("The envelope reaches the edge of the site window, so radius and area are LOWER BOUNDS.")
    return result


def _elapsed_minutes(site: Site, pts: list[dict], params: dict) -> np.ndarray:
    """Cumulative walking minutes along the points (speed at the segment mid-point slope; unknown slope is
    costed at the no-go slope, conservatively)."""
    t = np.zeros(len(pts))
    for i in range(1, len(pts)):
        a, c = pts[i - 1], pts[i]
        ds = math.hypot(c["x"] - a["x"], c["y"] - a["y"])
        sl = R.ground_slope(site, (a["x"] + c["x"]) / 2, (a["y"] + c["y"]) / 2)
        if not math.isfinite(sl):
            sl = params["max_slope_deg"]
        v = float(R.speed_mps(params, min(sl, params["max_slope_deg"])))
        t[i] = t[i - 1] + ds / v / 60.0
    return t


def check_eva_plan(site: Site, store: SetStore, route: dict | None = None, points=None,
                   profile: str = "eva_walkable", budget_min: float | None = None,
                   reserve_fraction: float | None = None, stops_min: float = 0.0, havens=None) -> dict:
    """`route`: a stored route dict (with "points"); or `points`: location refs [start, (via...), end] that are
    routed on foot with plan_route. havens default to the first point of the route (the base)."""
    if (route is None) == (points is None):
        raise AtlasError("Give exactly one of route_id or points.", hint="route_id from plan_route / plan_science_traverse, "
                                                                        'or points like ["base", "poi:sample_10_swift_run"].')
    budget, reserve, stops, usable, used = _resolve_budget(site, profile, budget_min, reserve_fraction, stops_min)
    params, _ = R.route_params(site, profile)
    notes = [MODEL_NOTE]
    if route is None:
        refs = points if isinstance(points, list) else [points]
        if len(refs) < 2:
            raise AtlasError("`points` needs at least 2 locations (start ... end).")
        route = R.plan_route(site, store, refs[0], refs[-1], refs[1:-1] or None, profile)
        if not route.get("feasible"):
            raise AtlasError("No walkable route through those points: " + str(route.get("reason")),
                             hint=route.get("hint") or "Pick points on walkable ground or relax the slope limit.")
        notes.append(f"The points were routed on foot ({profile}); the check uses that walking route.")
    elif route.get("profile") and route["profile"] != profile:
        notes.append(f'The route was planned with profile "{route["profile"]}"; elapsed time here is recomputed for '
                     f'"{profile}" (walking), not the route\'s own duration.')
    pts = [p for p in route.get("points", []) if p.get("x") is not None]
    if len(pts) < 2:
        raise AtlasError("The route has fewer than 2 points.")
    if havens is None:
        hpts = [{"x": pts[0]["x"], "y": pts[0]["y"], "id": "route start"}]
        haven_desc = "route start (the base)"
    else:
        hpts = resolve_ref(site, havens, store)
        haven_desc = "given havens (nearest used)"
    back_f, fnotes = T._field_and_notes(site, hpts, "walk_time_min", profile)
    notes.extend(fnotes)
    t = _elapsed_minutes(site, pts, params)
    px = np.array([p["x"] for p in pts])
    py = np.array([p["y"] for p in pts])
    rr, cc = site.rc_index(px, py)
    n = site.size_px
    inb = (rr >= 0) & (rr < n) & (cc >= 0) & (cc < n)
    back = np.full(len(pts), np.inf)
    back[inb] = back_f[rr[inb], cc[inb]]
    dist = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(px), np.diff(py)))])
    margin = usable - (t + back + stops)                 # minutes spare beyond the reserve if heading home now
    bad = ~np.isfinite(margin) | (margin < 0)
    rows = []
    for i in range(len(pts)):
        rows.append({"i": i, "x": pts[i]["x"], "y": pts[i]["y"], "distance_m": round(float(dist[i]), 1),
                     "elapsed_min": round(float(t[i]), 1),
                     "walk_back_min": round(float(back[i]), 1) if math.isfinite(back[i]) else None,
                     "margin_min": round(float(margin[i]), 1) if math.isfinite(margin[i]) else None})
    first = int(np.flatnonzero(bad)[0]) if bad.any() else None
    out: dict = {
        "profile": profile, "budget_min": round(budget, 2), "reserve_fraction": reserve, "usable_min": round(usable, 2),
        "stops_min": stops, "values": used, "havens": haven_desc,
        "route_id": route.get("route_id"), "points": len(pts), "route_distance_m": round(float(dist[-1]), 1),
        "route_walk_min": round(float(t[-1]), 1),
        "rule": ("at each point: elapsed + walk_back_to_nearest_haven + stops must be <= budget x (1 - reserve); "
                 "margin_min = usable - (elapsed + walk_back + stops)"),
        "notes": notes,
    }
    if first is None:
        k = int(np.argmin(np.where(np.isfinite(margin), margin, np.inf)))
        out.update({"ok": True, "point_of_no_return": None, "min_margin": rows[k],
                    "verdict": f"OK: the walk-back margin stays at or above the reserve everywhere; tightest at "
                               f"{rows[k]['distance_m']:.0f} m along the route with {rows[k]['margin_min']:.1f} min to spare."})
    else:
        ok_idx = np.flatnonzero(~bad[:first])
        last_safe = rows[int(ok_idx[-1])] if len(ok_idx) else None
        reason = ("no walkable path back from this point" if not math.isfinite(back[first]) else
                  f"elapsed {t[first]:.1f} + walk back {back[first]:.1f} (+ stops {stops:g}) min exceeds the usable "
                  f"{usable:.1f} min")
        out.update({"ok": False, "point_of_no_return": rows[first], "last_safe_point": last_safe, "reason": reason,
                    "verdict": f"NOT OK: past {rows[first]['distance_m']:.0f} m along the route the walk-back margin is "
                               f"below the reserve ({reason}). Turn back at or before the last safe point."})
    step = max(1, len(rows) // 40)
    sample = rows[::step]
    if rows[-1] is not sample[-1]:
        sample.append(rows[-1])
    out["samples"] = sample
    return out

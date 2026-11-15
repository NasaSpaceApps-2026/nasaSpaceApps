"""GET /validation: benchmark against the real Perseverance traverse (PRD "Validation").

Needs a traverse GeoJSON (site dir `traverse.geojson`, or create_app(traverse=...)): a LineString with
[lon, lat] coordinates (planetocentric, east-positive) and optional Point features with properties.sol.
Without one the endpoint says so instead of inventing numbers.
"""
from __future__ import annotations

import math

import numpy as np

from atlas_core import route as R


def _xy(site, lonlat):
    return site.latlon_to_xy(lonlat[1], lonlat[0])


def run_validation(world) -> dict:
    """Compare the site's terrain and planner with the real Perseverance traverse; returns {"available": False, reason, hint} when no traverse is loaded rather than inventing numbers."""
    site, tr = world.site, world.traverse
    if not tr:
        return {"available": False, "reason": "No traverse.geojson is loaded, so there is no real rover path "
                "to compare against.", "hint": "Put traverse.geojson (LineString in lon/lat, optional sol "
                "waypoints) in the site directory."}
    feats = tr.get("features", [])
    line = next((f for f in feats if f.get("geometry", {}).get("type") == "LineString"), None)
    if line is None:
        return {"available": False, "reason": "traverse.geojson has no LineString feature."}
    pts = np.array([_xy(site, c) for c in line["geometry"]["coordinates"]])
    params, _ = R.route_params(site, "rover_scout")
    # hazard agreement: share of in-site traverse length on safe or caution ground (2 m samples)
    seg = np.hypot(*np.diff(pts, axis=0).T)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = float(cum[-1])
    n = max(2, int(total / 2.0) + 1)
    d = np.linspace(0, total, n)
    xs, ys = np.interp(d, cum, pts[:, 0]), np.interp(d, cum, pts[:, 1])
    classes = {"safe": 0, "caution": 0, "no-go": 0, "unknown": 0, "outside_site": 0}
    for x, y in zip(xs, ys):
        if not site.in_bounds(x, y):
            classes["outside_site"] += 1
        else:
            classes[R.hazard_class(R.ground_slope(site, x, y), params)] += 1
    inside = n - classes["outside_site"]
    agree = classes["safe"] + classes["caution"]
    hazard = {"share_safe_or_caution": round(agree / inside, 3) if inside else None,
              "samples": classes, "traverse_length_m": round(total, 1), "profile": "rover_scout",
              "no_go_slope_deg": params["max_slope_deg"],
              "note": "Share of in-site traverse samples (2 m spacing) on ground our map classes as safe or "
                      "caution for the rover. The rover's real slope limits may differ from the assumed ones."}
    # route realism: up to 3 waypoint pairs at least 100 m apart along the traverse
    wps = sorted([f for f in feats if f.get("geometry", {}).get("type") == "Point"],
                 key=lambda f: (f.get("properties") or {}).get("sol", 0))
    wxy = [_xy(site, f["geometry"]["coordinates"]) for f in wps]
    wxy = [p for p in wxy if site.in_bounds(*p)]
    pairs, i = [], 0
    while len(pairs) < 3 and i < len(wxy) - 1:
        j = i + 1
        while j < len(wxy) and math.hypot(wxy[j][0] - wxy[i][0], wxy[j][1] - wxy[i][1]) < 100:
            j += 1
        if j < len(wxy):
            pairs.append((wxy[i], wxy[j]))
        i = j
    realism = []
    for a, b in pairs:
        ia = int(np.argmin(np.hypot(pts[:, 0] - a[0], pts[:, 1] - a[1])))
        ib = int(np.argmin(np.hypot(pts[:, 0] - b[0], pts[:, 1] - b[1])))
        lo, hi = sorted((ia, ib))
        driven = float(seg[lo:hi].sum())
        sl = [R.ground_slope(site, x, y) for x, y in pts[lo:hi + 1]]
        sl = [s for s in sl if math.isfinite(s)]
        r = R.plan_route(site, None, {"x": a[0], "y": a[1]}, {"x": b[0], "y": b[1]}, None, "rover_scout")
        realism.append({"from": {"x": round(a[0], 1), "y": round(a[1], 1)}, "to": {"x": round(b[0], 1), "y": round(b[1], 1)},
                        "driven_m": round(driven, 1), "driven_max_slope_deg": round(max(sl), 1) if sl else None,
                        "planned_feasible": r["feasible"], "planned_m": r["distance_m"] if r["feasible"] else None,
                        "planned_max_slope_deg": r["max_slope_deg"],
                        "reason": r["reason"]})
    return {"available": True, "hazard_agreement": hazard, "route_realism": realism,
            "note": "Both numbers are reported as computed; disagreement can come from the assumed slope "
                    "limits, the 4 m planning grid, or DEM error."}

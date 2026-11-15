"""Science traverse planning and validation against the real rover traverse."""
import json
import math
import threading

import numpy as np
from shapely.geometry import LineString, Point

from .places import Places
from .planner import Planner, RouteError
from .search import SearchError, SiteSearch
from .terrain import Terrain

MAX_CANDIDATES = 8
SCIENCE_TYPES = ("science_target", "sample_site", "landmark")


def plan_science_traverse(planner: Planner, places: Places, search: SiteSearch, start: tuple, start_label: str,
                          time_budget_min: float, interests: list = (), profile: str = None,
                          max_stops: int = 6) -> dict:
    prof = planner.profile(profile)
    stop_min = planner.cfg["stop_minutes"]
    words = [w.lower() for w in interests or []]

    # A stop is only possible if the straight-line round trip at flat speed fits the budget (every cost
    # cell is >= 1/flat_speed s/m), so anything further away can be dropped without changing the result.
    reach_m = max(0.0, (time_budget_min - stop_min) * 60 * prof["flat_speed_mps"] / 2)

    def within(x, y):
        return math.hypot(x - start[0], y - start[1]) <= reach_m and planner.t.in_bounds(x, y)

    cands = []
    for p in places.pois.values():
        if p.get("type") not in SCIENCE_TYPES or not p.get("science_value"):
            continue
        hay = " ".join(str(p.get(k, "")) for k in ("name", "description", "type")).lower()
        if words and not any(w in hay for w in words):
            continue
        if not within(p["x"], p["y"]):
            continue
        cands.append({"label": p["name"], "ref": f"poi:{p['id']}", "x": p["x"], "y": p["y"],
                      "science_value": float(p["science_value"]), "source": p.get("mission", "")})
    for w in words:  # interests that name a searchable target add mapped sites
        try:
            # Reachability is settled by the travel-time matrix below, so skip the per-target search.
            res = search.find(w, near=start, radius_m=reach_m or None, top_k=3, profile=prof["name"],
                              with_travel=False)
        except SearchError:
            continue
        for s in res["sites"]:
            ev = s["evidence"][0]
            cands.append({"label": f"{res['target']} site {s['id']}", "ref": s["id"], "x": s["x"], "y": s["y"],
                          "science_value": s["score"], "source": f"{ev['mission']} {ev['instrument']}"})
    cands.sort(key=lambda c: -c["science_value"])
    cands = cands[:MAX_CANDIDATES]
    if not cands:
        layers = [t["target"] for t in search.available() if t["available"]]
        why = (" This data build has no POIs with a science value"
               + (f"; searchable layers: {', '.join(layers)}." if layers else " and no mineral or geology layers yet."))
        return {"feasible": False, "message": "No science targets match"
                + (f" interests {', '.join(interests)}" if interests else "")
                + f" within reach of a {time_budget_min:.0f} min round trip." + why
                + " Try other interests, a longer budget, or plan_route to a chosen point."}

    # Travel-time matrix in minutes (node 0 = start). Costs are symmetric (MCP_Geometric averages the two
    # cells of each step), so search from node i only until nodes j > i are reached.
    nodes = [{"x": start[0], "y": start[1]}] + cands
    k = len(nodes)
    T = np.full((k, k), np.inf)
    np.fill_diagonal(T, 0.0)
    for i in range(k - 1):
        others = [(nodes[j]["x"], nodes[j]["y"]) for j in range(i + 1, k)]
        try:
            cum = planner.cost_from(nodes[i]["x"], nodes[i]["y"], prof, ends=others)
        except RouteError:
            continue
        for j in range(i + 1, k):
            T[i, j] = T[j, i] = cum[planner.cell(nodes[j]["x"], nodes[j]["y"])] / 60

    def tour_time(order):
        seq = [0] + order + [0]
        return sum(T[a, b] for a, b in zip(seq, seq[1:])) + stop_min * len(order)

    order: list[int] = []
    remaining = set(range(1, len(nodes)))
    while remaining and len(order) < max_stops:
        best = None
        base = tour_time(order)
        for c in remaining:
            for pos in range(len(order) + 1):
                trial = order[:pos] + [c] + order[pos:]
                t = tour_time(trial)
                if not math.isfinite(t) or t > time_budget_min:
                    continue
                gain = nodes[c]["science_value"] / max(t - base, 1e-3)
                if best is None or gain > best[0]:
                    best = (gain, trial, c)
        if best is None:
            break
        order = best[1]
        remaining.discard(best[2])

    if not order:
        nearest = min(range(1, len(nodes)), key=lambda c: T[0, c] + T[c, 0])
        need = T[0, nearest] + T[nearest, 0] + stop_min
        return {"feasible": False,
                "message": f"No stop fits in {time_budget_min:.0f} min. The cheapest round trip "
                           f"({nodes[nearest]['label']}) needs about {need:.0f} min including a {stop_min} min stop."}

    stops = [nodes[i] for i in order]
    route = planner.plan(start, start, via=[(s["x"], s["y"]) for s in stops],
                         labels=[start_label] + [s["label"] for s in stops] + [start_label],
                         profile=prof["name"], time_budget_min=time_budget_min, stops=len(stops))
    route["science_stops"] = [{"order": k + 1, "label": s["label"], "ref": s["ref"], "x": round(s["x"]),
                               "y": round(s["y"]), "science_value": round(s["science_value"], 2),
                               "source": s["source"]} for k, s in enumerate(stops)]
    route["science_value_total"] = round(sum(s["science_value"] for s in stops), 2)
    route["skipped"] = [nodes[i]["label"] for i in sorted(remaining)]
    return route


class Validation:
    """Checks our hazard map and planner against the real Perseverance traverse (Mars 2020, NASA/JPL).

    Reads data/traverse.geojson (one LineString per drive, as pipeline/05_vectors.py writes it, with
    site-frame `xy` when present) and the rover waypoints (Points in traverse.geojson, else
    `rover_waypoint` POIs in pois.geojson). Cached after the first run."""

    MAX_PAIRS = 8                 # waypoint pairs re-planned for route realism
    PAIR_MIN_M, PAIR_MAX_M = 100.0, 1500.0   # driven length between the pair (odometry)

    def __init__(self, terrain: Terrain, planner: Planner):
        self.t = terrain
        self.planner = planner
        self._cache = None
        self._lock = threading.Lock()

    def run(self) -> dict:
        with self._lock:  # the API may call this from several worker threads; compute once
            if self._cache is None:
                out = self._run()
                if not out.get("available"):
                    return out
                self._cache = out
            return self._cache

    def _lines(self, feats) -> list[np.ndarray]:
        lines = []
        for f in feats:
            g = f["geometry"]
            if g["type"] not in ("LineString", "MultiLineString"):
                continue
            parts = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
            xy_prop = f["properties"].get("xy") if g["type"] == "LineString" else None
            for part in ([xy_prop] if xy_prop else parts):
                if xy_prop:
                    a = np.asarray(part, float)
                else:
                    a = np.array([self.t.xy_from_latlon(c[1], c[0]) for c in part], float)
                if len(a) >= 2:
                    lines.append(a)
        return lines

    def _waypoints(self, feats) -> list[dict]:
        wps = []
        for f in feats:
            if f["geometry"]["type"] == "Point":
                lon, lat = f["geometry"]["coordinates"][:2]
                x, y = self.t.xy_from_latlon(lat, lon)
                wps.append({**f["properties"], "x": x, "y": y})
        if not wps:
            path = self.t.data_dir / "data" / "pois.geojson"
            if path.exists():
                for f in json.loads(path.read_text())["features"]:
                    p = f["properties"]
                    if p.get("type") != "rover_waypoint":
                        continue
                    if p.get("x") is None or p.get("y") is None:
                        lon, lat = f["geometry"]["coordinates"][:2]
                        p = {**p, **dict(zip(("x", "y"), self.t.xy_from_latlon(lat, lon)))}
                    wps.append(dict(p))
        wps = [w for w in wps if self.t.in_bounds(w["x"], w["y"])]
        wps.sort(key=lambda w: (w.get("sol") or 0, w.get("dist_total_m") or 0))
        return wps

    def _run(self) -> dict:
        path = self.t.data_dir / "data" / "traverse.geojson"
        if not path.exists():
            return {"available": False, "message": "No traverse.geojson in the data folder."}
        feats = json.loads(path.read_text())["features"]
        lines = self._lines(feats)
        if not lines:
            return {"available": False, "message": "traverse.geojson has no LineString."}
        prof = self.planner.profile(self.planner.cfg["rover_profile"])

        # Hazard agreement: sample every drive at the raster spacing, weight each sample by its length.
        xs, ys, ws = [], [], []
        for a in lines:
            for (x0, y0), (x1, y1) in zip(a[:-1], a[1:]):
                d = math.hypot(x1 - x0, y1 - y0)
                if d == 0:
                    continue
                n = max(1, int(math.ceil(d / self.t.res)))
                f = (np.arange(n) + 0.5) / n
                xs.append(x0 + (x1 - x0) * f)
                ys.append(y0 + (y1 - y0) * f)
                ws.append(np.full(n, d / n))
        xs, ys, ws = np.concatenate(xs), np.concatenate(ys), np.concatenate(ws)
        inside = (np.abs(xs) <= self.t.half) & (np.abs(ys) <= self.t.half)
        s = self.t.slope(xs[inside], ys[inside])
        w = ws[inside]
        ok = np.isfinite(s)
        s, w = s[ok], w[ok]
        total = float(w.sum())
        cls = np.where(s >= prof["nogo_slope_deg"], 2, np.where(s >= prof["caution_slope_deg"], 1, 0))
        share = {c: round(float(w[cls == k].sum() / total), 3) if total else 0.0
                 for k, c in enumerate(("safe", "caution", "no-go"))}
        agreement = round(1 - share["no-go"], 3)
        # Context: the steepest ground the rover actually drove on, by our map (length-weighted percentiles).
        order = np.argsort(s)
        cw = np.cumsum(w[order]) / total if total else np.array([0.0])
        pct = {f"p{q}": round(float(s[order][min(np.searchsorted(cw, q / 100), len(s) - 1)]), 1)
               for q in (50, 95, 99)} if total else {}
        pct["max"] = round(float(s.max()), 1) if total else None
        # Baseline: the same classes over the whole site. Agreement means more when the rover's share of
        # no-go ground is far below the site's (it avoided steep ground) than when the site is all flat.
        g = self.t.grids["slope"]
        g = g[np.isfinite(g)]
        base = {"safe": round(float((g < prof["caution_slope_deg"]).mean()), 3),
                "caution": round(float(((g >= prof["caution_slope_deg"]) & (g < prof["nogo_slope_deg"])).mean()), 3),
                "no-go": round(float((g >= prof["nogo_slope_deg"]).mean()), 3)}

        # Route realism: re-plan between consecutive real waypoints, compare with the odometry distance.
        wps = self._waypoints(feats)
        merged = LineString(np.vstack(lines)) if len(lines) == 1 else None
        cands = []
        for a, b in zip(wps, wps[1:]):
            if a.get("dist_total_m") is not None and b.get("dist_total_m") is not None:
                actual = float(b["dist_total_m"]) - float(a["dist_total_m"])
                basis = "odometry"
            elif merged is not None:
                actual = abs(merged.project(Point(b["x"], b["y"])) - merged.project(Point(a["x"], a["y"])))
                basis = "traverse line"
            else:
                continue
            if len(wps) > 2 and not (self.PAIR_MIN_M <= actual <= self.PAIR_MAX_M):
                continue
            cands.append((a, b, actual, basis))
        if len(cands) > self.MAX_PAIRS:  # spread the sample over the mission, not just the first sols
            pick = np.linspace(0, len(cands) - 1, self.MAX_PAIRS).round().astype(int)
            cands = [cands[i] for i in pick]
        pairs = []
        for a, b, actual, basis in cands:
            la, lb = a.get("name", "A"), b.get("name", "B")
            try:
                r = self.planner.plan((a["x"], a["y"]), (b["x"], b["y"]), labels=[la, lb], profile=prof["name"],
                                      store=False)
                pairs.append({"from": la, "to": lb, "sols": [a.get("sol"), b.get("sol")],
                              "straight_m": round(math.hypot(b["x"] - a["x"], b["y"] - a["y"])),
                              "actual_m": round(actual), "actual_basis": basis, "planned_m": r["distance_m"],
                              "ratio": round(r["distance_m"] / actual, 2) if actual else None,
                              "planned_max_slope_deg": r["max_slope_deg"]})
            except RouteError as e:
                pairs.append({"from": la, "to": lb, "sols": [a.get("sol"), b.get("sol")], "actual_m": round(actual),
                              "actual_basis": basis, "planned_m": None, "error": str(e)})
        ratios = [p["ratio"] for p in pairs if p.get("ratio")]
        real = not self.t.manifest.get("fixture")
        return {
            "available": True,
            "profile": prof["name"],
            "traverse_length_m": round(total),
            "traverse_drives": len(lines),
            "hazard_agreement": agreement,
            "hazard_share": share,
            "traverse_slope_deg": pct,
            "site_hazard_share": base,
            "hazard_agreement_note": f"Share of the real traverse length our map does NOT class as no-go for "
                                     f"{prof['name']} (caution >= {prof['caution_slope_deg']} deg, no-go >= "
                                     f"{prof['nogo_slope_deg']} deg, slope on the {self.t.res:g} m grid). "
                                     f"Higher supports the hazard map.",
            "route_realism": pairs,
            "route_realism_summary": {"pairs": len(pairs), "planned": len(ratios),
                                      "median_ratio": round(float(np.median(ratios)), 2) if ratios else None,
                                      "min_ratio": min(ratios) if ratios else None,
                                      "max_ratio": max(ratios) if ratios else None},
            "route_realism_note": "Planned vs actually driven length between consecutive real waypoints "
                                  f"({self.PAIR_MIN_M:.0f}-{self.PAIR_MAX_M:.0f} m drives, spread over the mission; "
                                  "actual = rover odometry where available). Ratios below 1 mean our planner "
                                  "found a shorter path than the rover drove, often because real drives detour "
                                  "for science, rocks below DEM resolution, and odometry includes wheel slip.",
            "sources": {"traverse": "Mars 2020 traverse and waypoints (NASA/JPL MMGIS)" if real else "fixture",
                        "slope": self.t.source_label("slope")},
        }

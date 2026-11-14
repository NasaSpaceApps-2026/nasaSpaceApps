"""Science traverse planning and validation against the real rover traverse."""
import json
import math

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

    cands = []
    for p in places.pois.values():
        if p.get("type") not in SCIENCE_TYPES or not p.get("science_value"):
            continue
        hay = " ".join(str(p.get(k, "")) for k in ("name", "description", "type")).lower()
        if words and not any(w in hay for w in words):
            continue
        cands.append({"label": p["name"], "ref": f"poi:{p['id']}", "x": p["x"], "y": p["y"],
                      "science_value": float(p["science_value"]), "source": p.get("mission", "")})
    for w in words:  # interests that name a searchable target add mapped sites
        try:
            res = search.find(w, near=start, top_k=3, profile=prof["name"])
        except SearchError:
            continue
        for s in res["sites"]:
            if s.get("reachable") is False:
                continue
            ev = s["evidence"][0]
            cands.append({"label": f"{res['target']} site {s['id']}", "ref": s["id"], "x": s["x"], "y": s["y"],
                          "science_value": s["score"], "source": f"{ev['mission']} {ev['instrument']}"})
    cands.sort(key=lambda c: -c["science_value"])
    cands = cands[:MAX_CANDIDATES]
    if not cands:
        return {"feasible": False, "message": "No science targets match"
                + (f" interests {', '.join(interests)}." if interests else ".") + " Try other interests or none."}

    # Travel-time matrix in minutes (node 0 = start).
    nodes = [{"x": start[0], "y": start[1]}] + cands
    T = np.full((len(nodes), len(nodes)), np.inf)
    for i, n in enumerate(nodes):
        try:
            cum = planner.cost_from(n["x"], n["y"], prof)
        except RouteError:
            continue
        for j, m in enumerate(nodes):
            T[i, j] = cum[planner.cell(m["x"], m["y"])] / 60

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
    def __init__(self, terrain: Terrain, planner: Planner):
        self.t = terrain
        self.planner = planner
        self._cache = None

    def run(self) -> dict:
        if self._cache:
            return self._cache
        path = self.t.data_dir / "data" / "traverse.geojson"
        if not path.exists():
            return {"available": False, "message": "No traverse.geojson in the data folder."}
        feats = json.loads(path.read_text())["features"]
        line_f = next((f for f in feats if f["geometry"]["type"] == "LineString"), None)
        if not line_f:
            return {"available": False, "message": "traverse.geojson has no LineString."}
        xy = [self.t.xy_from_latlon(lat, lon) for lon, lat, *_ in line_f["geometry"]["coordinates"]]
        line = LineString(xy)
        prof = self.planner.profile(self.planner.cfg["rover_profile"])

        n = max(2, int(line.length / self.t.res))
        pts = [line.interpolate(i / n, normalized=True) for i in range(n + 1)]
        xs = np.array([p.x for p in pts])
        ys = np.array([p.y for p in pts])
        inside = (np.abs(xs) <= self.t.half) & (np.abs(ys) <= self.t.half)
        s = self.t.slope(xs[inside], ys[inside])
        classes = np.where(s >= prof["nogo_slope_deg"], "no-go", np.where(s >= prof["caution_slope_deg"], "caution", "safe"))
        share = {c: round(float((classes == c).mean()), 3) for c in ("safe", "caution", "no-go")}
        agreement = share["safe"] + share["caution"]

        wps = [f for f in feats if f["geometry"]["type"] == "Point"]
        wps.sort(key=lambda f: f["properties"].get("sol") or 0)
        pairs = []
        for a, b in list(zip(wps, wps[1:]))[:3]:
            pa = self.t.xy_from_latlon(a["geometry"]["coordinates"][1], a["geometry"]["coordinates"][0])
            pb = self.t.xy_from_latlon(b["geometry"]["coordinates"][1], b["geometry"]["coordinates"][0])
            actual = abs(line.project(Point(pb)) - line.project(Point(pa)))
            la = a["properties"].get("name", "A")
            lb = b["properties"].get("name", "B")
            try:
                r = self.planner.plan(pa, pb, labels=[la, lb], profile=prof["name"])
                pairs.append({"from": la, "to": lb, "actual_m": round(actual), "planned_m": r["distance_m"],
                              "ratio": round(r["distance_m"] / actual, 2) if actual else None,
                              "planned_max_slope_deg": r["max_slope_deg"]})
            except RouteError as e:
                pairs.append({"from": la, "to": lb, "actual_m": round(actual), "planned_m": None, "error": str(e)})

        self._cache = {
            "available": True,
            "profile": prof["name"],
            "traverse_length_m": round(line.length),
            "hazard_agreement": round(agreement, 3),
            "hazard_share": share,
            "hazard_agreement_note": f"Share of the real traverse our map classes as safe or caution for "
                                     f"{prof['name']} (no-go >= {prof['nogo_slope_deg']} deg). Higher supports the hazard map.",
            "route_realism": pairs,
            "route_realism_note": "Planned vs actually driven length between consecutive real waypoints. "
                                  "Ratios below 1 mean our planner found a shorter path the rover did not take, "
                                  "often because real drives detour for science and rocks below DEM resolution.",
            "sources": {"traverse": "fixture" if self.t.manifest.get("fixture") else "Mars 2020 traverse",
                        "slope": self.t.source_label("slope")},
        }
        return self._cache

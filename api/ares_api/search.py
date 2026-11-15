"""Science site search: find and rank regions of a target layer (minerals, geologic units)."""
import itertools
import math

import numpy as np
from scipy import ndimage

from .planner import Planner, RouteError
from .terrain import Terrain

MIN_AREA_M2 = 50.0


class SearchError(ValueError):
    pass


class SiteSearch:
    def __init__(self, terrain: Terrain, planner: Planner, cfg: dict):
        self.t = terrain
        self.planner = planner
        self.targets = cfg["targets"]
        self._ids = itertools.count(1)

    def available(self) -> list[dict]:
        out = []
        for name, tg in self.targets.items():
            ok = tg["layer"] in self.t.layers
            out.append({"target": name, "available": ok, "layer": tg["layer"], "kind": tg["kind"],
                        "mission": tg["mission"], "instrument": tg["instrument"],
                        "aliases": tg.get("aliases", []), "description": tg.get("description", ""),
                        "data": self.t.layer_source.get(tg["layer"]) if ok else "missing"})
        return out

    def resolve_target(self, name: str) -> tuple[str, dict]:
        n = name.strip().lower()
        for key, tg in self.targets.items():
            if n == key or n in [a.lower() for a in tg.get("aliases", [])]:
                if tg["layer"] not in self.t.layers:
                    raise SearchError(f"Target '{key}' needs layer '{tg['layer']}', which is not loaded yet. "
                                      f"Available: {', '.join(t['target'] for t in self.available() if t['available'])}.")
                return key, tg
        avail = [t["target"] for t in self.available() if t["available"]]
        raise SearchError(f"Unknown target '{name}'. Available targets: {', '.join(avail) or 'none'}.")

    def find(self, target: str, near: tuple = None, near_label: str = None, radius_m: float = None,
             max_slope_deg: float = None, top_k: int = 5, profile: str = None, with_travel: bool = True) -> dict:
        """Regions of the target layer, ranked. With `near` (and with_travel), each site also gets
        reachable / travel_min from a least-cost search that stops once every site is reached."""
        key, tg = self.resolve_target(target)
        prof = self.planner.profile(profile)
        max_slope = max_slope_deg or prof["nogo_slope_deg"]
        layer = self.t.layers[tg["layer"]]
        slope = self.t.grids["slope"]
        if tg["kind"] == "class":
            mask = np.isclose(layer, tg["value"])
        else:
            mask = layer >= tg.get("threshold", 0.5)  # NaN compares False: no-data never matches
        if near and radius_m:
            # Only the rows/cols inside the radius box can match; build distances there, not on the whole grid.
            r0, c0 = (int(np.floor(v)) for v in self.t.to_cell(near[0] - radius_m, near[1] + radius_m))
            r1, c1 = (int(np.ceil(v)) + 1 for v in self.t.to_cell(near[0] + radius_m, near[1] - radius_m))
            h, w = self.t.shape
            r0, c0, r1, c1 = max(r0, 0), max(c0, 0), min(r1, h), min(c1, w)
            keep = np.zeros(self.t.shape, bool)
            if r0 < r1 and c0 < c1:
                xs, ys = self.t.cell_xy(np.arange(r0, r1)[:, None], np.arange(c0, c1)[None, :])
                keep[r0:r1, c0:c1] = (xs - near[0]) ** 2 + (ys - near[1]) ** 2 <= radius_m ** 2
            mask &= keep

        labels, n = ndimage.label(mask)
        if n == 0:
            return {"target": key, "sites": [], "count": 0,
                    "message": f"No cells match '{key}'" + (f" within {radius_m:.0f} m." if radius_m else ".")}
        # Per-region statistics over the matching cells only (the grid is 16 M cells; regions are tiny).
        flat = np.flatnonzero(labels)
        lab = labels.ravel()[flat] - 1
        strength = (np.ones(flat.size, np.float32) if tg["kind"] == "class"
                    else np.nan_to_num(layer.ravel()[flat]))
        sl = np.nan_to_num(slope.ravel()[flat], nan=90.0)
        counts = np.bincount(lab, minlength=n)
        areas = counts * self.t.res ** 2
        means = np.bincount(lab, strength, n) / counts
        slope_means = np.bincount(lab, sl, n) / counts
        peaks = np.full(n, -np.inf)
        np.maximum.at(peaks, lab, strength)
        # Site anchor: the strongest cell that is under the slope limit (where a person could stand).
        accessible = np.where(sl <= max_slope, strength, -1.0)
        order = np.lexsort((-accessible, lab))           # by region, strongest first
        first = order[np.r_[0, np.flatnonzero(np.diff(lab[order])) + 1]]
        anchor_flat = np.full(n, -1, np.int64)
        anchor_ok = np.zeros(n, bool)
        anchor_flat[lab[first]] = flat[first]
        anchor_ok[lab[first]] = accessible[first] >= 0

        keep_idx = [i for i in range(n) if areas[i] >= MIN_AREA_M2 and anchor_ok[i]]
        anchors = {i: np.unravel_index(anchor_flat[i], self.t.shape) for i in keep_idx}
        anchor_xy = {i: tuple(float(v) for v in self.t.cell_xy(*anchors[i])) for i in keep_idx}

        cum = None
        if near and with_travel and keep_idx:
            try:
                cum = self.planner.cost_from(near[0], near[1], prof, max_slope_deg,
                                             ends=[anchor_xy[i] for i in keep_idx])
            except RouteError:
                cum = None

        sites = []
        for i in keep_idx:
            x, y = anchor_xy[i]
            area_term = 1 - math.exp(-areas[i] / 2000.0)
            slope_pen = min(1.0, slope_means[i] / max_slope)
            score = (0.6 * float(means[i]) + 0.4 * area_term) * (1 - 0.3 * slope_pen)
            site = {"x": round(x), "y": round(y), "z": round(float(self.t.elevation(x, y)), 1),
                    "score": round(score, 3), "area_m2": round(float(areas[i])),
                    "mean_slope_deg": round(float(slope_means[i]), 1),
                    "slope_at_site_deg": round(float(self.t.slope(x, y)), 1)}
            site["lat"], site["lon"] = self.t.latlon(x, y)
            if near:
                site["distance_m"] = round(math.hypot(x - near[0], y - near[1]))
            if cum is not None:
                rc = self.planner.cell(x, y)
                secs = float(cum[rc])
                site["reachable"] = bool(np.isfinite(secs))
                site["travel_min"] = round(secs / 60, 1) if np.isfinite(secs) else None
            else:
                site["reachable"] = None
            ev = {"layer": tg["layer"], "mission": tg["mission"], "instrument": tg["instrument"],
                  "data": self.t.layer_source.get(tg["layer"])}
            if tg["kind"] == "class":
                ev["unit_class"] = tg["value"]
            else:
                ev["mean_value"] = round(float(means[i]), 3)
                ev["peak_value"] = round(float(peaks[i]), 3)
                ev["threshold"] = tg.get("threshold", 0.5)
            site["evidence"] = [ev, {"layer": "slope", "source": self.t.source_label("slope"),
                                     "mean_slope_deg": site["mean_slope_deg"]}]
            sites.append(site)

        sites.sort(key=lambda s: (s["reachable"] is False, -s["score"]))
        sites = sites[:top_k]
        for s in sites:
            s["id"] = f"site{next(self._ids)}"
        out = {"target": key, "description": tg.get("description", ""), "count": len(sites),
               "near": near_label, "max_slope_deg": max_slope, "profile": prof["name"], "sites": sites}
        if self.t.layer_source.get(tg["layer"]) == "synthetic":
            out["caveat"] = f"Layer '{tg['layer']}' is a synthetic placeholder, not Mars data."
        return out

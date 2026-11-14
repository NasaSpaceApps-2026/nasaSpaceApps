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
             max_slope_deg: float = None, top_k: int = 5, profile: str = None) -> dict:
        key, tg = self.resolve_target(target)
        prof = self.planner.profile(profile)
        max_slope = max_slope_deg or prof["nogo_slope_deg"]
        layer = self.t.layers[tg["layer"]]
        slope = self.t.grids["slope"]
        if tg["kind"] == "class":
            mask = np.isclose(layer, tg["value"])
            strength = mask.astype(np.float32)
        else:
            mask = layer >= tg.get("threshold", 0.5)
            strength = np.nan_to_num(layer)
        if near and radius_m:
            rows = np.arange(self.t.shape[0], dtype=np.float32)[:, None]
            cols = np.arange(self.t.shape[1], dtype=np.float32)[None, :]
            xs, ys = self.t.cell_xy(rows, cols)
            mask &= (xs - near[0]) ** 2 + (ys - near[1]) ** 2 <= radius_m ** 2

        labels, n = ndimage.label(mask)
        if n == 0:
            return {"target": key, "sites": [], "count": 0,
                    "message": f"No cells match '{key}'" + (f" within {radius_m:.0f} m." if radius_m else ".")}
        idx = np.arange(1, n + 1)
        cell_area = self.t.res ** 2
        areas = ndimage.sum_labels(np.ones_like(strength), labels, idx) * cell_area
        means = ndimage.mean(strength, labels, idx)
        slope_means = ndimage.mean(np.nan_to_num(slope, nan=90.0), labels, idx)
        # Site anchor: the strongest cell that is under the slope limit (where a person could stand).
        accessible = np.where(np.nan_to_num(slope, nan=90.0) <= max_slope, strength, -1.0)
        anchors = ndimage.maximum_position(accessible, labels, idx)
        peaks = ndimage.maximum(strength, labels, idx)

        cum = None
        if near:
            try:
                cum = self.planner.cost_from(near[0], near[1], self.planner.profile(profile), max_slope_deg)
            except RouteError:
                cum = None

        sites = []
        for i in range(n):
            if areas[i] < MIN_AREA_M2:
                continue
            r, c = anchors[i]
            if accessible[r, c] < 0:
                continue  # whole region is too steep to stand on
            x, y = (float(v) for v in self.t.cell_xy(r, c))
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

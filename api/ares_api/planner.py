"""Least-cost route planner on a coarse slope/roughness grid (skimage MCP_Geometric)."""
import itertools
import math

import numpy as np
from scipy import ndimage
from shapely.geometry import LineString
from skimage.graph import MCP_Geometric

from . import config
from .terrain import Terrain

SNAP_RADIUS_M = 20.0
POINT_SPACING_M = 5.0


class RouteError(ValueError):
    pass


def _pool(a: np.ndarray, f: int, fn) -> np.ndarray:
    h, w = (a.shape[0] // f) * f, (a.shape[1] // f) * f
    return fn(a[:h, :w].reshape(h // f, f, w // f, f), axis=(1, 3))


class Planner:
    def __init__(self, terrain: Terrain, cfg: dict):
        self.t = terrain
        self.cfg = cfg
        self.f = max(1, int(round(config.PLANNING_RES_M / terrain.res)))
        self.res = terrain.res * self.f
        slope = np.nan_to_num(terrain.grids["slope"], nan=90.0)
        self.slope_max = _pool(slope, self.f, np.max)
        self.slope_mean = _pool(slope, self.f, np.mean)
        self.rough = _pool(np.nan_to_num(terrain.grids["rough"]), self.f, np.mean)
        self.filled = _pool(terrain.filled.astype(np.float32), self.f, np.max) > 0
        self._cost_cache: dict = {}
        self.routes: dict[str, dict] = {}
        self._ids = itertools.count(1)

    # --- profiles and costs ----------------------------------------------------
    def profile(self, name: str = None) -> dict:
        name = name or self.cfg["default_profile"]
        if name not in self.cfg["profiles"]:
            raise RouteError(f"Unknown profile '{name}'. Options: {', '.join(self.cfg['profiles'])}.")
        return {"name": name, **self.cfg["profiles"][name]}

    def speed(self, slope_deg, prof: dict):
        k = prof.get("speed_curve_k", self.cfg["speed_curve"]["k"])
        return prof["flat_speed_mps"] * np.exp(-k * np.abs(np.tan(np.radians(slope_deg))))

    def nogo_limit(self, prof: dict, max_slope: float = None) -> float:
        lim = prof["nogo_slope_deg"]
        return min(lim, max_slope) if max_slope else lim

    def cost_grid(self, prof: dict, max_slope: float = None) -> tuple[np.ndarray, np.ndarray]:
        """Seconds per metre per coarse cell, and the impassable mask."""
        lim = self.nogo_limit(prof, max_slope)
        key = (prof["name"], lim)
        if key not in self._cost_cache:
            w = self.cfg["cost_weights"]
            nogo = self.slope_max > lim
            buf = int(math.ceil(w.get("nogo_buffer_m", 0) / self.res))
            if buf:
                nogo = ndimage.binary_dilation(nogo, iterations=buf)
            cost = (1.0 / self.speed(self.slope_mean, prof)) * (1 + w["rough"] * self.rough + w["unknown"] * self.filled)
            cost = cost.astype(np.float64)
            cost[nogo] = np.inf
            self._cost_cache[key] = (cost, nogo)
        return self._cost_cache[key]

    # --- grid helpers ------------------------------------------------------------
    def cell(self, x: float, y: float) -> tuple[int, int]:
        r, c = self.t.to_cell(x, y, self.res)
        h, w = self.slope_max.shape
        return int(np.clip(round(float(r)), 0, h - 1)), int(np.clip(round(float(c)), 0, w - 1))

    def cell_xy(self, rc) -> tuple[float, float]:
        x, y = self.t.cell_xy(rc[0], rc[1], self.res)
        return float(x), float(y)

    def _snap(self, x, y, nogo, label, limit: float = None, strict: bool = False):
        """Move a point off a buffered no-go cell if it is only in the safety buffer.
        With strict, a point whose own slope is over the limit is refused instead."""
        rc = self.cell(x, y)
        if not nogo[rc]:
            return rc, None
        here = float(self.t.slope(x, y))
        if strict and limit is not None and here > limit:
            raise RouteError(f"{label} at ({x:.0f}, {y:.0f}) is on a {here:.0f} deg slope, over the "
                             f"{limit:.0f} deg limit. Pick a point on flatter ground nearby "
                             f"(sample_terrain shows slope).")
        n = int(SNAP_RADIUS_M / self.res)
        r0, c0 = rc
        best = None
        for r in range(max(0, r0 - n), min(nogo.shape[0], r0 + n + 1)):
            for c in range(max(0, c0 - n), min(nogo.shape[1], c0 + n + 1)):
                if not nogo[r, c]:
                    d = math.hypot(r - r0, c - c0)
                    if best is None or d < best[0]:
                        best = (d, (r, c))
        if best is None:
            raise RouteError(f"{label} at ({x:.0f}, {y:.0f}) sits on terrain over the slope limit "
                             f"(slope {float(self.t.slope(x, y)):.0f} deg) with no safe ground within "
                             f"{SNAP_RADIUS_M:.0f} m. Pick a different point.")
        return best[1], f"{label} moved {best[0] * self.res:.0f} m to the nearest safe ground."

    def cost_from(self, x: float, y: float, prof: dict, max_slope: float = None) -> np.ndarray:
        """Cumulative travel seconds from (x, y) to every coarse cell (inf where unreachable)."""
        cost, nogo = self.cost_grid(prof, max_slope)
        rc, _ = self._snap(x, y, nogo, "Start")
        m = MCP_Geometric(cost, sampling=(self.res, self.res))
        cum, _ = m.find_costs([rc])
        return cum

    # --- planning -------------------------------------------------------------------
    def _leg(self, a, b, prof, max_slope, labels):
        cost, nogo = self.cost_grid(prof, max_slope)
        warnings = []
        lim = self.nogo_limit(prof, max_slope)
        sa, wa = self._snap(*a, nogo, labels[0])  # start may be wherever the rover already is
        sb, wb = self._snap(*b, nogo, labels[1], lim, strict=True)
        warnings += [w for w in (wa, wb) if w]
        m = MCP_Geometric(cost, sampling=(self.res, self.res))
        cum, _ = m.find_costs([sa], [sb])
        if not np.isfinite(cum[sb]):
            cum, _ = m.find_costs([sa])
            reach = np.isfinite(cum)
            rr, cc = np.nonzero(reach)
            d = np.hypot(rr - sb[0], cc - sb[1])
            i = int(np.argmin(d))
            nx, ny = self.cell_xy((rr[i], cc[i]))
            lim = self.nogo_limit(prof, max_slope)
            raise RouteError(
                f"No route from {labels[0]} to {labels[1]} stays under {lim:.0f} deg slope for profile "
                f"{prof['name']}. Nearest reachable point is ({nx:.0f}, {ny:.0f}), "
                f"{d[i] * self.res:.0f} m from the goal. Use that point as the goal, pick a different goal, "
                f"or (if the limit was tightened with max_slope_deg) relax it up to the profile's "
                f"{prof['nogo_slope_deg']} deg.")
        path = m.traceback(sb)
        return [self.cell_xy(rc) for rc in path], warnings

    def plan(self, start, goal, via: list = (), labels: list = None, profile: str = None,
             max_slope_deg: float = None, time_budget_min: float = None, stops: int = 0) -> dict:
        prof = self.profile(profile)
        pts = [start, *via, goal]
        labels = labels or [f"({p[0]:.0f}, {p[1]:.0f})" for p in pts]
        for p, lab in zip(pts, labels):
            if not self.t.in_bounds(*p):
                raise RouteError(f"{lab} is outside the site (|x|, |y| <= {self.t.half:.0f} m).")
        cells, warnings, leg_ends = [], [], []
        for i in range(len(pts) - 1):
            leg, w = self._leg(pts[i], pts[i + 1], prof, max_slope_deg, (labels[i], labels[i + 1]))
            warnings += w
            # exact endpoints at the ends, grid path in between
            leg = [tuple(pts[i])] + leg[1:-1] + [tuple(pts[i + 1])]
            cells += leg if not cells else leg[1:]
            leg_ends.append(len(cells) - 1)
        route = self._summarise(cells, leg_ends, prof, max_slope_deg, labels, stops, time_budget_min)
        route["warnings"] = warnings + route["warnings"]
        route["direct_line"] = self.direct_line(pts[0], pts[-1], prof, max_slope_deg)
        rid = f"r{next(self._ids)}"
        route["route_id"] = rid
        self.routes[rid] = route
        return route

    def _summarise(self, raw, leg_ends, prof, max_slope, labels, stops, budget):
        # Simplify per leg so stop points survive, then resample to <= 5 m spacing.
        pts, legs, start = [], [], 0
        for i, end in enumerate(leg_ends):
            seg = raw[start:end + 1]
            line = LineString(seg).simplify(1.0) if len(seg) > 2 else LineString(seg)
            n = max(1, int(math.ceil(line.length / POINT_SPACING_M)))
            res = [line.interpolate(j / n, normalized=True).coords[0] for j in range(n + 1)]
            legs.append({"from": labels[i], "to": labels[i + 1], "length_pts": len(res)})
            pts += res if not pts else res[1:]
            start = end
        xs = np.array([p[0] for p in pts])
        ys = np.array([p[1] for p in pts])
        zs = self.t.elevation(xs, ys)
        slopes = self.t.slope(xs, ys)
        seg = np.hypot(np.diff(xs), np.diff(ys))
        mid_slope = self.t.slope((xs[:-1] + xs[1:]) / 2, (ys[:-1] + ys[1:]) / 2)
        seg_s = seg / self.speed(mid_slope, prof)
        dz = np.diff(zs)
        # per-leg totals
        idx = 0
        for leg in legs:
            k = leg.pop("length_pts") - 1
            leg["distance_m"] = round(float(seg[idx:idx + k].sum()))
            leg["duration_min"] = round(float(seg_s[idx:idx + k].sum()) / 60, 1)
            idx += k
        duration = float(seg_s.sum()) / 60
        stop_min = stops * self.cfg["stop_minutes"]
        used = (duration + stop_min) / prof["eva_limit_min"]
        margin = 1 - prof["reserve_frac"] - used
        lim = self.nogo_limit(prof, max_slope)
        caution = int((slopes >= prof["caution_slope_deg"]).sum())
        warnings = []
        if caution:
            warnings.append(f"{caution} of {len(pts)} route points are on caution slopes "
                            f"(>= {prof['caution_slope_deg']} deg).")
        if margin < 0:
            warnings.append(f"Consumables margin is {margin * 100:.0f}% (below the {prof['reserve_frac'] * 100:.0f}% "
                            f"reserve). Shorten the route or drop stops.")
        if budget and duration + stop_min > budget:
            warnings.append(f"Route takes {duration + stop_min:.0f} min, over the {budget:.0f} min budget.")
        return {
            "feasible": True,
            "profile": prof["name"],
            "start": labels[0], "goal": labels[-1], "stops": labels[1:-1],
            "points": [{"x": round(float(x), 1), "y": round(float(y), 1), "z": round(float(z), 2),
                        "slope": round(float(s), 1)} for x, y, z, s in zip(xs, ys, zs, slopes)],
            "distance_m": round(float(seg.sum())),
            "duration_min": round(duration, 1),
            "stop_minutes": stop_min,
            "ascent_m": round(float(dz[dz > 0].sum()), 1),
            "descent_m": round(float(-dz[dz < 0].sum()), 1),
            "max_slope_deg": round(float(slopes.max()), 1),
            "slope_limit_deg": lim,
            "hazard_cells": caution,
            "legs": legs,
            "consumables": {"used_frac": round(used, 3), "reserve_frac": prof["reserve_frac"],
                            "margin_frac": round(margin, 3), "eva_limit_min": prof["eva_limit_min"],
                            "basis": "time-based: (drive + stop time) / EVA limit (assumed)"},
            "time_budget_min": budget,
            "warnings": warnings,
            "sources": {"slope": self.t.source_label("slope"), "speeds": prof.get("source", "assumed")},
        }

    def direct_line(self, a, b, prof, max_slope=None) -> dict:
        """The straight line between endpoints, to explain why the route detours."""
        d = math.hypot(b[0] - a[0], b[1] - a[1])
        n = max(2, int(d / self.t.res))
        xs = np.linspace(a[0], b[0], n)
        ys = np.linspace(a[1], b[1], n)
        s = self.t.slope(xs, ys)
        i = int(np.argmax(s))
        lim = self.nogo_limit(prof, max_slope)
        return {"distance_m": round(d), "max_slope_deg": round(float(s[i]), 1),
                "crosses_nogo": bool(s[i] > lim),
                "steepest_at": {"x": round(float(xs[i])), "y": round(float(ys[i]))}}

    def get(self, rid: str) -> dict:
        if rid not in self.routes:
            raise RouteError(f"No route '{rid}'. Plan one with plan_route first. Known: {', '.join(self.routes) or 'none'}.")
        return self.routes[rid]

    def assess(self, rid: str) -> dict:
        r = self.get(rid)
        reasons = []
        if not r["feasible"]:
            reasons.append("route is not feasible")
        if r["consumables"]["margin_frac"] < 0:
            reasons.append(f"margin {r['consumables']['margin_frac'] * 100:.0f}% is below the reserve")
        if r["time_budget_min"] and r["duration_min"] + r["stop_minutes"] > r["time_budget_min"]:
            reasons.append("over the time budget")
        if r["max_slope_deg"] > r["slope_limit_deg"]:
            reasons.append(f"max slope {r['max_slope_deg']} deg exceeds the {r['slope_limit_deg']} deg limit")
        go = not reasons
        return {"route_id": rid, "go": go, "verdict": "GO" if go else "NO-GO", "reasons": reasons or ["all checks pass"],
                "distance_m": r["distance_m"], "duration_min": r["duration_min"],
                "max_slope_deg": r["max_slope_deg"], "consumables": r["consumables"], "warnings": r["warnings"]}

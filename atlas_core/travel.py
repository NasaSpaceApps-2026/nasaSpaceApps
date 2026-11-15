"""Travel cost, reachability and nearest pairs. Spec: contracts/mcp-tools.md sections 4.4 and 4.6.

Layer: atlas_core, called by devapi/ (REST) and from there by mcp/ tools; reads the Site loaded from data/build/site/<name>/.
Method: Dijkstra least-cost path via skimage.graph.MCP_Geometric on a ~4 m planning grid, 8-connected (diagonal steps cost
sqrt(2) x the cell cost). Inputs: location refs, profile (profiles.yaml travel keys). Outputs: cost fields in metres or
minutes, nearest pair sets. Failure: AtlasError + hint.

Metrics:
    "straight_m"     Euclidean distance in metres, ignores terrain. Computed exactly on the
                     analysis grid (distance transform from source cells, or direct formula).
    "walk_m"         shortest path length in metres over passable cells.
    "walk_time_min"  shortest travel time in minutes over passable cells using the profile's speed.

Passable: slope_2m finite and <= profile.travel.max_slope_deg. Unknown cells are impassable.

Speed (placeholder, must be labelled as such in outputs): isotropic Tobler-shaped curve
    speed = flat_speed_mps * exp(-3.5 * tan(slope))
Isotropic means uphill and downhill cost the same; outputs say times are approximate.

Planning grid: the analysis grid block-reduced by factor k = max(1, round(4.0 / site.res_m)) to
about 4 m cells. A planning cell is passable only if ALL its analysis cells are passable (conservative).
Its slope is the max slope of its analysis cells. Use skimage.graph.MCP_Geometric with
fully_connected=True; remember its costs are in "cost per cell step" units: multiply by the
planning cell size in metres. Fields returned to callers are on the ANALYSIS grid (each analysis
cell takes its planning cell's value), float32, np.inf where unreachable.

Sources outside passable cells: snap each source to the nearest passable planning cell within
20 m; if none, that source is reported in notes and ignored.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.spatial import cKDTree
from skimage.graph import MCP_Geometric

from .errors import AtlasError
from .refs import resolve_ref
from .sets import SetStore
from .site import Site

METRICS = ("straight_m", "walk_m", "walk_time_min")


SNAP_RADIUS_M = 20.0            # ASSUMPTION: how far (m) a source may be moved onto passable terrain before it is ignored
MULTI_SOURCE_THRESHOLD = 25    # above this many `from` members nearest() uses one multi-source Dijkstra (performance switch)
PLACEHOLDER_NOTE = ("Walking speed uses a PLACEHOLDER Tobler-shaped curve (speed = flat_speed * exp(-3.5 * tan(slope))), "
                    "isotropic (uphill = downhill).")
APPROX_NOTE = "Walk times and distances are approximate (4 m planning grid, 8-connected grid paths, isotropic speed)."


# ---- profile and planning grid ------------------------------------------

def _check_metric(metric: str) -> None:
    if metric not in METRICS:
        raise AtlasError(f'Unknown metric "{metric}".', hint="Use one of: " + ", ".join(METRICS) + ".")


def _travel_params(site: Site, profile: str) -> dict:
    prof = site.profiles.get(profile)
    travel = prof.get("travel") if isinstance(prof, dict) else None
    if not travel:
        ok = [n for n, p in site.profiles.items() if isinstance(p, dict) and p.get("travel")]
        raise AtlasError(f'Profile "{profile}" has no travel parameters.',
                         hint="Profiles with a travel block: " + (", ".join(sorted(ok)) or "none") + ".")
    # Thresholds may be plain numbers or {"value": x, "source": "..."} (provenance form).
    travel = {k: (v["value"] if isinstance(v, dict) and "value" in v else v) for k, v in travel.items()}
    for key in ("flat_speed_mps", "max_slope_deg"):
        if key not in travel:
            raise AtlasError(f'Profile "{profile}" travel block is missing "{key}".')
        if not isinstance(travel[key], (int, float)):
            raise AtlasError(f'Profile "{profile}" travel "{key}" must be a number, got {travel[key]!r}.')
    if not travel["flat_speed_mps"] > 0:
        raise AtlasError(f'Profile "{profile}" has a non-positive flat_speed_mps.')
    return travel


class _Plan:
    """Planning grid: block-reduced passability, max slope, and geometry."""

    def __init__(self, site: Site, travel: dict):
        slope = site.layer("slope_2m").data.astype(np.float64)
        n = site.size_px
        self.n = n
        self.k = k = max(1, int(round(4.0 / site.res_m)))
        self.cell_m = k * site.res_m
        passable = np.isfinite(slope) & (slope <= float(travel["max_slope_deg"]))
        m = -(-n // k)                      # ceil
        pad = m * k - n                     # edge blocks: padding does not exist, treat as passable/flat
        sl = np.where(np.isfinite(slope), slope, 0.0)
        if pad:
            passable = np.pad(passable, ((0, pad), (0, pad)), constant_values=True)
            sl = np.pad(sl, ((0, pad), (0, pad)), constant_values=0.0)
        self.m = m
        self.passable = passable.reshape(m, k, m, k).all(axis=(1, 3))
        self.slope = sl.reshape(m, k, m, k).max(axis=(1, 3))
        self.site = site

    def cell_centre_xy(self, i: int, j: int) -> tuple[float, float]:
        k, n = self.k, self.n
        r = i * k + (min(k, n - i * k) - 1) / 2.0
        c = j * k + (min(k, n - j * k) - 1) / 2.0
        x, y = self.site.rc_to_xy(r, c)
        return float(x), float(y)

    def costs(self, metric: str, travel: dict) -> np.ndarray:
        """Cost per planning-cell unit (1 per metre for walk_m, seconds per metre for time)."""
        if metric == "walk_m":
            c = np.ones(self.passable.shape, np.float64)
        else:
            # flat_speed_mps is from the profile (profiles.yaml); the exp(-3.5 tan) shape is the PLACEHOLDER curve,
            # isotropic. Cost = seconds per metre of path.
            speed = float(travel["flat_speed_mps"]) * np.exp(-3.5 * np.tan(np.radians(self.slope)))
            c = 1.0 / speed
        c[~self.passable] = np.inf
        return c

    def scale(self, metric: str) -> float:
        """Multiply MCP_Geometric cost (cost-units x cell steps) by this to get metres / minutes."""
        # MCP_Geometric sums cost x step length in CELLS (diagonals count sqrt(2)); x cell_m gives metres, and
        # seconds-per-metre cost x cell_m / 60 gives minutes.
        return self.cell_m if metric == "walk_m" else self.cell_m / 60.0

    def snap(self, x: float, y: float):
        """Nearest passable planning cell within SNAP_RADIUS_M of (x, y): (i, j, dist_m) or None."""
        r, c = self.site.rc_index(x, y)
        i = min(max(int(r) // self.k, 0), self.m - 1)
        j = min(max(int(c) // self.k, 0), self.m - 1)
        if self.passable[i, j]:
            return i, j, 0.0
        w = int(math.ceil(SNAP_RADIUS_M / self.cell_m)) + 1
        best = None
        for ii in range(max(0, i - w), min(self.m, i + w + 1)):
            for jj in range(max(0, j - w), min(self.m, j + w + 1)):
                if not self.passable[ii, jj]:
                    continue
                cx, cy = self.cell_centre_xy(ii, jj)
                d = math.hypot(cx - x, cy - y)
                if d <= SNAP_RADIUS_M and (best is None or d < best[2]):
                    best = (ii, jj, d)
        return best


def _snap_sources(plan: _Plan, sources: list[dict]):
    """Returns (snapped: list[(i, j) | None], notes)."""
    snapped, moved, dropped = [], [], 0
    for s in sources:
        res = plan.snap(float(s["x"]), float(s["y"]))
        if res is None:
            dropped += 1
            snapped.append(None)
        else:
            snapped.append((res[0], res[1]))
            if res[2] > 0:
                moved.append(res[2])
    notes = []
    if moved:
        notes.append(f"{len(moved)} location(s) were not on walkable ground and were moved to the nearest "
                     f"walkable cell (up to {max(moved):.0f} m).")
    if dropped:
        notes.append(f"{dropped} location(s) have no walkable ground within {SNAP_RADIUS_M:.0f} m and were ignored.")
    return snapped, notes


def _walk_plan_field(plan: _Plan, metric: str, travel: dict, cells: list[tuple[int, int]]):
    """Dijkstra from planning cells; returns (field in metres/minutes on the planning grid, mcp)."""
    mcp = MCP_Geometric(plan.costs(metric, travel), fully_connected=True)
    cost, _ = mcp.find_costs(sorted(set(cells)))
    return (cost * plan.scale(metric)).astype(np.float32), mcp


def _straight_field(site: Site, sources: list[dict]) -> np.ndarray:
    n = site.size_px
    cols = np.arange(n)
    xs = -site.half_m + (cols + 0.5) * site.res_m
    ys = site.half_m - (cols + 0.5) * site.res_m
    px = np.array([float(s["x"]) for s in sources])
    py = np.array([float(s["y"]) for s in sources])
    if len(sources) <= 8:
        d2 = np.full((n, n), np.inf)
        for sx, sy in zip(px, py):
            np.minimum(d2, ((ys - sy) ** 2)[:, None] + ((xs - sx) ** 2)[None, :], out=d2)
        return np.sqrt(d2).astype(np.float32)
    tree = cKDTree(np.column_stack([px, py]))
    out = np.empty((n, n), np.float32)
    step = max(1, 2_000_000 // n)
    for r0 in range(0, n, step):
        r1 = min(n, r0 + step)
        gx, gy = np.meshgrid(xs, ys[r0:r1])
        d, _ = tree.query(np.column_stack([gx.ravel(), gy.ravel()]))
        out[r0:r1] = d.reshape(r1 - r0, n)
    return out


def _expand(plan: _Plan, pf: np.ndarray) -> np.ndarray:
    full = np.repeat(np.repeat(pf, plan.k, axis=0), plan.k, axis=1)
    return np.ascontiguousarray(full[: plan.n, : plan.n])


def _field_and_notes(site: Site, sources: list[dict], metric: str, profile: str):
    _check_metric(metric)
    if not sources:
        raise AtlasError("No source locations were given.", hint="Pass a point, POI id or set id.")
    if metric == "straight_m":
        return _straight_field(site, sources), []
    travel = _travel_params(site, profile)
    plan = _Plan(site, travel)
    snapped, notes = _snap_sources(plan, sources)
    cells = [c for c in snapped if c is not None]
    if not cells:
        return np.full((plan.n, plan.n), np.inf, np.float32), notes
    pf, _ = _walk_plan_field(plan, metric, travel, cells)
    return _expand(plan, pf), notes


def travel_field(site: Site, sources: list[dict], metric: str, profile: str = "eva_walkable") -> np.ndarray:
    """Cost from the NEAREST of `sources` (each with x, y) to every analysis cell.
    Multi-source: one Dijkstra seeded from all sources at once."""
    return _field_and_notes(site, sources, metric, profile)[0]


def _ref_label(ref) -> str:
    if isinstance(ref, str):
        return ref
    if isinstance(ref, dict):
        if "x" in ref and "y" in ref:
            return f"({ref['x']:g}, {ref['y']:g}) m"
        if "lat" in ref and "lon" in ref:
            return f"({ref['lat']:g}, {ref['lon']:g})"
    if isinstance(ref, list):
        return _ref_label(ref[0]) if len(ref) == 1 else f"{len(ref)} locations"
    return "the given location"


def within_mask(site: Site, within: dict, store: SetStore | None) -> tuple[np.ndarray, np.ndarray, str]:
    """within = {"from": <location ref>, "max": number, "metric": one of METRICS,
                 "profile": optional, default "eva_walkable"}.
    Returns (mask = field <= max, field, label) where label is e.g.
    "within 60 walk_time_min of poi:base". Resolves `from` with atlas_core.refs.resolve_ref.
    Raises AtlasError for unknown metric (hint lists METRICS) or missing keys."""
    if not isinstance(within, dict):
        raise AtlasError("`within` must be an object.", hint='Example: {"from": "base", "max": 60, "metric": "walk_time_min"}.')
    missing = [k for k in ("from", "max", "metric") if k not in within]
    if missing:
        raise AtlasError("`within` is missing: " + ", ".join(missing) + ".",
                         hint='Example: {"from": "base", "max": 60, "metric": "walk_time_min"}.')
    metric = within["metric"]
    _check_metric(metric)
    try:
        limit = float(within["max"])
    except (TypeError, ValueError):
        raise AtlasError(f'`within.max` must be a number, got {within["max"]!r}.') from None
    if not math.isfinite(limit) or limit < 0:
        raise AtlasError("`within.max` must be a finite number >= 0.")
    sources = resolve_ref(site, within["from"], store)
    if not sources:
        raise AtlasError("`within.from` resolved to no locations.", hint="Check that the set is not empty.")
    field, notes = _field_and_notes(site, sources, metric, within.get("profile", "eva_walkable"))
    mask = field <= limit
    label = f"within {limit:g} {metric} of {_ref_label(within['from'])}"
    if notes:
        label += " (" + " ".join(notes) + ")"
    return mask, field, label


def _euclid(a: dict, b: dict) -> float:
    return math.hypot(float(a["x"]) - float(b["x"]), float(a["y"]) - float(b["y"]))


def _has_cells(m: dict) -> bool:
    c = m.get("_cells")
    return c is not None and len(c) > 0


def _cell_xy(site: Site, cells: np.ndarray):
    n = site.size_px
    cells = np.asarray(cells, np.int64)
    x, y = site.rc_to_xy(cells // n, cells % n)
    return np.asarray(x, np.float64), np.asarray(y, np.float64)


def _pairs_straight(site: Site, fp: list[dict], tp: list[dict]):
    """Returns (to_index, cost, from_entry, to_entry) per from member. Region members are measured
    cell to cell, so the cost is the gap between the nearest cells of the two members."""
    def pts(members):
        xs, ys, owner = [], [], []
        for i, m in enumerate(members):
            if _has_cells(m):
                x, y = _cell_xy(site, m["_cells"])
            else:
                x, y = np.array([float(m["x"])]), np.array([float(m["y"])])
            xs.append(x), ys.append(y), owner.append(np.full(len(x), i))
        return np.concatenate(xs), np.concatenate(ys), np.concatenate(owner)
    tx, ty, towner = pts(tp)
    fx, fy, fowner = pts(fp)
    tree = cKDTree(np.column_stack([tx, ty]))
    d, idx = tree.query(np.column_stack([fx, fy]))
    out = []
    for fi, f in enumerate(fp):
        sel = np.nonzero(fowner == fi)[0]
        k = sel[int(np.argmin(d[sel]))]
        h = int(idx[k])
        ti = int(towner[h])
        fe = (float(fx[k]), float(fy[k])) if _has_cells(f) else None
        te = (float(tx[h]), float(ty[h])) if _has_cells(tp[ti]) else None
        out.append((ti, float(d[k]), fe, te))
    return out


def _endpoints(plan: _Plan, members: list[dict], notes: list[str]):
    """Planning cells to seed from / read at, per member: (cells, entry) or None if unusable.
    A region (member with "_cells") uses every passable planning cell it touches; `entry` maps each of
    them to the (x, y) of a region cell inside it. Points snap to the nearest passable cell (<= 20 m);
    a region with no passable cell falls back to snapping its representative point."""
    out, moved, dropped = [], [], 0
    n, k, m_ = plan.n, plan.k, plan.m
    for mem in members:
        if _has_cells(mem):
            cells = np.asarray(mem["_cells"], np.int64)
            r, c = cells // n, cells % n
            pi, pj = r // k, c // k
            ok = plan.passable[pi, pj]
            if ok.any():
                r, c, pi, pj = r[ok], c[ok], pi[ok], pj[ok]
                d2 = (r - (pi * k + (k - 1) / 2.0)) ** 2 + (c - (pj * k + (k - 1) / 2.0)) ** 2
                key = pi * m_ + pj
                order = np.lexsort((c, r, d2, key))
                first = np.concatenate(([True], key[order][1:] != key[order][:-1]))
                sel = order[first]
                x, y = plan.site.rc_to_xy(r[sel], c[sel])
                entry = {(int(a), int(b)): (float(xx), float(yy)) for a, b, xx, yy in zip(pi[sel], pj[sel], x, y)}
                out.append((sorted(entry), entry))
                continue
        res = plan.snap(float(mem["x"]), float(mem["y"]))
        if res is None:
            dropped += 1
            out.append(None)
            continue
        if res[2] > 0:
            moved.append(res[2])
        out.append(([(res[0], res[1])], None))
    if moved:
        notes.append(f"{len(moved)} location(s) were not on walkable ground and were moved to the nearest "
                     f"walkable cell (up to {max(moved):.0f} m).")
    if dropped:
        notes.append(f"{dropped} location(s) have no walkable ground within {SNAP_RADIUS_M:.0f} m and were ignored.")
    return out


def _lex_min(pf: np.ndarray, cells: list[tuple[int, int]]):
    """Cell with the smallest finite pf value (ties: smallest cell), or None."""
    arr = np.asarray(cells)
    vals = pf[arr[:, 0], arr[:, 1]]
    fin = np.isfinite(vals)
    if not fin.any():
        return None
    arr, vals = arr[fin], vals[fin]
    o = np.lexsort((arr[:, 1], arr[:, 0], vals))[0]
    return (int(arr[o, 0]), int(arr[o, 1])), float(vals[o])


def _pairs_walk(site: Site, fp, tp, metric, profile, notes):
    """Returns (to_index or None, cost, from_entry, to_entry) per from member. Regions are measured
    to/from their nearest passable cell: a region seeds Dijkstra from all of its cells, or has the
    minimum read over all of its cells."""
    travel = _travel_params(site, profile)
    plan = _Plan(site, travel)
    f_eps = _endpoints(plan, fp, notes)
    t_eps = _endpoints(plan, tp, notes)
    by_cell: dict[tuple[int, int], list[int]] = {}
    for j, ep in enumerate(t_eps):
        if ep is not None:
            for c in ep[0]:
                by_cell.setdefault(c, []).append(j)
    none = (None, math.inf, None, None)
    out: list[tuple] = [none] * len(fp)
    if not by_cell:
        return out

    def pick(fi: int, members: list[int]) -> int:
        return min(members, key=lambda j: (_euclid(fp[fi], tp[j]), j))

    def entry_of(ep, cell):
        return ep[1][cell] if ep is not None and ep[1] is not None else None

    if len(fp) <= MULTI_SOURCE_THRESHOLD:
        cache: dict[tuple, tuple] = {}
        t_all = sorted(by_cell)
        for fi, ep in enumerate(f_eps):
            if ep is None:
                continue
            key = tuple(ep[0])
            if key not in cache:
                cache[key] = _walk_plan_field(plan, metric, travel, ep[0])
            pf, mcp = cache[key]
            best = _lex_min(pf, t_all)
            if best is None:
                continue
            tc, cost = best
            ti = pick(fi, by_cell[tc])
            fe = None
            if ep[1] is not None:
                fe = ep[1][tuple(int(v) for v in mcp.traceback(tc)[0])]
            out[fi] = (ti, cost, fe, entry_of(t_eps[ti], tc))
        return out

    pf, mcp = _walk_plan_field(plan, metric, travel, list(by_cell))
    for fi, ep in enumerate(f_eps):
        if ep is None:
            continue
        best = _lex_min(pf, ep[0])
        if best is None:
            continue
        fc, cost = best
        start = tuple(int(v) for v in mcp.traceback(fc)[0])
        ti = pick(fi, by_cell[start])
        out[fi] = (ti, cost, entry_of(ep, fc), entry_of(t_eps[ti], start))
    return out


def nearest(site: Site, store: SetStore, from_ref, to_ref, metric: str = "walk_time_min",
            profile: str = "eva_walkable", limit: int = 5) -> dict:
    """For each member of `from`, the nearest member of `to` by `metric`; pairs ranked by cost.

    Returns:
    {
      "set_id": "set:N",        # pairs set (kind "pairs"), all pairs ranked
      "count": int,
      "results": [{"id", "from_id", "from_name", "to_id", "to_name", "x", "y", "lat", "lon",
                   "cost", "cost_metric", "straight_m"}][:limit],
      "unreachable": [from_id, ...],      # from members with no reachable `to`
      "approximate": bool,                # True for walk metrics (isotropic speed, grid paths)
      "notes": [str],                     # e.g. snapped sources, speed curve is a placeholder
    }
    x, y, lat, lon are the `from` member's. Implementation: if len(from) <= 25 run one Dijkstra
    per `from` member and read cost at every `to` member; otherwise one multi-source Dijkstra from
    all `to` members plus per-cell source attribution.

    Region members (find_regions sets) are measured to their NEAREST CELL, not their representative
    point: a `from` region seeds the search from all its passable cells and a `to` region is read as
    the minimum over its cells. Such a pair also has "from_entry_x/y" / "to_entry_x/y": the cell of the
    region where the path starts / ends. "straight_m" is then measured between those entry cells."""
    _check_metric(metric)
    if not isinstance(limit, int) or limit < 1:
        raise AtlasError("`limit` must be an integer >= 1.")
    fp = resolve_ref(site, from_ref, store)
    tp = resolve_ref(site, to_ref, store)
    if not fp:
        raise AtlasError("`from` resolved to no locations.", hint="Check that the set is not empty.")
    if not tp:
        raise AtlasError("`to` resolved to no locations.", hint="Check that the set is not empty.")
    notes: list[str] = []
    if any("area_m2" in m and not _has_cells(m) for m in fp + tp):
        notes.append("Some regions are measured from their representative interior point, not their nearest "
                     "edge, so costs to large regions are overestimates.")
    if metric == "straight_m":
        res = _pairs_straight(site, fp, tp)
    else:
        res = _pairs_walk(site, fp, tp, metric, profile, notes)
        notes.append(PLACEHOLDER_NOTE if metric == "walk_time_min" else "Distances follow 4 m grid paths.")
        notes.append(APPROX_NOTE)
    members, unreachable = [], []
    for f, (ti, cost, f_entry, t_entry) in zip(fp, res):
        if ti is None or not math.isfinite(cost):
            unreachable.append(f.get("id"))
            continue
        t = tp[ti]
        pair = {
            "from_id": f.get("id"), "from_name": f.get("name"),
            "to_id": t.get("id"), "to_name": t.get("name"),
            "x": f["x"], "y": f["y"], "lat": f.get("lat"), "lon": f.get("lon"),
            "cost": round(float(cost), 3), "cost_metric": metric,
            "straight_m": round(_euclid(f_entry and {"x": f_entry[0], "y": f_entry[1]} or f,
                                        t_entry and {"x": t_entry[0], "y": t_entry[1]} or t), 2),
            "score": round(float(cost), 3),
        }
        if f_entry is not None:
            pair["from_entry_x"], pair["from_entry_y"] = round(f_entry[0], 2), round(f_entry[1], 2)
        if t_entry is not None:
            pair["to_entry_x"], pair["to_entry_y"] = round(t_entry[0], 2), round(t_entry[1], 2)
        for side, mem in (("from", f), ("to", t)):       # keep the evidence tiers of regions/spots (with provenance)
            if mem.get("evidence"):
                pair[f"{side}_evidence"] = mem["evidence"]
        if t.get("x") is not None:
            pair["to_x"], pair["to_y"] = t["x"], t["y"]
        members.append(pair)
    members.sort(key=lambda m: m["cost"])     # stable: ties keep `from` order
    ps = store.create("pairs", "nearest",
                      {"from": from_ref, "to": to_ref, "metric": metric, "profile": profile}, members)
    return {
        "set_id": ps.set_id,
        "count": ps.count,
        "results": ps.members[:limit],
        "unreachable": unreachable,
        "approximate": metric != "straight_m",
        "notes": notes,
    }

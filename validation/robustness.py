"""Scientific stress test of the Ares Atlas query engine (atlas_core).

Not "does the code run" but "do the answers make physical sense and are they robust".
Run from the repo root:

    .venv/bin/python validation/robustness.py [--runs 20] [--sections 1,2,3,4,5,6] [--out validation/results.json]

Sections (see docs/stress-test-robustness.md for method and results):
    1 invariance / metamorphic     2 coordinates vs pyproj       3 resolution consistency (1/2/4 m)
    4 DEM noise Monte Carlo        5 threshold sensitivity       6 sanity bounds

The engine is READ-ONLY here: noisy / rotated sites are built by copying the Site dataclass and replacing the
`elevation`, `slope_2m` and `rough_5x5` layers (the only DEM-derived layers the tools under test consume).
Everything is seeded (SEED below). Results are written as JSON; the doc quotes them.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import multiprocessing as mp
import os
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "pipeline"))

from atlas_core import Layer, SetStore, Site, load_site  # noqa: E402
from atlas_core import envelope, route, search, sun, terrain, travel, visibility  # noqa: E402

SITE_DIR = ROOT / "data/build/site/jezero"
RAW_HIRISE = ROOT / "data/raw/hirise/window_18.452_77.418_4096.tif"
SEED = 20261115
WORKERS = max(1, min(6, (os.cpu_count() or 2) - 2))

# Vertical precision of HiRISE stereo DTMs. Source OPENED during this work:
#   https://www.uahirise.org/dtm/about.php : "the post spacing is 1-2 m with vertical precision in the tens of centimeters."
# Mission goal (McEwen et al. 2007, HiRISE instrument paper, quoted second-hand, NOT opened): better than 0.25 m.
# The Kirk et al. 2008 (JGR 113, E00A24, doi:10.1029/2007JE003000) abstract was opened (USGS record) and gives slopes, not an
# explicit vertical error figure; the full text was not accessible (HTTP 403). So: sigma 0.25 m is ASSUMED, range 0.1-0.5 m.
SIGMA_Z = 0.25
CORR_SIGMA_M = 4.0           # Gaussian smoothing sigma of the correlated noise (m); ASSUMED (stereo correlation window)
NOISE_MODELS = {
    "white_0.25": dict(kind="white", sigma=0.25),       # worst case: independent errors at 2 m posts, no averaging gain
    "white_0.125": dict(kind="white", sigma=0.125),     # 0.25 m independent errors at 1 m posts averaged 2x2 to 2 m
    "corr_0.25": dict(kind="corr", sigma=0.25),         # spatially correlated (Gaussian, sigma 4 m), total sd 0.25 m
}

# ---------------------------------------------------------------------------------------------- helpers


def now():
    return time.perf_counter()


def site_area_m2(site: Site) -> float:
    return (site.size_px * site.res_m) ** 2


def clone_site(site: Site, layers: dict | None = None, profiles: dict | None = None) -> Site:
    """Shallow copy of a Site with some layer ids replaced (Layer objects) and/or profiles replaced."""
    L = dict(site.layers)
    if layers:
        L.update(layers)
    return replace(site, layers=L, profiles=profiles if profiles is not None else site.profiles,
                   markers=dict(site.markers), pois=list(site.pois))


def fast_rough(dem: np.ndarray, window: int = 5) -> np.ndarray:
    """Same quantity as terrain.roughness (population std in a window, edge-padded) via box filters (~10x faster)."""
    from scipy import ndimage
    z = dem.astype(np.float64)
    z = z - np.nanmean(z)
    m = ndimage.uniform_filter(z, window, mode="nearest")
    m2 = ndimage.uniform_filter(z * z, window, mode="nearest")
    return np.sqrt(np.clip(m2 - m * m, 0, None)).astype(np.float32)


def site_with_dem(site: Site, dem: np.ndarray, rough: np.ndarray | None = None) -> Site:
    dem = dem.astype(np.float32)
    sl = terrain.slope_deg(dem, site.res_m)
    rg = fast_rough(dem) if rough is None else rough
    meta = site.layers
    return clone_site(site, {
        "elevation": Layer("elevation", "continuous", dem, meta["elevation"].meta),
        "slope_2m": Layer("slope_2m", "continuous", sl, meta["slope_2m"].meta),
        "rough_5x5": Layer("rough_5x5", "continuous", rg, meta["rough_5x5"].meta),
    })


def synthetic_site(dem: np.ndarray, res_m: float, profiles: dict) -> Site:
    n = dem.shape[0]
    z = dem.astype(np.float32)
    meta = {"title": "synthetic", "units": "", "resolution_m": res_m}
    layers = {
        "elevation": Layer("elevation", "continuous", z, dict(meta)),
        "slope_2m": Layer("slope_2m", "continuous", terrain.slope_deg(z, res_m), dict(meta)),
        "rough_5x5": Layer("rough_5x5", "continuous", terrain.roughness(z), dict(meta)),
        "quality_flag": Layer("quality_flag", "categorical", np.zeros((n, n), np.int16), dict(meta),
                              classes={0: "hirise_dtm", 1: "ctx_fill", 2: "hirise_seam_suspect", 3: "hirise_noisy"}),
    }
    return Site(name="synthetic", lat0=18.452, lon0=77.418, res_m=res_m, size_px=n, layers=layers,
                profiles=profiles, pois=[])


def circ_diff(a, b):
    """Smallest absolute angle difference, degrees."""
    d = np.abs((a - b + 180.0) % 360.0 - 180.0)
    return d


def pct(a, qs=(50, 95, 99)):
    a = a[np.isfinite(a)]
    return {f"p{q}": float(np.percentile(a, q)) for q in qs}


def jaccard_tol(A, B, tol_m):
    """Jaccard of two point lists, one-to-one greedy nearest matching within tol_m (tol 0 = exact)."""
    A = [tuple(p) for p in A]
    B = [tuple(p) for p in B]
    used = set()
    m = 0
    for a in A:
        best, bd = None, 1e18
        for j, b in enumerate(B):
            if j in used:
                continue
            d = math.hypot(a[0] - b[0], a[1] - b[1])
            if d < bd:
                best, bd = j, d
        if best is not None and bd <= tol_m + 1e-9:
            used.add(best)
            m += 1
    u = len(A) + len(B) - m
    return (m / u if u else float('nan')), m


def valid_cells(diag, site):
    """Candidate-centre cell count from the engine diagnostics (last 'footprint' step)."""
    n2 = site.size_px ** 2
    for d in diag:
        if d["step"].startswith("+ footprint"):
            fp = d["pass_fraction"]
    return int(round(fp * n2))


def valid_cells_final(diag, site):
    return int(round(diag[-1]["pass_fraction"] * site.size_px ** 2))


def traverse_xy(site: Site, spacing=2.0):
    """Real Perseverance drive segments in the site, resampled every `spacing` m. Returns (N,2) site metres."""
    fc = json.loads((SITE_DIR / "traverse.geojson").read_text())
    pts = []
    for f in fc["features"]:
        g = f["geometry"]
        if g["type"] != "LineString":
            continue
        xy = np.array([site.latlon_to_xy(lat, lon) for lon, lat in g["coordinates"]])
        for a, b in zip(xy[:-1], xy[1:]):
            d = float(np.hypot(*(b - a)))
            if d > 25.0:      # join between separate drive segments (up to ~300 m straight line), not driven ground
                continue
            k = max(1, int(round(d / spacing)))
            t = (np.arange(k) / k)[:, None]
            pts.append(a + (b - a) * t)
    p = np.vstack(pts)
    inb = (np.abs(p[:, 0]) < site.half_m) & (np.abs(p[:, 1]) < site.half_m)
    return p[inb]


def sample_cells(site: Site, layer: str, xy: np.ndarray):
    r, c = site.rc_index(xy[:, 0], xy[:, 1])
    return site.layers[layer].data[np.clip(r, 0, site.size_px - 1), np.clip(c, 0, site.size_px - 1)]


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------------------------------------- section 1


def section1(site: Site, quick=False) -> dict:
    out: dict = {}
    t0 = now()
    rng = np.random.default_rng(SEED + 1)
    dem = site.layers["elevation"].data
    res = site.res_m

    # ---- 1a. synthetic analytic surfaces (exact answers) -------------------------------------
    n = 257
    c = (np.arange(n) - n // 2) * 2.0
    X, Y = np.meshgrid(c, -c)                      # Y north (row 0 = north)
    syn: dict = {}
    for th in (5, 15, 30, 45):
        for az in (0, 45, 90, 200, 315):           # downslope bearing, deg clockwise from north
            gx = -math.tan(math.radians(th)) * math.sin(math.radians(az))
            gn = -math.tan(math.radians(th)) * math.cos(math.radians(az))
            z = gx * X + gn * Y
            s = terrain.slope_deg(z, 2.0)[2:-2, 2:-2]
            a = terrain.aspect_deg(z, 2.0)[2:-2, 2:-2]
            syn[f"plane_{th}_{az}"] = dict(slope_err=float(np.abs(s - th).max()),
                                           aspect_err=float(circ_diff(a, az).max()))
    a_ = 0.002
    zp = a_ * (X ** 2 + Y ** 2)
    s = terrain.slope_deg(zp, 2.0)[2:-2, 2:-2]
    exact = np.degrees(np.arctan(2 * a_ * np.hypot(X, Y)))[2:-2, 2:-2]
    syn["paraboloid_slope_err_max"] = float(np.abs(s - exact).max())
    asp = terrain.aspect_deg(zp, 2.0)[2:-2, 2:-2]
    # downslope of a dome points toward the centre: bearing from cell to centre
    toward = (np.degrees(np.arctan2(-X, -Y)) % 360)[2:-2, 2:-2]
    ok = np.isfinite(asp)
    syn["paraboloid_aspect_err_max"] = float(circ_diff(asp[ok], toward[ok]).max())
    out["synthetic_surfaces"] = dict(
        plane_slope_err_max=max(v["slope_err"] for k, v in syn.items() if k.startswith("plane")),
        plane_aspect_err_max=max(v["aspect_err"] for k, v in syn.items() if k.startswith("plane")),
        paraboloid_slope_err_max=syn["paraboloid_slope_err_max"],
        paraboloid_aspect_err_max=syn["paraboloid_aspect_err_max"])

    # ---- 1b. rotate / transpose the real DEM --------------------------------------------------
    s0 = terrain.slope_deg(dem, res)
    a0 = terrain.aspect_deg(dem, res)
    transforms = {
        "rot90": (lambda m: np.rot90(m, 1), lambda a: (a - 90) % 360),
        "rot180": (lambda m: np.rot90(m, 2), lambda a: (a - 180) % 360),
        "rot270": (lambda m: np.rot90(m, 3), lambda a: (a - 270) % 360),
        "transpose": (lambda m: m.T, lambda a: (270 - a) % 360),
    }
    rot = {}
    for name, (T, amap) in transforms.items():
        d1 = np.ascontiguousarray(T(dem))
        s1 = terrain.slope_deg(d1, res)
        a1 = terrain.aspect_deg(d1, res)
        ds = np.abs(T(s0) - s1)
        exp_a = amap(T(a0))
        both = np.isfinite(exp_a) & np.isfinite(a1)
        mism = int((np.isfinite(exp_a) != np.isfinite(a1)).sum())
        dang = circ_diff(exp_a[both], a1[both])
        qs = np.percentile(s0, [1, 50, 90, 99])
        qs1 = np.percentile(s1, [1, 50, 90, 99])
        rot[name] = dict(slope_max_abs_diff_deg=float(ds.max()), slope_pct_max_diff=float(np.abs(qs - qs1).max()),
                         aspect_max_diff_deg=float(dang.max()), aspect_cells_gt_0p01deg=int((dang > 0.01).sum()),
                         aspect_defined_mismatch_cells=mism)
    out["rotation"] = rot
    out["rotation_note"] = "slope_deg edge-pads the border (rotation-symmetric); aspect_deg NaN-pads (border cells NaN both ways)."
    log(f"  1b rotation done {now()-t0:.0f}s")

    # ---- 1c/d. walk-time symmetry + triangle inequality (planning-grid Dijkstra, internal API) ------------
    from skimage.graph import MCP_Geometric
    tp = travel._travel_params(site, "eva_walkable")
    plan = travel._Plan(site, tp)
    cost_t = plan.costs("walk_time_min", tp)
    cost_d = plan.costs("walk_m", tp)
    # sample 14 passable planning cells in the large connected walkable component, central 7 km
    from scipy import ndimage
    lab, nl = ndimage.label(plan.passable, structure=np.ones((3, 3)))
    big = np.argmax(np.bincount(lab.ravel())[1:]) + 1
    m_ = plan.m
    ii, jj = np.nonzero(lab == big)
    sel = rng.choice(len(ii), 14, replace=False)
    cells = [(int(ii[k]), int(jj[k])) for k in sel]
    fields_t, fields_d = [], []
    for ce in cells:
        ft, _ = MCP_Geometric(cost_t, fully_connected=True).find_costs([ce])
        fd, _ = MCP_Geometric(cost_d, fully_connected=True).find_costs([ce])
        fields_t.append(ft * plan.scale("walk_time_min"))
        fields_d.append(fd * plan.scale("walk_m"))
    k = len(cells)
    asym_t, asym_d, asym_rel_t = [], [], []
    for i in range(k):
        for j in range(i + 1, k):
            a, b = fields_t[i][cells[j]], fields_t[j][cells[i]]
            asym_t.append(abs(a - b))
            asym_rel_t.append(abs(a - b) / max(a, b))
            asym_d.append(abs(fields_d[i][cells[j]] - fields_d[j][cells[i]]))
    # one planning-cell diagonal step at the worst cost endpoint, in minutes, as the natural tolerance
    cell_min = np.array([cost_t[c_] for c_ in cells]) * plan.cell_m * math.sqrt(2) / 60.0
    tol_min = float(cell_min.max())
    viol, worst, n_tri = 0, 0.0, 0
    viol_strict = 0
    for ia in range(k):
        for ib in range(k):
            for ic in range(k):
                if len({ia, ib, ic}) < 3:
                    continue
                n_tri += 1
                ac = fields_t[ia][cells[ic]]
                ab_bc = fields_t[ia][cells[ib]] + fields_t[ib][cells[ic]]
                ex = ac - ab_bc
                if ex > 1e-6:
                    viol_strict += 1
                if ex > tol_min:
                    viol += 1
                worst = max(worst, ex)
    out["walk_symmetry"] = dict(
        n_pairs=len(asym_t), max_abs_asym_min=float(max(asym_t)), max_rel_asym=float(max(asym_rel_t)),
        mean_rel_asym=float(np.mean(asym_rel_t)), max_abs_asym_walk_m=float(max(asym_d)),
        tolerance_one_diag_step_min=tol_min,
        typical_pair_time_min=float(np.mean([fields_t[i][cells[j]] for i in range(k) for j in range(k) if i != j])))
    out["triangle"] = dict(n_triples=n_tri, violations_gt_1e6_min=viol_strict, violations_gt_one_cell=viol,
                           worst_excess_min=float(worst), tolerance_min=tol_min)
    log(f"  1c/d symmetry+triangle done {now()-t0:.0f}s")

    # public-API cross-check of symmetry (nearest, both directions), 6 pairs
    pub = []
    pts = [plan.cell_centre_xy(*c_) for c_ in cells]
    for q in range(6):
        A, B = pts[2 * q], pts[2 * q + 1]
        st = SetStore()
        f = travel.nearest(site, st, {"x": A[0], "y": A[1]}, {"x": B[0], "y": B[1]}, metric="walk_time_min")
        g = travel.nearest(site, st, {"x": B[0], "y": B[1]}, {"x": A[0], "y": A[1]}, metric="walk_time_min")
        pub.append((f["results"][0]["cost"], g["results"][0]["cost"]))
    out["walk_symmetry_public_api"] = dict(pairs=[(round(a, 3), round(b, 3)) for a, b in pub],
                                           max_rel_diff=float(max(abs(a - b) / max(a, b) for a, b in pub)))

    # ---- 1e. find_locations monotonicity --------------------------------------------------------
    def fl(conditions=None, profile="landing_pad", R=50, within=None, **kw):
        st = SetStore()
        r = search.find_locations(site, st, conditions=conditions, profile=profile, footprint_radius_m=R,
                                  within=within, limit=5, **kw)
        return r, st

    mono = {}

    def run_series(name, variants, expect):
        counts, spots, vcells = [], [], []
        for lab_, kw in variants:
            r, _ = fl(**kw)
            counts.append(r["count"])
            vcells.append(valid_cells_final(r["diagnostics"], site))
            spots.append(r["count"])
        seq = vcells
        ok = all((seq[i + 1] >= seq[i]) if expect == "nondecreasing" else (seq[i + 1] <= seq[i])
                 for i in range(len(seq) - 1))
        sp_ok = all((spots[i + 1] >= spots[i]) if expect == "nondecreasing" else (spots[i + 1] <= spots[i])
                    for i in range(len(spots) - 1))
        mono[name] = dict(labels=[v[0] for v in variants], valid_candidate_cells=vcells, spot_counts=spots,
                          candidate_monotone=bool(ok), spot_count_monotone=bool(sp_ok))

    sl = lambda v: [{"layer": "slope_2m", "op": "<=", "value": v}]
    run_series("relax_slope", [(f"slope<={v}", dict(conditions=sl(v))) for v in (6, 8, 10, 12, 15)], "nondecreasing")
    run_series("relax_rough", [(f"rough<={v}", dict(conditions=[{"layer": "rough_5x5", "op": "<=", "value": v}]))
                               for v in (0.3, 0.5, 0.8, 1.2)], "nondecreasing")
    run_series("shrink_footprint", [(f"R={R}", dict(R=R)) for R in (100, 75, 50, 25, 10)], "nondecreasing")
    extra = [{"layer": "tpi_50m", "op": "<=", "value": 1.0}, {"layer": "local_relief_50m", "op": "<=", "value": 8},
             {"evidence": "carbonate", "min_tier": "weak_orbital"}]
    run_series("add_conditions", [(f"+{i} cond", dict(conditions=extra[:i])) for i in range(0, 4)], "nonincreasing")
    base_wi = {"from": "base", "metric": "walk_time_min"}
    run_series("relax_within_max", [(f"within {T} min", dict(within={**base_wi, "max": T})) for T in (20, 40, 80, 160)],
               "nondecreasing")
    # adding `within` never adds candidates: valid(with within) <= valid(without) at same other settings
    r0, _ = fl()
    v0 = valid_cells_final(r0["diagnostics"], site)
    rw, stw = fl(within={**base_wi, "max": 60})
    vw = valid_cells_final(rw["diagnostics"], site)
    # every spot found with `within` must also be a spot-candidate without it: footprint clear (no failing cell)
    mono["add_within"] = dict(valid_cells_without=v0, valid_cells_with=vw, spots_without=r0["count"],
                              spots_with=rw["count"], candidate_monotone=bool(vw <= v0),
                              spot_count_monotone=bool(rw["count"] <= r0["count"]))
    out["find_locations_monotonicity"] = mono
    log(f"  1e monotonicity done {now()-t0:.0f}s")

    # ---- 1f. nearest() is never farther than any other pair ------------------------------------------
    nn = {}
    pois = [(p["x"], p["y"]) for p in site.pois if p.get("type") != "base"][:30]
    pidx = rng.choice(len(pois), 14, replace=False)
    frm = [pois[i] for i in pidx[:6]]
    to = [pois[i] for i in pidx[8:]]
    st = SetStore()
    R_ = travel.nearest(site, st, [{"x": x, "y": y} for x, y in frm], [{"x": x, "y": y} for x, y in to],
                        metric="straight_m", limit=50)
    viol = 0
    for m in R_["results"]:
        brute = min(math.hypot(m["x"] - x, m["y"] - y) for x, y in to)
        if m["cost"] > brute + 0.01:
            viol += 1
    nn["straight"] = dict(n_from=len(frm), n_to=len(to), violations=viol)
    R_ = travel.nearest(site, st, [{"x": x, "y": y} for x, y in frm], [{"x": x, "y": y} for x, y in to],
                        metric="walk_time_min", limit=50)
    viol, gaps = 0, []
    for m in R_["results"]:
        costs = []
        f = travel.travel_field(site, [{"x": m["x"], "y": m["y"]}], "walk_time_min")
        for x, y in to:
            r_, c_ = site.rc_index(x, y)
            costs.append(float(f[int(r_), int(c_)]))
        brute = min(costs)
        gaps.append(m["cost"] - brute)
        if m["cost"] > brute + 0.05:
            viol += 1
    nn["walk_time"] = dict(n_from=len(frm), n_to=len(to), reported=len(R_["results"]), violations=viol,
                           max_excess_min=float(max(gaps)) if gaps else None, unreachable=len(R_["unreachable"]))
    out["nearest_minimality"] = nn
    log(f"  1f nearest done {now()-t0:.0f}s")

    # ---- 1g. line-of-sight reciprocity at equal heights --------------------------------------------------------
    ncheck = 150 if quick else 500
    half = site.half_m - 300
    rows, miss = [], []
    st = SetStore()
    for _ in range(ncheck):
        A = rng.uniform(-half, half, 2)
        ang, d = rng.uniform(0, 2 * np.pi), rng.uniform(80, 2500)
        B = A + d * np.array([np.sin(ang), np.cos(ang)])
        if not site.in_bounds(*B):
            continue
        h = 1.7 if len(rows) % 2 == 0 else 0.2      # alternate eye heights; low eyes make grazing rays common
        r1 = visibility.line_of_sight(site, {"x": A[0], "y": A[1]}, {"x": B[0], "y": B[1]}, h, h, st)
        r2 = visibility.line_of_sight(site, {"x": B[0], "y": B[1]}, {"x": A[0], "y": A[1]}, h, h, st)
        m1 = r1.get("min_clearance_m", -r1.get("clearance_deficit_m", 0.0))
        m2 = r2.get("min_clearance_m", -r2.get("clearance_deficit_m", 0.0))
        rows.append((d, r1["clear"], r2["clear"], m1, m2))
    arr = np.array([(r[0], r[1], r[2], r[3], r[4]) for r in rows], float)
    agree = arr[:, 1] == arr[:, 2]
    dis = ~agree
    bins = [(0, 300), (300, 800), (800, 1500), (1500, 2600)]
    per_bin = {f"{lo}-{hi}m": dict(n=int(((arr[:, 0] >= lo) & (arr[:, 0] < hi)).sum()),
                                   agree=float(agree[(arr[:, 0] >= lo) & (arr[:, 0] < hi)].mean()))
               for lo, hi in bins}
    margin_dis = np.minimum(np.abs(arr[dis, 3]), np.abs(arr[dis, 4])) if dis.any() else np.array([])
    out["los_reciprocity"] = dict(
        n_pairs=int(len(arr)), agreement=float(agree.mean()), n_disagree=int(dis.sum()),
        visible_fraction=float(arr[:, 1].mean()), per_distance=per_bin,
        disagree_median_abs_margin_m=float(np.median(margin_dis)) if margin_dis.size else None,
        disagree_max_abs_margin_m=float(margin_dis.max()) if margin_dis.size else None,
        clearance_asym_median_m=float(np.median(np.abs(arr[:, 3] - arr[:, 4]))),
        clearance_asym_p95_m=float(np.percentile(np.abs(arr[:, 3] - arr[:, 4]), 95)))
    # viewshed (radial sweep over the grid): reciprocity V_A(B) == V_B(A) at equal heights, and agreement with the exact
    # per-pair line of sight. Observers/targets are random; mask read from the stored region set.
    n_obs, n_tgt = (3, 15) if quick else (6, 30)
    n = site.size_px
    rec_agree = rec_n = 0
    vs_vs_los_agree = vs_vs_los_n = 0
    rec_dis = []
    st = SetStore()

    def vis_mask(P):
        v = visibility.viewshed(site, st, {"x": float(P[0]), "y": float(P[1])}, 1.7, 1.7, 1500, coarse=False)
        cells = st.get(v["set_id"]).members[0].get("_cells")
        m = np.zeros(n * n, bool)
        if cells is not None and len(cells):
            m[cells] = True
        return m

    for _ in range(n_obs):
        A = rng.uniform(-half + 1500 * 0.0, half, 2)
        mA = vis_mask(A)
        for _ in range(n_tgt):
            ang, d = rng.uniform(0, 2 * np.pi), rng.uniform(100, 1400)
            B = A + d * np.array([np.sin(ang), np.cos(ang)])
            if not site.in_bounds(*B):
                continue
            rb_, cb_ = (int(v) for v in site.rc_index(B[0], B[1]))
            ra_, ca_ = (int(v) for v in site.rc_index(A[0], A[1]))
            mB = vis_mask(B)
            ab, ba = bool(mA[rb_ * n + cb_]), bool(mB[ra_ * n + ca_])
            rec_n += 1
            rec_agree += int(ab == ba)
            if ab != ba:
                rec_dis.append(round(d))
            los = visibility.line_of_sight(site, {"x": A[0], "y": A[1]}, {"x": B[0], "y": B[1]}, 1.7, 1.7, st)["clear"]
            vs_vs_los_n += 1
            vs_vs_los_agree += int(ab == los)
    out["viewshed_reciprocity"] = dict(n_pairs=rec_n, agreement=(rec_agree / rec_n if rec_n else None),
                                       disagree_distances_m=rec_dis,
                                       viewshed_vs_line_of_sight_agreement=(vs_vs_los_agree / vs_vs_los_n if vs_vs_los_n else None))
    log(f"  1g visibility done {now()-t0:.0f}s")

    # ---- 1h. walk metric direction bias on a synthetic plane (grid-path anisotropy) ------------------------------
    nS = 600
    zc = np.zeros((nS, nS), np.float32)
    syn_site = synthetic_site(zc, 2.0, copy.deepcopy(site.profiles))
    bias = {}
    for deg in (0, 10, 22.5, 30, 45, 60, 67.5, 80, 90):
        d = 500.0
        B = (d * math.sin(math.radians(deg)), d * math.cos(math.radians(deg)))
        st = SetStore()
        r = travel.nearest(syn_site, st, {"x": -B[0] / 2, "y": -B[1] / 2}, {"x": B[0] / 2, "y": B[1] / 2},
                           metric="walk_m")["results"][0]
        bias[str(deg)] = round(r["cost"] / r["straight_m"], 4)
    out["walk_metric_direction_bias"] = dict(ratio_walk_over_straight_by_bearing=bias,
                                             octile_theoretical_max=1.0824)
    out["runtime_s"] = round(now() - t0, 1)
    return out


# ---------------------------------------------------------------------------------------------- section 2


def section2(site: Site) -> dict:
    import site_cfg
    from pyproj import CRS, Transformer
    t0 = now()
    out = {}
    geo = CRS.from_string(f"+proj=longlat +R={site_cfg.R} +no_defs")
    proj = CRS.from_string(site_cfg.CRS)
    fwd = Transformer.from_crs(geo, proj, always_xy=True)
    inv = Transformer.from_crs(proj, geo, always_xy=True)
    h = site.half_m
    g = np.linspace(-h, h, 41)
    X, Y = np.meshgrid(g, g)
    X, Y = X.ravel(), Y.ravel()
    ll = np.array([site.xy_to_latlon(x, y) for x, y in zip(X, Y)])           # (lat, lon)
    back = np.array([site.latlon_to_xy(a, b) for a, b in ll])
    rt = np.hypot(back[:, 0] - X, back[:, 1] - Y)
    lon_p, lat_p = inv.transform(X, Y)
    # our latlon vs pyproj latlon, expressed in metres on the ground
    m_per_deg = site_cfg.R * math.radians(1)
    dlat_m = (ll[:, 0] - lat_p) * m_per_deg
    dlon_m = (ll[:, 1] - lon_p) * m_per_deg * np.cos(np.radians(lat_p))
    xp, yp = fwd.transform(ll[:, 1], ll[:, 0])                                # pyproj forward of OUR latlon
    fwd_err = np.hypot(xp - X, yp - Y)
    # our latlon_to_xy of the pyproj latlon vs the true xy
    ours_fwd = np.array([site.latlon_to_xy(a, b) for a, b in zip(lat_p, lon_p)])
    ours_err = np.hypot(ours_fwd[:, 0] - X, ours_fwd[:, 1] - Y)
    out["grid_points"] = int(len(X))
    out["round_trip_max_m"] = float(rt.max())
    out["xy_to_latlon_vs_pyproj_max_m"] = float(np.hypot(dlat_m, dlon_m).max())
    out["latlon_to_xy_vs_pyproj_max_m"] = float(ours_err.max())
    out["pyproj_forward_of_our_latlon_max_m"] = float(fwd_err.max())
    out["origin_maps_to"] = [float(v) for v in fwd.transform(site.lon0, site.lat0)]
    # also: the raster georeferencing of the site file against our cell-centre convention
    import rasterio
    with rasterio.open(SITE_DIR / "elevation.tif") as d:
        tr = d.transform
        rr, cc = np.array([0, 100, 2048, 4095]), np.array([0, 700, 2048, 4095])
        gx = tr.c + (cc + 0.5) * tr.a
        gy = tr.f + (rr + 0.5) * tr.e
        ox, oy = site.rc_to_xy(rr, cc)
        out["geotiff_vs_rc_to_xy_max_m"] = float(np.hypot(gx - ox, gy - oy).max())
        # rc round trip
        r_, c_ = site.xy_to_rc(ox, oy)
        out["rc_roundtrip_max_cells"] = float(max(np.abs(r_ - rr).max(), np.abs(c_ - cc).max()))
    # the 8 km square at lat0 in lat/lon, vs a true great-circle (sphere) distance, to quantify the flat-frame error
    def haversine(lat1, lon1, lat2, lon2):
        p1, p2 = math.radians(lat1), math.radians(lat2)
        a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
        return 2 * site_cfg.R * math.asin(math.sqrt(a))
    errs = []
    rng = np.random.default_rng(SEED + 2)
    for _ in range(300):
        a = rng.uniform(-h, h, 2)
        b = rng.uniform(-h, h, 2)
        la, lb = site.xy_to_latlon(*a), site.xy_to_latlon(*b)
        errs.append(abs(haversine(*la, *lb) - math.hypot(*(a - b))) / max(1.0, math.hypot(*(a - b))))
    out["flat_frame_vs_great_circle_max_rel_dist_error"] = float(max(errs))
    out["runtime_s"] = round(now() - t0, 1)
    return out


# ---------------------------------------------------------------------------------------------- section 3

ROVER = dict(caution=15.0, nogo=20.0)


def _classes(s, caution=15.0, nogo=20.0):
    return np.where(s > nogo, 2, np.where(s > caution, 1, 0)).astype(np.int8)


def _read_1m(x0, y0, size):
    import rasterio
    from rasterio.crs import CRS
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject
    import site_cfg
    dst = np.full((size, size), np.nan, np.float32)
    with rasterio.open(RAW_HIRISE) as d:
        reproject(rasterio.band(d, 1), dst, dst_transform=from_origin(x0, y0, 1.0, 1.0),
                  dst_crs=CRS.from_string(site_cfg.CRS), dst_nodata=np.nan, src_nodata=d.nodata,
                  resampling=Resampling.bilinear)
    return dst


def _block(z, f):
    n = z.shape[0] // f * f
    return z[:n, :n].reshape(n // f, f, n // f, f).mean(axis=(1, 3))


def section3(site: Site) -> dict:
    t0 = now()
    out: dict = {"windows": {}}
    if not RAW_HIRISE.exists():
        return {"skipped": f"{RAW_HIRISE} not found"}
    sl = site.layers["slope_2m"].data
    # choose 1024 m windows aligned to the site grid: landing area, steepest block, median block
    blk = 512
    nb = site.size_px // blk
    means = np.array([[np.nanmean(sl[i * blk:(i + 1) * blk, j * blk:(j + 1) * blk]) for j in range(nb)] for i in range(nb)])
    order = np.dstack(np.unravel_index(np.argsort(means.ravel()), means.shape))[0]
    steep = tuple(order[-1])
    med = tuple(order[len(order) // 2])
    flat = tuple(order[0])
    wins = {
        "landing_area": (1332.0, 80.0),
        "steepest_block": (-site.half_m + steep[1] * 1024.0, site.half_m - steep[0] * 1024.0),
        "median_block": (-site.half_m + med[1] * 1024.0, site.half_m - med[0] * 1024.0),
        "flattest_block": (-site.half_m + flat[1] * 1024.0, site.half_m - flat[0] * 1024.0),
    }
    size = 1024
    for name, (x0, y0) in wins.items():
        z1 = _read_1m(x0, y0, size)
        if not np.isfinite(z1).all():
            out["windows"][name] = {"skipped": f"{np.isnan(z1).mean():.1%} NaN in reprojected window"}
            continue
        z2, z4 = _block(z1, 2), _block(z1, 4)
        s1, s2, s4 = terrain.slope_deg(z1, 1.0), terrain.slope_deg(z2, 2.0), terrain.slope_deg(z4, 4.0)
        # check our 2 m reproduction against the engine's own layer
        r0, c0 = int(round((site.half_m - y0) / 2)), int(round((x0 + site.half_m) / 2))
        site_sl = sl[r0:r0 + 512, c0:c0 + 512]
        site_z = site.layers["elevation"].data[r0:r0 + 512, c0:c0 + 512]
        crop = slice(4, -4)
        rep = dict(elev_rms_vs_site_m=float(np.sqrt(np.mean((z2 - site_z) ** 2))),
                   slope_max_abs_diff_deg=float(np.abs(s2[crop, crop] - site_sl[crop, crop]).max()),
                   slope_rmse_deg=float(np.sqrt(np.mean((s2[crop, crop] - site_sl[crop, crop]) ** 2))))
        # compare on 2 m grid: 1 m slope averaged 2x2 vs native 2 m slope; on 4 m grid: 2 m averaged 2x2 vs native 4 m
        s1_on2 = _block(s1, 2)[2:-2, 2:-2]
        s2_c = s2[2:-2, 2:-2]
        s2_on4 = _block(s2, 2)[1:-1, 1:-1]
        s4_c = s4[1:-1, 1:-1]
        s1_on4 = _block(s1, 4)[1:-1, 1:-1]

        def cmp(a, b):
            n_ = min(a.shape[0], b.shape[0])
            a, b = a[:n_, :n_], b[:n_, :n_]
            ca, cb = _classes(a), _classes(b)
            ch = ca != cb
            return dict(mean_diff_deg=float(np.mean(a - b)), rmse_deg=float(np.sqrt(np.mean((a - b) ** 2))),
                        corr=float(np.corrcoef(a.ravel(), b.ravel())[0, 1]),
                        class_changed_frac=float(ch.mean()),
                        to_safer_frac=float((cb < ca).mean()), to_steeper_frac=float((cb > ca).mean()))

        def stats(s):
            return dict(**pct(s), gt10=float((s > 10).mean()), gt15=float((s > 15).mean()),
                        gt20=float((s > 20).mean()), gt25=float((s > 25).mean()))

        out["windows"][name] = dict(
            origin_xy_m=[x0, y0], reproduction_vs_site_layer=rep,
            slope_stats={"1m": stats(s1[4:-4, 4:-4]), "2m": stats(s2[2:-2, 2:-2]), "4m": stats(s4[1:-1, 1:-1])},
            one_vs_two_m_on_2m_grid=cmp(s1_on2, s2_c),
            two_vs_four_m_on_4m_grid=cmp(s2_on4, s4_c),
            one_vs_four_m_on_4m_grid=cmp(s1_on4, s4_c),
            pad_eligible_le10=dict(m1=float((s1 <= 10).mean()), m2=float((s2 <= 10).mean()), m4=float((s4 <= 10).mean())))
        log(f"  3 {name} done {now()-t0:.0f}s")
    out["runtime_s"] = round(now() - t0, 1)
    return out


# ---------------------------------------------------------------------------------------------- section 4

_BASE: dict = {}


def make_noise(model: dict, shape, res, seed):
    from scipy import ndimage
    rng = np.random.default_rng(seed)
    w = rng.normal(0.0, 1.0, shape)
    if model["kind"] == "corr":
        w = ndimage.gaussian_filter(w, CORR_SIGMA_M / res, mode="reflect")
        w /= w.std()
    return (w * model["sigma"]).astype(np.float32)


def _base_xy(res):
    return None


def _top_xy(r):
    return [(m["x"], m["y"]) for m in r["results"]]


def mc_job(args):
    model_name, seed = args
    B = _BASE
    site = B["site"]
    dem = B["dem"]
    nz = make_noise(NOISE_MODELS[model_name], dem.shape, site.res_m, seed)
    nsite = site_with_dem(site, dem + nz, B["rough_clean"] if False else None)
    sl_b = B["slope"]
    sl_n = nsite.layers["slope_2m"].data
    res = {"model": model_name, "seed": seed}
    res["slope_bias_deg"] = float(np.mean(sl_n - sl_b))
    res["slope_mae_deg"] = float(np.mean(np.abs(sl_n - sl_b)))
    for pname, (cau, ng) in {"rover_scout": (15.0, 20.0), "eva_walkable": (15.0, 25.0)}.items():
        cb, cn = _classes(sl_b, cau, ng), _classes(sl_n, cau, ng)
        res[f"{pname}_class_changed"] = float((cb != cn).mean())
        res[f"{pname}_nogo_cells_retained"] = float(((cb == 2) & (cn == 2)).sum() / max((cb == 2).sum(), 1))
        res[f"{pname}_nogo_area_ratio"] = float((cn == 2).sum() / max((cb == 2).sum(), 1))
    res["pad_slope_le10_flip"] = float(((sl_b <= 10) != (sl_n <= 10)).mean())
    res["pad_slope_le10_frac"] = float((sl_n <= 10).mean())
    st = SetStore()
    r = search.find_locations(nsite, st, profile="landing_pad", limit=10)
    res["pad_count"] = r["count"]
    res["pad_valid_cells"] = valid_cells(r["diagnostics"], nsite)
    res["top10"] = _top_xy(r)
    res["top10_slope"] = [m["attributes"]["slope_2m_mean"] for m in r["results"]]
    k_ = int(round(50.0 / site.res_m))
    rough_n = nsite.layers["rough_5x5"].data
    yy, xx = np.mgrid[-k_:k_ + 1, -k_:k_ + 1]
    disc = (yy ** 2 + xx ** 2) <= k_ ** 2
    still = 0
    for (x, y) in B["base_top"]:
        r0, c0 = (int(v) for v in site.rc_index(x, y))
        ws, wr = sl_n[r0 - k_:r0 + k_ + 1, c0 - k_:c0 + k_ + 1], rough_n[r0 - k_:r0 + k_ + 1, c0 - k_:c0 + k_ + 1]
        still += int(ws[disc].max() <= 10.0 and wr[disc].max() <= 0.5)
    res["base_top10_still_valid"] = still / max(len(B["base_top"]), 1)
    rts = []
    for (A, Bp) in B["pairs"]:
        rr = route.plan_route(nsite, SetStore(), {"x": A[0], "y": A[1]}, {"x": Bp[0], "y": Bp[1]}, profile="rover_scout")
        rts.append((bool(rr["feasible"]), rr.get("distance_m"), rr.get("duration_min")))
    res["routes"] = rts
    e = envelope.eva_envelope(nsite, SetStore(), "base", budget_min=120.0)
    res["envelope_120_area_m2"] = e["area_m2"]
    return res


def section4(site: Site, runs: int) -> dict:
    t0 = now()
    out: dict = {"runs_per_model": runs, "seed": SEED, "models": NOISE_MODELS, "workers": WORKERS,
                 "sigma_source": ("uahirise.org/dtm/about.php (opened): 'vertical precision in the tens of centimeters'; "
                                  "McEwen 2007 goal <0.25 m (second-hand); sigma 0.25 m ASSUMED, range 0.1-0.5 m"),
                 "corr_sigma_m": CORR_SIGMA_M}
    dem = site.layers["elevation"].data
    base_slope = site.layers["slope_2m"].data
    # baseline answers
    st = SetStore()
    b = search.find_locations(site, st, profile="landing_pad", limit=10)
    base_top = _top_xy(b)
    # fixed A->B pairs for the rover (rover_scout), feasible at baseline
    rng = np.random.default_rng(SEED + 4)
    rp, _ = route.route_params(site, "rover_scout")
    plan = travel._Plan(site, rp)
    ii, jj = np.nonzero(plan.passable)
    pairs, base_routes = [], []
    tries = 0
    while len(pairs) < 5 and tries < 400:
        tries += 1
        k1, k2 = rng.choice(len(ii), 2)
        A = plan.cell_centre_xy(ii[k1], jj[k1])
        Bp = plan.cell_centre_xy(ii[k2], jj[k2])
        d = math.hypot(A[0] - Bp[0], A[1] - Bp[1])
        if not (1500 <= d <= 3500):
            continue
        rr = route.plan_route(site, SetStore(), {"x": A[0], "y": A[1]}, {"x": Bp[0], "y": Bp[1]}, profile="rover_scout")
        if rr["feasible"]:
            pairs.append((A, Bp))
            base_routes.append((rr["distance_m"], rr["duration_min"], d))
    e0 = envelope.eva_envelope(site, SetStore(), "base", budget_min=120.0)
    out["baseline"] = dict(top10=base_top, pad_count=b["count"], pad_valid_cells=valid_cells(b["diagnostics"], site),
                           routes=[dict(A=p[0], B=p[1], distance_m=r[0], duration_min=r[1], straight_m=r[2])
                                   for p, r in zip(pairs, base_routes)],
                           envelope_120_area_m2=e0["area_m2"], envelope_120_touches_edge=e0["touches_site_edge"])
    # rough_5x5 fast vs exact (so the Monte Carlo's faster roughness is validated)
    rg_fast = fast_rough(dem)
    out["fast_rough_check_max_abs_diff_m"] = float(np.nanmax(np.abs(rg_fast - site.layers["rough_5x5"].data)))
    _BASE.update(site=site, dem=dem, slope=base_slope, pairs=pairs, rough_clean=None, base_top=base_top)

    jobs = [(m, SEED * 100 + 17 * i + hash_m) for hash_m, m in enumerate(NOISE_MODELS) for i in range(runs)]
    ctx = mp.get_context("fork")
    with ctx.Pool(WORKERS) as pool:
        results = pool.map(mc_job, jobs, chunksize=1)
    log(f"  4 MC {len(jobs)} runs done {now()-t0:.0f}s")

    summary = {}
    for m in NOISE_MODELS:
        R = [r for r in results if r["model"] == m]
        s = {}
        for key in ("slope_bias_deg", "slope_mae_deg", "rover_scout_class_changed", "eva_walkable_class_changed",
                    "rover_scout_nogo_cells_retained", "eva_walkable_nogo_cells_retained",
                    "rover_scout_nogo_area_ratio", "eva_walkable_nogo_area_ratio", "pad_slope_le10_flip",
                    "pad_slope_le10_frac", "pad_count", "pad_valid_cells", "envelope_120_area_m2", "base_top10_still_valid"):
            v = np.array([r[key] for r in R], float)
            s[key] = dict(mean=float(v.mean()), sd=float(v.std(ddof=1)) if len(v) > 1 else 0.0,
                          min=float(v.min()), max=float(v.max()))
        js_t, js_e, mt, me = [], [], [], []
        top_in_base = []
        for r in R:
            j, mm = jaccard_tol(base_top, r["top10"], 25.0)
            js_t.append(j)
            mt.append(mm)
            j, mm = jaccard_tol(base_top, r["top10"], 0.0)
            js_e.append(j)
            me.append(mm)
        s["top10_jaccard_tol25m"] = dict(mean=float(np.nanmean(js_t)), min=float(np.nanmin(js_t)), max=float(np.nanmax(js_t)))
        s["top10_jaccard_exact"] = dict(mean=float(np.nanmean(js_e)), min=float(np.nanmin(js_e)), max=float(np.nanmax(js_e)))
        # pairwise stability between noisy runs (no reference to baseline)
        pj = []
        for a in range(len(R)):
            for c in range(a + 1, len(R)):
                pj.append(jaccard_tol(R[a]["top10"], R[c]["top10"], 25.0)[0])
        pj = [v for v in pj if not math.isnan(v)]
        s["top10_pairwise_jaccard_tol25m_mean"] = float(np.mean(pj)) if pj else None
        sl_ = [np.mean(r["top10_slope"]) for r in R if r["top10_slope"]]
        s["top10_mean_slope_of_pads_deg"] = float(np.mean(sl_)) if sl_ else None
        s["runs_with_zero_pads"] = int(sum(1 for r in R if r["pad_count"] == 0))
        rts = []
        for k in range(len(pairs)):
            ok = [r["routes"][k] for r in R]
            feas = [o for o in ok if o[0]]
            d = np.array([o[1] for o in feas], float)
            t = np.array([o[2] for o in feas], float)
            bd, bt = base_routes[k][0], base_routes[k][1]
            rts.append(dict(pair=k, baseline_distance_m=bd, baseline_duration_min=bt, feasible_runs=len(feas), runs=len(ok),
                            dist_mean=float(d.mean()) if len(d) else None, dist_sd=float(d.std(ddof=1)) if len(d) > 1 else None,
                            dist_cv=float(d.std(ddof=1) / d.mean()) if len(d) > 1 else None,
                            time_mean=float(t.mean()) if len(t) else None,
                            time_cv=float(t.std(ddof=1) / t.mean()) if len(t) > 1 else None,
                            time_rel_shift=float((t.mean() - bt) / bt) if len(t) else None,
                            dist_rel_shift=float((d.mean() - bd) / bd) if len(d) else None,
                            time_max_rel_dev=float(np.max(np.abs(t - bt) / bt)) if len(t) else None))
        s["routes"] = rts
        a0 = out["baseline"]["envelope_120_area_m2"]
        ev = np.array([r["envelope_120_area_m2"] for r in R])
        s["envelope_120_rel_shift_mean"] = float((ev.mean() - a0) / a0)
        s["envelope_120_cv"] = float(ev.std(ddof=1) / ev.mean()) if len(ev) > 1 else 0.0
        summary[m] = s
    out["summary"] = summary

    # cheap slope-only sensitivity to sigma (white noise, 3 seeds each)
    sens = {}
    for sg in (0.05, 0.1, 0.25, 0.5):
        v = []
        for k in range(3):
            nz = make_noise(dict(kind="white", sigma=sg), dem.shape, site.res_m, SEED + 900 + k)
            sn = terrain.slope_deg(dem + nz, site.res_m)
            cb, cn = _classes(base_slope), _classes(sn)
            v.append((float((cb != cn).mean()), float(np.mean(sn - base_slope)), float(((base_slope <= 10) != (sn <= 10)).mean())))
        v = np.array(v)
        sens[str(sg)] = dict(rover_class_changed=float(v[:, 0].mean()), slope_bias_deg=float(v[:, 1].mean()),
                             pad_le10_flip=float(v[:, 2].mean()))
    out["white_sigma_sweep"] = sens
    out["runtime_s"] = round(now() - t0, 1)
    return out


# ---------------------------------------------------------------------------------------------- section 5


def section5(site: Site) -> dict:
    t0 = now()
    out: dict = {}
    # ---- 5a. landing pad slope x footprint --------------------------------------------------------
    def pads(slope_v, R):
        st = SetStore()
        r = search.find_locations(site, st, conditions=[{"layer": "slope_2m", "op": "<=", "value": slope_v}],
                                  profile="landing_pad", footprint_radius_m=R, limit=20)
        return r

    base = pads(10, 50)
    base_top5 = _top_xy({"results": base["results"][:5]})
    grid = {}
    for sv in (8, 10, 12):
        for R in (25, 50, 75):
            r = base if (sv, R) == (10, 50) else pads(sv, R)
            top5 = _top_xy({"results": r["results"][:5]})
            top20 = _top_xy(r)
            j5, m5 = jaccard_tol(base_top5, top5, 25.0)
            _, m20 = jaccard_tol(base_top5, top20, 25.0)
            grid[f"slope<={sv},R={R}"] = dict(
                slope=sv, R=R, spots=r["count"], candidate_cells=valid_cells(r["diagnostics"], site),
                candidate_frac=valid_cells(r["diagnostics"], site) / site.size_px ** 2,
                top5_overlap_with_baseline_tol25m=m5, baseline_top5_found_in_top20=m20,
                top5_mean_slope=float(np.mean([m["attributes"]["slope_2m_mean"] for m in r["results"][:5]])),
                top5=[(round(a), round(b)) for a, b in top5])
    out["landing_pad_grid"] = grid
    out["landing_pad_baseline_top5"] = [(round(a), round(b)) for a, b in base_top5]
    log(f"  5a done {now()-t0:.0f}s")

    # ---- 5b. EVA max slope -> envelope area ----------------------------------------------------------------
    eva = {}
    for ms in (20, 25, 30):
        prof = copy.deepcopy(site.profiles)
        prof["eva_walkable"]["travel"]["max_slope_deg"]["value"] = ms
        s2 = clone_site(site, profiles=prof)
        row = {}
        for bud in (60, 120, 480):
            e = envelope.eva_envelope(s2, SetStore(), "base", budget_min=float(bud))
            row[f"budget_{bud}min"] = dict(area_km2=e["area_km2"], radius_m=e["max_one_way_radius_m"],
                                           touches_edge=e["touches_site_edge"])
        # share of the site that is passable at the 4 m planning grid
        tp = travel._travel_params(s2, "eva_walkable")
        pl = travel._Plan(s2, tp)
        row["planning_passable_frac"] = float(pl.passable.mean())
        eva[str(ms)] = row
    out["eva_envelope_by_max_slope"] = eva
    log(f"  5b done {now()-t0:.0f}s")

    # ---- 5c. rover no-go threshold -> share of real traverse -------------------------------------------------
    xy = traverse_xy(site)
    sl = sample_cells(site, "slope_2m", xy)
    sl_site = site.layers["slope_2m"].data
    tr = {"n_samples": int(len(xy)), "traverse_length_km": float(len(xy) * 2.0 / 1000.0)}
    for t in (10, 15, 20, 25):
        share_tr = float((sl > t).mean())
        share_site = float((sl_site > t).mean())
        tr[f"gt{t}"] = dict(traverse=share_tr, site=share_site,
                            ratio=(share_site / share_tr if share_tr > 0 else None),
                            traverse_samples=int((sl > t).sum()))
    # bootstrap by sol segment is not available here; use block bootstrap over 50-sample (100 m) blocks
    rng = np.random.default_rng(SEED + 5)
    nb = len(sl) // 50
    blocks = sl[:nb * 50].reshape(nb, 50)
    for t in (15, 20):
        v = []
        for _ in range(500):
            idx = rng.integers(0, nb, nb)
            v.append((blocks[idx] > t).mean())
        tr[f"gt{t}"]["traverse_share_ci95"] = [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))]
    out["rover_traverse"] = tr
    out["runtime_s"] = round(now() - t0, 1)
    return out


# ---------------------------------------------------------------------------------------------- section 6


def section6(site: Site) -> dict:
    t0 = now()
    out: dict = {}
    bad = []
    ranges = {}
    z = site.layers["elevation"].data
    zmin, zmax = float(np.nanmin(z)), float(np.nanmax(z))
    for lid, lay in site.layers.items():
        d = lay.data
        if lay.kind == "continuous":
            f = d[np.isfinite(d)]
            ranges[lid] = (float(f.min()), float(f.max()), float((~np.isfinite(d)).mean()))
        else:
            ranges[lid] = (float(d.min()), float(d.max()), None)

    def chk(name, cond, detail=""):
        out.setdefault("checks", []).append(dict(name=name, ok=bool(cond), detail=detail))
        if not cond:
            bad.append(name)

    sl = site.layers["slope_2m"].data
    chk("slope_2m in [0, 90]", ranges["slope_2m"][0] >= 0 and ranges["slope_2m"][1] <= 90, str(ranges["slope_2m"]))
    a = ranges["aspect_deg"]
    chk("aspect_deg in [0, 360)", a[0] >= 0 and a[1] < 360, str(a))
    chk("aspect_class in {-1..8}", ranges["aspect_class"][0] >= -1 and ranges["aspect_class"][1] <= 8, str(ranges["aspect_class"]))
    chk("rough_5x5 >= 0", ranges["rough_5x5"][0] >= 0, str(ranges["rough_5x5"]))
    chk("tri >= 0", ranges["tri"][0] >= 0, str(ranges["tri"]))
    chk("local_relief_50m/200m >= 0", ranges["local_relief_50m"][0] >= 0 and ranges["local_relief_200m"][0] >= 0)
    chk("relief_200 >= relief_50 where both finite",
        bool(np.nanmin(site.layers["local_relief_200m"].data - site.layers["local_relief_50m"].data) >= -1e-3))
    chk("dist_to_* within site diagonal", ranges["dist_to_traverse_m"][1] <= site.half_m * 2 * math.sqrt(2) + 1
        and ranges["dist_to_unit_contact_m"][1] <= site.half_m * 2 * math.sqrt(2) + 1,
        f"{ranges['dist_to_traverse_m'][1]:.0f}, {ranges['dist_to_unit_contact_m'][1]:.0f}")
    ev_ok = all(ranges[k][0] >= -1 and ranges[k][1] <= 4 for k, l in site.layers.items() if l.kind == "evidence")
    chk("evidence tiers in -1..4", ev_ok)
    chk("geologic_unit codes in classes", set(np.unique(site.layers["geologic_unit"].data)) <= set(site.layers["geologic_unit"].classes))
    chk("quality_flag codes in classes", set(np.unique(site.layers["quality_flag"].data)) <= set(site.layers["quality_flag"].classes))
    chk("curvature finite & |c| < 1 /m", max(abs(ranges["curvature_profile"][0]), ranges["curvature_profile"][1]) < 1.0,
        str(ranges["curvature_profile"]))
    chk("mineral_diversity_100m in 1..7, unit_diversity in 1..6", ranges["mineral_diversity_100m"][1] <= 7
        and ranges["unit_diversity_100m"][1] <= 6)
    # DEM-derived layers agree with their definitions
    chk("slope_2m == Horn(elevation) exactly", float(np.nanmax(np.abs(terrain.slope_deg(z, site.res_m) - sl))) == 0.0)
    out["layer_ranges"] = {k: v for k, v in ranges.items() if site.layers[k].kind != "evidence"}
    out["elevation_range_m"] = [zmin, zmax]
    out["relief_m"] = zmax - zmin
    steep = float((sl > 45).mean())
    out["slope_gt45_frac"] = steep
    out["slope_gt60_cells"] = int((sl > 60).sum())

    # POIs inside the site and their z agree with the DEM
    zz = []
    for p in site.pois:
        zz.append(abs(route.terrain_z(site, p["x"], p["y"]) - p["z"]) if p.get("z") is not None else np.nan)
    chk("POI z within 1.5 m of DEM (bilinear)", np.nanmax(zz) < 1.5, f"max {np.nanmax(zz):.2f} m")
    chk("all POIs inside site", all(site.in_bounds(p["x"], p["y"]) for p in site.pois))

    # tool outputs
    rng = np.random.default_rng(SEED + 6)
    st = SetStore()
    r = search.find_locations(site, st, profile="landing_pad", limit=50)
    spots = st.get(r["set_id"]).members
    cl = np.array([m["clear_radius_m"] for m in spots])
    means = np.array([m["attributes"]["slope_2m_mean"] for m in spots])
    maxs = np.array([m["attributes"]["slope_2m_max"] for m in spots])
    xs = np.array([[m["x"], m["y"]] for m in spots])
    chk("every pad: clear_radius >= 50 m and <= 1000", cl.min() >= 50 - 1e-6 and cl.max() <= 1000, f"{cl.min()}..{cl.max()}")
    chk("every pad: max slope in footprint <= 10 deg", maxs.max() <= 10 + 1e-3, f"max {maxs.max():.3f}")
    chk("every pad: mean <= max slope", bool((means <= maxs + 1e-6).all()))
    chk("every pad inside site", bool((np.abs(xs) <= site.half_m).all()))
    ll = np.array([[m["lat"], m["lon"]] for m in spots])
    chk("pad lat/lon plausible", bool((np.abs(ll[:, 0] - site.lat0) < 0.2).all() and (np.abs(ll[:, 1] - site.lon0) < 0.2).all()))
    rr = search.find_regions(site, SetStore(), conditions=[{"layer": "slope_2m", "op": "<=", "value": 5}], limit=50)
    areas = np.array([m["area_m2"] for m in rr["results"]])
    chk("find_regions areas <= site area, sum of all <= site area", areas.max() <= site_area_m2(site), f"max {areas.max():.3g}")
    st2 = SetStore()
    rr2 = search.find_regions(site, st2, conditions=[{"layer": "slope_2m", "op": "<=", "value": 5}], limit=50)
    allareas = np.array([m["area_m2"] for m in st2.get(rr2["set_id"]).members])
    chk("sum of region areas <= site area", allareas.sum() <= site_area_m2(site), f"{allareas.sum():.3g} of {site_area_m2(site):.3g}")
    e = envelope.eva_envelope(site, SetStore(), "base")
    chk("eva envelope area <= site area", e["area_m2"] <= site_area_m2(site), f"{e['area_m2']:.4g} of {site_area_m2(site):.4g}")
    chk("eva envelope area == cells x res^2", abs(e["area_m2"] - e["reachable_cells"] * site.res_m ** 2) < 1.0)
    chk("envelope radius <= half-diagonal", e["max_one_way_radius_m"] <= site.half_m * math.sqrt(2) * 2)
    out["envelope_8h"] = dict(area_km2=e["area_km2"], site_km2=site_area_m2(site) / 1e6, touches_edge=e["touches_site_edge"],
                              one_way_limit_min=e.get("one_way_limit_min"))

    # route outputs
    zs = []
    asc = []
    for _ in range(5):
        A = rng.uniform(-2500, 2500, 2)
        B = A + rng.uniform(-1500, 1500, 2)
        rt = route.plan_route(site, SetStore(), {"x": A[0], "y": A[1]}, {"x": B[0], "y": B[1]}, profile="eva_walkable")
        if rt["feasible"]:
            zs += [p["z"] for p in rt["points"]]
            asc.append((rt["ascent_m"], rt["distance_m"], rt["duration_min"], rt["max_slope_deg"]))
    zs = np.array(zs)
    chk("route z within DEM range", zs.min() >= zmin - 1 and zs.max() <= zmax + 1, f"{zs.min():.1f}..{zs.max():.1f} vs {zmin:.1f}..{zmax:.1f}")
    chk("route ascent >= 0, max_slope <= profile limit",
        all(a_[0] >= 0 and a_[3] <= 25 + 1e-6 for a_ in asc), str(asc[:2]))
    n_el = 200
    pts = rng.uniform(-site.half_m + 5, site.half_m - 5, (n_el, 2))
    tz = np.array([route.terrain_z(site, x, y) for x, y in pts])
    chk("terrain_z within elevation range", np.nanmin(tz) >= zmin - 1e-3 and np.nanmax(tz) <= zmax + 1e-3)

    # walking speed curve
    tpar, _ = route.route_params(site, "eva_walkable")
    sl_grid = np.linspace(0, 45, 91)
    v = route.speed_mps(tpar, sl_grid)
    chk("walking speed 0 < v <= flat speed, <= 1.8 m/s (human)", v.min() > 0 and v.max() <= tpar["flat_speed_mps"] + 1e-9 and v.max() <= 1.8,
        f"v(0)={v[0]:.3f}, v(25)={route.speed_mps(tpar, 25.0):.3f}, v(45)={v[-1]:.3f}")
    chk("speed monotone decreasing with slope", bool((np.diff(v) < 0).all()))
    out["speed_curve"] = {str(s): float(route.speed_mps(tpar, s)) for s in (0, 5, 10, 15, 20, 25)}
    out["speed_curve_note"] = ("profiles.yaml text says speed = flat*exp(-k*|tan(slope)+c|); code (route.speed_mps, travel._Plan.costs) "
                               "uses flat*exp(-3.5*tan(slope)): k = 3.5 (Tobler 1993), c = 0, so no peak at -5 % gradient and symmetric.")
    # walking time of 1 km on flat ground
    flat_min = 1000.0 / tpar["flat_speed_mps"] / 60.0
    out["flat_1km_min"] = flat_min

    # sun
    sun_rows = []
    lat_c, lon_e = site.lat0, site.lon0
    worst_el, best_el = 90, -90
    max_dev = 0.0
    devs, noon_el = [], []
    for sol in range(0, 700, 25):
        t = sun.utc_for_ltst(sol, 12.0, lon_e)
        sp = sun.sun_position(t, lat_c, lon_e)
        lat_g = sp["site_lat_planetographic_deg"]
        decl = sp["subsolar_lat_planetographic_deg"]
        formula = 90.0 - abs(lat_g - decl)
        # also scan the whole sol for the true max elevation
        tt = [sun.sun_position(sun.utc_for_ltst(sol, h, lon_e), lat_c, lon_e)["sun_elevation_deg"] for h in np.arange(11.0, 13.01, 0.25)]
        best_el = max(best_el, max(tt))
        worst_el = min(worst_el, sp["sun_elevation_deg"])
        noon_el.append(sp["sun_elevation_deg"])
        devs.append((sp["sun_elevation_deg"] - formula, max(tt) - formula, sp["ls_deg"], decl))
    devs = np.array(devs)
    # midnight elevations
    mid = [sun.sun_position(sun.utc_for_ltst(sol, 0.0, lon_e), lat_c, lon_e)["sun_elevation_deg"] for sol in range(0, 700, 25)]
    allel = np.array(mid + noon_el)
    chk("sun elevation within -90..90 (sampled)", allel.min() >= -90 and allel.max() <= 90, f"{allel.min():.1f}..{allel.max():.1f}")
    chk("noon elevation = 90 - |lat_g - decl| within 0.5 deg", np.abs(devs[:, 0]).max() < 0.5,
        f"max |dev| noon-LTST {np.abs(devs[:, 0]).max():.3f}, scan-max {np.abs(devs[:, 1]).max():.3f}")
    chk("midnight sun below horizon at 18 N", max(mid) < 0, f"max {max(mid):.1f}")
    out["sun"] = dict(noon_dev_max_deg=float(np.abs(devs[:, 0]).max()), noon_dev_mean_deg=float(devs[:, 0].mean()),
                      scan_max_dev_max_deg=float(np.abs(devs[:, 1]).max()),
                      noon_elevation_range_deg=[float(min(noon_el)), float(max(noon_el))],
                      declination_range_deg=[float(devs[:, 3].min()), float(devs[:, 3].max())],
                      n_sols=int(len(devs)), midnight_max_deg=float(max(mid)))
    out["failed_checks"] = bad
    out["runtime_s"] = round(now() - t0, 1)
    return out


# ---------------------------------------------------------------------------------------------- main


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=20, help="Monte Carlo runs per noise model (section 4)")
    ap.add_argument("--sections", default="1,2,3,4,5,6")
    ap.add_argument("--quick", action="store_true", help="fewer LOS pairs")
    ap.add_argument("--out", default=str(ROOT / "validation/results.json"))
    args = ap.parse_args()
    secs = {int(s) for s in args.sections.split(",")}
    T0 = now()
    site = load_site(SITE_DIR)
    log(f"site loaded {now()-T0:.1f}s; workers={WORKERS}; seed={SEED}")
    res = {"seed": SEED}
    funcs = {1: lambda: section1(site, args.quick), 2: lambda: section2(site), 3: lambda: section3(site),
             4: lambda: section4(site, args.runs), 5: lambda: section5(site), 6: lambda: section6(site)}
    for k in sorted(secs):
        log(f"== section {k}")
        res[f"section{k}"] = funcs[k]()
        Path(args.out).write_text(json.dumps(res, indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o)))
    res["total_runtime_s"] = round(now() - T0, 1)
    Path(args.out).write_text(json.dumps(res, indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o)))
    log(f"total {res['total_runtime_s']}s -> {args.out}")


if __name__ == "__main__":
    main()

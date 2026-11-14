"""find_locations and find_regions. Spec: contracts/mcp-tools.md sections 4.4 and 4.5.
OWNER: builder "search". Signatures and return shapes are FROZEN; implement the bodies.

Common return fields (both functions):
    {
      "set_id": "set:N",               # full ranked result stored in the SetStore
      "count": int,                    # members in the stored set
      "results": [member, ...],        # top `limit` members
      "objective": str,
      "assumptions": [{"what": "slope_2m <= 10", "source": "assumed" | citation}],
      "diagnostics": [{"step": str, "pass_fraction": float}],   # see below
      "hint": str | None,              # next step; mandatory when count == 0
    }

Diagnostics order: one entry per top-level condition (pass fraction of that condition alone, over
ALL cells of the site), then "all conditions", then (find_locations) "+ footprint radius R m",
then "+ within <max> <metric> of <from>" if `within` is given. pass_fraction is over all cells.

find_locations member:
    {"id", "x", "y", "lat", "lon", "score", "clear_radius_m",
     "attributes": {"slope_2m_mean", "slope_2m_max", "elevation_mean",          # if those layers exist
                    "<metric>": value-from-within-field}}                       # if within given
    x, y = footprint centre (a cell centre). clear_radius_m = Euclidean distance (m) from the
    centre to the nearest failing cell (distance transform), capped at 1000.

find_regions member:
    {"id", "x", "y", "lat", "lon", "score", "area_m2", "bbox": [xmin, ymin, xmax, ymax],
     "attributes": {"centroid_x", "centroid_y", "slope_2m_mean", "slope_2m_max",
                    "<metric>": min value of within-field inside the region}}
    x, y = representative interior point: the region cell farthest from the region's edge.

Objectives:
    find_locations: "flattest" (min mean slope_2m in footprint), "largest_clear_area"
        (max clear_radius_m), "closest" (min within-field value; requires `within`),
        "highest" / "lowest" (mean elevation in footprint), "best_evidence:<layer>" (max tier in
        footprint, ties broken by closest if within given else flattest).
    find_regions: "largest" (max area), "closest" (requires within), "flattest" (min mean slope),
        "best_evidence:<layer>" (max tier inside region, then largest).
    Ties are broken by (straight distance to the first `within.from` location, then id) so results
    are deterministic.

Within: delegate to atlas_core.travel.within_mask(site, within, store) which returns
    (mask, field, label). Do not reimplement travel here.

Spreading (find_locations): greedy. Take the best valid centre, exclude valid centres within
spacing_m (default max(2 * footprint_radius_m, 100)), repeat. Store ALL spread spots, not just
`limit`.

Errors (AtlasError with hint): unknown profile, objective needs `within`, no footprint radius
given and none in the profile (default 0 m is allowed only if explicitly 0), limit outside 1..50.
"""
from __future__ import annotations

import copy
import math

import numpy as np
from scipy import ndimage
from scipy.signal import fftconvolve

from . import travel
from .conditions import combine, compile_all, merge_profile
from .errors import AtlasError
from .refs import resolve_ref
from .sets import SetStore
from .site import Site

MAX_CLEAR_RADIUS_M = 1000.0
MAX_STORED_SPOTS = 50000
SCORE_DECIMALS = 6          # ranking precision; hides FFT round-off so ties are real ties
_LOC_OBJECTIVES = ("flattest", "largest_clear_area", "closest", "highest", "lowest")
_REG_OBJECTIVES = ("largest", "closest", "flattest")


# ---------------------------------------------------------------- helpers


def _pct(f: float) -> str:
    """Percentage that never rounds a non-zero share down to 0 (or a partial share up to 100)."""
    if f <= 0:
        return "0%"
    if f >= 1:
        return "100%"
    if f < 0.0001:
        return "<0.01%"
    if f > 0.9999:
        return ">99.99%"
    return f"{f * 100:.2f}".rstrip("0").rstrip(".") + "%"

def _check_limit(limit) -> int:
    if isinstance(limit, bool) or not isinstance(limit, (int, np.integer)) or not 1 <= limit <= 50:
        raise AtlasError(f"limit must be an integer from 1 to 50, got {limit!r}.",
                         hint="Use limit between 1 and 50; the full ranking stays in the stored set.")
    return int(limit)


def _src_label(src) -> str:
    return src if src else "user-specified"


def _compile(site: Site, conditions, profile):
    conds, params = merge_profile(site, conditions, profile)
    if not conds:
        raise AtlasError("No conditions given.",
                         hint='Pass conditions, e.g. [{"layer": "slope_2m", "op": "<=", "value": 10}], '
                              "or a profile such as " + (", ".join(sorted(site.profiles)) or "none") + ".")
    compiled = compile_all(site, conds)
    shape = (site.size_px, site.size_px)
    return conds, params, compiled, combine(compiled, shape)


def _frac(mask: np.ndarray) -> float:
    return float(mask.sum()) / mask.size


def _diagnostics(compiled, allmask) -> list[dict]:
    d = [{"step": c.label, "pass_fraction": _frac(c.mask)} for c in compiled]
    d.append({"step": "all conditions", "pass_fraction": _frac(allmask)})
    return d


def _cond_assumptions(compiled) -> list[dict]:
    return [{"what": c.label, "source": _src_label(c.source)} for c in compiled]


def _apply_within(site, store, within, mask):
    """Returns (mask, field, label, first_from_xy)."""
    if within is None:
        return mask, None, None, None
    wmask, field, label = travel.within_mask(site, within, store)
    refs = resolve_ref(site, within["from"], store)
    first = (float(refs[0]["x"]), float(refs[0]["y"])) if refs else None
    return mask & wmask, np.asarray(field), label, first


def _parse_objective(site: Site, objective: str, allowed: tuple, within) -> str | None:
    """Validates; returns the evidence layer id for best_evidence objectives, else None."""
    if isinstance(objective, str) and objective.startswith("best_evidence:"):
        lid = objective.split(":", 1)[1]
        layer = site.layer(lid)
        if layer.kind != "evidence":
            raise AtlasError(f'Layer "{lid}" is {layer.kind}, so best_evidence:{lid} is not valid.',
                             hint="Use best_evidence:<evidence layer id>, e.g. best_evidence:carbonate.")
        return lid
    if objective not in allowed:
        raise AtlasError(f'Unknown objective "{objective}".',
                         hint="Valid objectives: " + ", ".join(allowed) + ", best_evidence:<layer>.")
    if objective == "closest" and within is None:
        raise AtlasError('Objective "closest" needs a `within` limit to measure distance from.',
                         hint='Add within={"from": "base", "max": 120, "metric": "walk_time_min"} or pick another objective.')
    return None


def _disk(radius_cells2: float):
    """Footprint offsets: cells whose centre is strictly within the radius; centre always included."""
    k = int(math.ceil(math.sqrt(radius_cells2))) if radius_cells2 > 0 else 0
    ar = np.arange(-k, k + 1)
    d2 = ar[:, None] ** 2 + ar[None, :] ** 2
    kern = (d2 < radius_cells2)
    kern[k, k] = True
    return kern, k


def _window_vals(arr, r, c, kern, k):
    """Values of arr over the footprint of cell (r, c); cell is known to be well inside the grid
    or the footprint is clipped (only happens for region stats, never for valid spot centres)."""
    r0, r1 = max(r - k, 0), min(r + k + 1, arr.shape[0])
    c0, c1 = max(c - k, 0), min(c + k + 1, arr.shape[1])
    kk = kern[r0 - (r - k): r1 - (r - k), c0 - (c - k): c1 - (c - k)]
    return arr[r0:r1, c0:c1][kk]


def _stats(vals):
    f = vals[np.isfinite(vals)]
    if f.size == 0:
        return None, None
    return round(float(f.mean()), 4), round(float(f.max()), 4)


def _latlon(site, x, y):
    lat, lon = site.xy_to_latlon(x, y)
    return round(lat, 5), round(lon, 5)


def _next_available(avail, rows, cols, ptr):
    n = rows.size
    B = 4096
    while ptr < n:
        seg = avail[rows[ptr:ptr + B], cols[ptr:ptr + B]]
        nz = int(seg.argmax())
        if seg[nz]:
            return ptr + nz
        ptr += B
        B = min(B * 2, 1 << 20)
    return -1


def _tier_in_footprint(layer, valid_cells_r2: float, res: float):
    """Max evidence tier inside the footprint of every cell (-1 if no covered cell)."""
    d = layer.data
    best = np.full(d.shape, -1, np.int8)
    for t in range(0, 5):
        has = d >= t
        if not has.any():
            break
        dist2 = np.rint(ndimage.distance_transform_edt(~has) ** 2)
        best[(dist2 < valid_cells_r2) | has] = t
    return best


# ---------------------------------------------------------------- find_locations

def find_locations(site: Site, store: SetStore, conditions: list[dict] | None = None,
                   profile: str | None = None, footprint_radius_m: float | None = None,
                   within: dict | None = None, objective: str = "flattest",
                   spacing_m: float | None = None, limit: int = 5) -> dict:
    limit = _check_limit(limit)
    conds, params, compiled, allmask = _compile(site, conditions, profile)

    # footprint radius
    fp_src = "user-specified"
    if footprint_radius_m is None:
        fr = params.get("footprint_radius_m")
        if fr is None:
            raise AtlasError("No footprint radius given and the profile has none.",
                             hint="Pass footprint_radius_m (use 0 explicitly for point locations), "
                                  "for example 50 for a landing pad.")
        if isinstance(fr, dict):
            footprint_radius_m, fp_src = fr.get("value"), fr.get("source") or "unspecified"
        else:
            footprint_radius_m, fp_src = fr, "unspecified"
    if isinstance(footprint_radius_m, bool) or not isinstance(footprint_radius_m, (int, float, np.integer, np.floating)) \
            or not np.isfinite(footprint_radius_m) or footprint_radius_m < 0:
        raise AtlasError(f"footprint_radius_m must be a number >= 0, got {footprint_radius_m!r}.",
                         hint="Use metres, e.g. 50 for a landing pad, or 0 for point locations.")
    R = float(footprint_radius_m)
    spacing_default = spacing_m is None
    if spacing_default:
        spacing_m = max(2 * R, 100.0)
    if isinstance(spacing_m, bool) or not isinstance(spacing_m, (int, float, np.integer, np.floating)) \
            or not np.isfinite(spacing_m) or spacing_m <= 0:
        raise AtlasError(f"spacing_m must be a number > 0, got {spacing_m!r}.",
                         hint="Omit spacing_m for the default max(2 * footprint radius, 100) m.")
    spacing_m = float(spacing_m)

    ev_layer = _parse_objective(site, objective, _LOC_OBJECTIVES, within)
    if objective == "flattest" or ev_layer is not None:
        site.layer("slope_2m")   # raises a helpful error if missing
    if objective in ("highest", "lowest"):
        site.layer("elevation")

    res = site.res_m
    n = site.size_px
    diagnostics = _diagnostics(compiled, allmask)

    # footprint check: distance transform; cells outside the grid count as failing
    padded = np.zeros((n + 2, n + 2), bool)
    padded[1:-1, 1:-1] = allmask
    dist2 = np.rint(ndimage.distance_transform_edt(padded) ** 2)[1:-1, 1:-1]   # integer cell^2
    r_cells2 = (R / res) ** 2
    valid = allmask & (dist2 >= r_cells2)
    diagnostics.append({"step": f"+ footprint radius {_fmt_num(R)} m", "pass_fraction": _frac(valid)})

    wfield = wlabel = first_xy = None
    if within is not None:
        valid, wfield, wlabel, first_xy = _apply_within(site, store, within, valid)
        diagnostics.append({"step": "+ " + wlabel, "pass_fraction": _frac(valid)})

    kern, k = _disk(r_cells2)
    kf = kern.astype(np.float64)
    slope = site.layers["slope_2m"].data if "slope_2m" in site.layers else None
    elev = site.layers["elevation"].data if "elevation" in site.layers else None

    def disk_mean_known(arr):
        """(mean over footprint, True where the whole footprint is finite)."""
        fin = np.isfinite(arr)
        filled = np.where(fin, arr, 0.0).astype(np.float64)
        if k == 0:
            return filled, fin
        s = fftconvolve(filled, kf, mode="same")
        bad = fftconvolve((~fin).astype(np.float64), kf, mode="same")
        return s / kf.sum(), bad < 0.5

    rr, cc = np.nonzero(valid)
    # primary score per candidate (lower = better after sign handling) and reported base
    sec = None
    if objective == "flattest" or (ev_layer is not None):
        mean, known = disk_mean_known(slope)
        flat = np.where(known, mean, np.nan)
        if objective == "flattest":
            keep = np.isfinite(flat[rr, cc])
            rr, cc = rr[keep], cc[keep]
            prim = flat[rr, cc]
        else:
            tier = _tier_in_footprint(site.layers[ev_layer], r_cells2, res)
            prim = -tier[rr, cc].astype(np.float64)
            if within is not None:
                sec = wfield[rr, cc].astype(np.float64)
            else:
                sec = np.where(np.isfinite(flat[rr, cc]), flat[rr, cc], np.inf)
    elif objective == "largest_clear_area":
        prim = -np.sqrt(dist2[rr, cc]) * res
    elif objective == "closest":
        prim = wfield[rr, cc].astype(np.float64)
    else:  # highest / lowest
        mean, known = disk_mean_known(elev)
        keep = known[rr, cc]
        rr, cc = rr[keep], cc[keep]
        prim = mean[rr, cc] * (-1.0 if objective == "highest" else 1.0)

    if rr.size:
        keys = [rr * n + cc]                                   # last-resort tie-break: row-major index
        if first_xy is not None:
            x, y = site.rc_to_xy(rr, cc)
            keys.append(np.round(np.hypot(x - first_xy[0], y - first_xy[1]), SCORE_DECIMALS))
        if sec is not None:
            keys.append(np.round(sec, SCORE_DECIMALS))
        keys.append(np.round(prim, SCORE_DECIMALS))
        order = np.lexsort(keys)                               # last key is primary
        rr, cc = rr[order], cc[order]

    # greedy spreading
    avail = np.zeros((n, n), bool)
    avail[rr, cc] = True
    s_cells2 = (spacing_m / res) ** 2
    sk = int(math.ceil(math.sqrt(s_cells2)))
    sar = np.arange(-sk, sk + 1)
    sdisk = (sar[:, None] ** 2 + sar[None, :] ** 2) < s_cells2
    picked = []
    ptr = 0
    truncated = False
    while True:
        ptr = _next_available(avail, rr, cc, ptr)
        if ptr < 0:
            break
        if len(picked) >= MAX_STORED_SPOTS:
            truncated = True
            break
        r, c = int(rr[ptr]), int(cc[ptr])
        picked.append((r, c))
        r0, r1 = max(r - sk, 0), min(r + sk + 1, n)
        c0, c1 = max(c - sk, 0), min(c + sk + 1, n)
        sub = sdisk[r0 - (r - sk): r1 - (r - sk), c0 - (c - sk): c1 - (c - sk)]
        avail[r0:r1, c0:c1] &= ~sub
        ptr += 1

    # members
    members = []
    metric = within["metric"] if within is not None else None
    for r, c in picked:
        x, y = (float(v) for v in site.rc_to_xy(r, c))
        lat, lon = _latlon(site, x, y)
        clear = min(math.sqrt(float(dist2[r, c])) * res, MAX_CLEAR_RADIUS_M)
        attrs = {}
        if slope is not None:
            m_, x_ = _stats(_window_vals(slope, r, c, kern, k))
            attrs["slope_2m_mean"], attrs["slope_2m_max"] = m_, x_
        if elev is not None:
            attrs["elevation_mean"], _ = _stats(_window_vals(elev, r, c, kern, k))
        if wfield is not None:
            v = float(wfield[r, c])
            attrs[metric] = round(v, 4)
        if objective == "flattest":
            score = attrs["slope_2m_mean"]
        elif objective == "largest_clear_area":
            score = round(clear, 4)
        elif objective == "closest":
            score = attrs[metric]
        elif objective in ("highest", "lowest"):
            score = attrs["elevation_mean"]
        else:
            score = int(np.max(_window_vals(site.layers[ev_layer].data, r, c, kern, k)))
        members.append({"x": x, "y": y, "lat": lat, "lon": lon, "score": score,
                        "clear_radius_m": round(clear, 4), "attributes": attrs})

    query = {"tool": "find_locations", "conditions": copy.deepcopy(conds), "profile": profile,
             "footprint_radius_m": R, "within": copy.deepcopy(within), "objective": objective,
             "spacing_m": spacing_m, "limit": limit}
    s = store.create("spots", "find_locations", query, members)

    assumptions = _cond_assumptions(compiled)
    assumptions.append({"what": f"footprint_radius_m = {_fmt_num(R)}", "source": _src_label(fp_src)})
    assumptions.append({"what": f"spacing_m = {_fmt_num(spacing_m)}",
                        "source": "default max(2 * footprint radius, 100)" if spacing_default else "user-specified"})
    if truncated:
        assumptions.append({"what": f"stored set truncated at {MAX_STORED_SPOTS} spots", "source": "implementation limit"})

    hint = _loc_hint(s.set_id, s.count, compiled, diagnostics, R, within, objective, valid, rr.size)
    return {"set_id": s.set_id, "count": s.count,
            "results": copy.deepcopy(s.members[:limit]), "objective": objective,
            "assumptions": assumptions, "diagnostics": diagnostics, "hint": hint}


def _fmt_num(v: float) -> str:
    return str(int(v)) if float(v) == int(v) else str(v)


def _loc_hint(set_id, count, compiled, diag, R, within, objective, valid, n_candidates):
    if count > 0:
        return (f"Call describe_location on a result for details, or find_regions/nearest with {set_id}. "
                f"Spots are at least the spacing apart; the full ranking is in {set_id}.")
    ncond = len(compiled)
    cond_steps = diag[:ncond]
    worst = min(cond_steps, key=lambda d: d["pass_fraction"])
    allf = diag[ncond]["pass_fraction"]
    if allf == 0:
        if any(d["pass_fraction"] == 0 for d in cond_steps):
            z = next(d for d in cond_steps if d["pass_fraction"] == 0)
            return f'No cell passes "{z["step"]}" alone. Relax that condition.'
        return (f'Each condition passes somewhere but no cell passes all together. '
                f'Most restrictive: "{worst["step"]}" ({_pct(worst["pass_fraction"])}). Relax it or drop a condition.')
    if diag[ncond + 1]["pass_fraction"] == 0:
        return (f"{_pct(allf)} of cells pass all conditions, but none has a clear area of radius {_fmt_num(R)} m. "
                f"Reduce footprint_radius_m or relax \"{worst['step']}\".")
    if within is not None and diag[-1]["pass_fraction"] == 0:
        return "Valid spots exist but none is inside the within limit. Relax the limit (max) or the footprint radius."
    if valid.any() and n_candidates == 0:
        return ("Valid spots exist, but every footprint contains cells with unknown data for the objective "
                f'"{objective}". Try a different objective.')
    return "Relax a condition or the footprint radius."


# ---------------------------------------------------------------- find_regions

def find_regions(site: Site, store: SetStore, conditions: list[dict] | None = None,
                 profile: str | None = None, min_area_m2: float = 400.0,
                 within: dict | None = None, objective: str = "largest", limit: int = 5) -> dict:
    limit = _check_limit(limit)
    if isinstance(min_area_m2, bool) or not isinstance(min_area_m2, (int, float, np.integer, np.floating)) \
            or not np.isfinite(min_area_m2) or min_area_m2 < 0:
        raise AtlasError(f"min_area_m2 must be a number >= 0, got {min_area_m2!r}.",
                         hint="Use square metres, e.g. 400 (the default).")
    conds, params, compiled, allmask = _compile(site, conditions, profile)
    ev_layer = _parse_objective(site, objective, _REG_OBJECTIVES, within)
    if objective == "flattest":
        site.layer("slope_2m")
    diagnostics = _diagnostics(compiled, allmask)
    mask, wfield, wlabel, first_xy = _apply_within(site, store, within, allmask)
    if within is not None:
        diagnostics.append({"step": "+ " + wlabel, "pass_fraction": _frac(mask)})

    res = site.res_m
    n = site.size_px
    cell_area = res * res
    labels, nlab = ndimage.label(mask, structure=np.ones((3, 3), bool))
    counts = np.bincount(labels.ravel(), minlength=nlab + 1)
    areas = counts * cell_area
    keep_ids = [i for i in range(1, nlab + 1) if areas[i] >= min_area_m2]
    slices = ndimage.find_objects(labels)
    slope = site.layers["slope_2m"].data if "slope_2m" in site.layers else None
    metric = within["metric"] if within is not None else None

    recs = []
    for i in keep_ids:
        sl = slices[i - 1]
        reg = labels[sl] == i
        padded = np.pad(reg, 1)
        edt = ndimage.distance_transform_edt(padded)[1:-1, 1:-1]
        edt = np.where(reg, edt, -1.0)
        ar, ac = np.unravel_index(int(np.argmax(edt)), edt.shape)   # first max in row-major order
        r, c = sl[0].start + ar, sl[1].start + ac
        rows, cols = np.nonzero(reg)
        rows = rows + sl[0].start
        cols = cols + sl[1].start
        cx, cy = site.rc_to_xy(rows, cols)
        x, y = (float(v) for v in site.rc_to_xy(r, c))
        xmin = float(site.rc_to_xy(0, sl[1].start)[0]) - res / 2
        xmax = float(site.rc_to_xy(0, sl[1].stop - 1)[0]) + res / 2
        ymax = float(site.rc_to_xy(sl[0].start, 0)[1]) + res / 2
        ymin = float(site.rc_to_xy(sl[0].stop - 1, 0)[1]) - res / 2
        attrs = {"centroid_x": round(float(cx.mean()), 4), "centroid_y": round(float(cy.mean()), 4)}
        smean = smax = None
        if slope is not None:
            smean, smax = _stats(slope[rows, cols])
            attrs["slope_2m_mean"], attrs["slope_2m_max"] = smean, smax
        wmin = None
        if wfield is not None:
            wmin = float(np.min(wfield[rows, cols]))
            attrs[metric] = round(wmin, 4)
        if ev_layer is not None:
            tmax = int(site.layers[ev_layer].data[rows, cols].max())
        recs.append({"i": i, "x": x, "y": y, "area": float(areas[i]), "bbox": [xmin, ymin, xmax, ymax],
                     "attrs": attrs, "smean": smean, "wmin": wmin,
                     "tmax": tmax if ev_layer is not None else None})

    def key(rec):
        dist = round(math.hypot(rec["x"] - first_xy[0], rec["y"] - first_xy[1]), SCORE_DECIMALS) if first_xy else 0.0
        if objective == "largest":
            prim = (-rec["area"],)
        elif objective == "closest":
            prim = (rec["wmin"],)
        elif objective == "flattest":
            prim = (rec["smean"] if rec["smean"] is not None else math.inf,)
        else:
            prim = (-rec["tmax"], -rec["area"])
        return prim + (dist, rec["i"])

    recs.sort(key=key)
    members = []
    for rec in recs:
        lat, lon = _latlon(site, rec["x"], rec["y"])
        if objective == "largest":
            score = rec["area"]
        elif objective == "closest":
            score = round(rec["wmin"], 4)
        elif objective == "flattest":
            score = rec["smean"]
        else:
            score = rec["tmax"]
        members.append({"x": rec["x"], "y": rec["y"], "lat": lat, "lon": lon, "score": score,
                        "area_m2": rec["area"], "bbox": rec["bbox"], "attributes": rec["attrs"]})

    query = {"tool": "find_regions", "conditions": copy.deepcopy(conds), "profile": profile,
             "min_area_m2": float(min_area_m2), "within": copy.deepcopy(within),
             "objective": objective, "limit": limit}
    s = store.create("regions", "find_regions", query, members)
    assumptions = _cond_assumptions(compiled)
    assumptions.append({"what": f"min_area_m2 = {_fmt_num(float(min_area_m2))}", "source": "user-specified or default"})

    if s.count:
        hint = (f"Call describe_location on a region point, or nearest(from={s.set_id}, to=<pad set>) "
                "for the closest pads.")
    else:
        ncond = len(compiled)
        if not mask.any():
            z = [d for d in diagnostics[:ncond] if d["pass_fraction"] == 0]
            if z:
                hint = f'No cell passes "{z[0]["step"]}" alone. Relax that condition.'
            elif diagnostics[ncond]["pass_fraction"] == 0:
                worst = min(diagnostics[:ncond], key=lambda d: d["pass_fraction"])
                hint = (f'No cell passes all conditions together. Most restrictive: "{worst["step"]}" '
                        f'({_pct(worst["pass_fraction"])}). Relax it or drop a condition.')
            else:
                hint = "Cells match but none is inside the within limit. Relax the limit."
        else:
            biggest = float(areas[1:].max())
            hint = (f"{nlab} connected area(s) match, but the largest is {biggest:.0f} m2, below "
                    f"min_area_m2 = {_fmt_num(float(min_area_m2))}. Lower min_area_m2 or relax the conditions.")
    return {"set_id": s.set_id, "count": s.count, "results": copy.deepcopy(s.members[:limit]),
            "objective": objective, "assumptions": assumptions, "diagnostics": diagnostics, "hint": hint}

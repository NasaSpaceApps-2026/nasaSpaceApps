"""Slow, simple, independent reference implementation for the search tools.

Deliberately plain Python loops over cells: no distance transforms, no convolution, no
atlas_core.conditions code. It re-derives condition logic straight from the layer arrays.
Cells are addressed by (row, col); everything is in the site frame (metres).
"""
from __future__ import annotations

import math

TIERS = {"not_detected": 0, "inferred_from_unit": 1, "weak_orbital": 2,
         "strong_orbital": 3, "confirmed_in_situ": 4}


def _is_nan(v) -> bool:
    return v != v or v in (float("inf"), float("-inf"))


def _class_code(layer, v):
    for code, name in layer.classes.items():
        if name == v or code == v:
            return code
    raise KeyError(v)


def cell_passes(site, cond: dict, r: int, c: int) -> bool:
    """Does one cell satisfy one condition? Unknown cells never pass."""
    if "any_of" in cond:
        return any(cell_passes(site, s, r, c) for s in cond["any_of"])
    if "evidence" in cond:
        v = int(site.layers[cond["evidence"]].data[r][c])
        t = cond["min_tier"]
        t = TIERS[t] if isinstance(t, str) else t
        return v >= 0 and v >= t
    layer = site.layers[cond["layer"]]
    v = layer.data[r][c]
    op, val = cond["op"], cond["value"]
    if layer.kind == "continuous":
        v = float(v)
        if _is_nan(v):
            return False
        if op == "<":
            return v < val
        if op == "<=":
            return v <= val
        if op == ">":
            return v > val
        if op == ">=":
            return v >= val
        if op == "between":
            return val[0] <= v <= val[1]
        raise ValueError(op)
    v = int(v)
    if v < 0:
        return False
    if op == "==":
        return v == _class_code(layer, val)
    if op == "!=":
        return v != _class_code(layer, val)
    codes = [_class_code(layer, x) for x in val]
    if op == "in":
        return v in codes
    if op == "not_in":
        return v not in codes
    raise ValueError(op)


def passing_grid(site, conds: list[dict]) -> list[list[bool]]:
    n = site.size_px
    return [[all(cell_passes(site, cd, r, c) for cd in conds) for c in range(n)] for r in range(n)]


def condition_fraction(site, conds: list[dict]) -> float:
    n = site.size_px
    ok = sum(all(cell_passes(site, cd, r, c) for cd in conds) for r in range(n) for c in range(n))
    return ok / (n * n)


def footprint_cells(site, r: int, c: int, radius_m: float):
    """Cells (possibly outside the grid) whose centre is strictly within radius_m of the centre of
    (r, c); the centre cell is always included."""
    res = site.res_m
    k = int(math.ceil(radius_m / res)) + 1
    out = [(r, c)]
    for dr in range(-k, k + 1):
        for dc in range(-k, k + 1):
            if (dr or dc) and math.hypot(dr * res, dc * res) < radius_m - 1e-9:
                out.append((r + dr, c + dc))
    return out


def footprint_valid(site, passing, r: int, c: int, radius_m: float) -> bool:
    n = site.size_px
    for rr, cc in footprint_cells(site, r, c, radius_m):
        if not (0 <= rr < n and 0 <= cc < n) or not passing[rr][cc]:
            return False
    return True


def valid_centres(site, conds: list[dict], radius_m: float) -> list[tuple[int, int]]:
    passing = passing_grid(site, conds)
    n = site.size_px
    return [(r, c) for r in range(n) for c in range(n)
            if passing[r][c] and footprint_valid(site, passing, r, c, radius_m)]


def flattest_score(site, r: int, c: int, radius_m: float):
    """Mean slope_2m over the footprint; None if any footprint cell is unknown."""
    layer = site.layers["slope_2m"].data
    vals = []
    for rr, cc in footprint_cells(site, r, c, radius_m):
        v = float(layer[rr][cc])
        if _is_nan(v):
            return None
        vals.append(v)
    return sum(vals) / len(vals)


def clear_radius(site, passing, r: int, c: int) -> float:
    """Brute-force distance (m) to the nearest failing cell, outside-the-grid cells counting as failing
    (nearest outside cell centre is one step beyond the edge)."""
    n = site.size_px
    best = float("inf")
    for rr in range(-1, n + 1):
        for cc in range(-1, n + 1):
            inside = 0 <= rr < n and 0 <= cc < n
            if inside and passing[rr][cc]:
                continue
            best = min(best, math.hypot((rr - r) * site.res_m, (cc - c) * site.res_m))
    return best


def find_flattest(site, conds, radius_m, spacing_m, limit=None):
    """Greedy flattest spots: sort by (score rounded to 6 dp, row-major index), then spread.
    Returns [(r, c, score)]."""
    cands = []
    for r, c in valid_centres(site, conds, radius_m):
        s = flattest_score(site, r, c, radius_m)
        if s is not None:
            cands.append((round(s, 6), r * site.size_px + c, r, c, s))
    cands.sort()
    picked = []
    for _, _, r, c, s in cands:
        if all(math.hypot((r - pr) * site.res_m, (c - pc) * site.res_m) >= spacing_m for pr, pc, _ in picked):
            picked.append((r, c, s))
            if limit and len(picked) >= limit:
                break
    return picked

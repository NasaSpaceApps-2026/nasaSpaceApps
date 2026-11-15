"""Independent slow reference for travel cost: plain-Python Dijkstra (heapq) on the planning grid.

Deliberately shares no code with atlas_core.travel. Edge cost between 8-connected cells is
step_length * (cost_a + cost_b) / 2 with step_length 1 (orthogonal) or sqrt(2) (diagonal), in cell
units; callers multiply by the cell size. Impassable cells are skipped.
"""
from __future__ import annotations

import heapq
import math

import numpy as np


def planning_costs(site, profile: str, metric: str):
    """Loop-based block reduction (independent of travel.py). Returns (costs list-of-lists with
    None for impassable, cell_m, scale to metres/minutes)."""
    t = site.profiles[profile]["travel"]
    n = site.size_px
    k = max(1, round(4.0 / site.res_m))
    assert n % k == 0, "oracle assumes size_px divisible by k"
    slope = site.layers["slope_2m"].data
    m = n // k
    out = [[None] * m for _ in range(m)]
    for i in range(m):
        for j in range(m):
            blk = slope[i * k:(i + 1) * k, j * k:(j + 1) * k]
            if (not np.all(np.isfinite(blk))) or float(blk.max()) > t["max_slope_deg"]:
                continue
            if metric == "walk_m":
                out[i][j] = 1.0
            else:
                v = t["flat_speed_mps"] * math.exp(-3.5 * math.tan(math.radians(float(blk.max()))))
                out[i][j] = 1.0 / v
    cell_m = k * site.res_m
    return out, cell_m, (cell_m if metric == "walk_m" else cell_m / 60.0)


def dijkstra(costs, sources):
    """costs: list of lists (None = impassable); sources: [(i, j)]. Returns array of cumulative
    cost in cost-units x cell-steps (inf where unreachable)."""
    m = len(costs)
    dist = [[math.inf] * m for _ in range(m)]
    heap = []
    for (i, j) in sources:
        if costs[i][j] is not None:
            dist[i][j] = 0.0
            heap.append((0.0, i, j))
    heapq.heapify(heap)
    nbrs = [(di, dj, math.sqrt(2) if di and dj else 1.0)
            for di in (-1, 0, 1) for dj in (-1, 0, 1) if di or dj]
    while heap:
        d, i, j = heapq.heappop(heap)
        if d > dist[i][j]:
            continue
        ci = costs[i][j]
        for di, dj, L in nbrs:
            a, b = i + di, j + dj
            if 0 <= a < m and 0 <= b < m and costs[a][b] is not None:
                nd = d + L * (ci + costs[a][b]) / 2.0
                if nd < dist[a][b]:
                    dist[a][b] = nd
                    heapq.heappush(heap, (nd, a, b))
    return np.array(dist)


def oracle_field(site, source_xy, metric: str, profile: str = "eva_walkable"):
    """Planning-grid field in metres/minutes from source points (assumed on passable cells)."""
    costs, cell_m, scale = planning_costs(site, profile, metric)
    k = round(4.0 / site.res_m)
    cells = []
    for x, y in source_xy:
        r, c = site.rc_index(x, y)
        cells.append((int(r) // k, int(c) // k))
    return dijkstra(costs, cells) * scale, cell_m

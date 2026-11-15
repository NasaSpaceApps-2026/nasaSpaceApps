"""describe_location: every layer's value at a point or averaged over a footprint (contracts/mcp-tools.md 4.3).

Layer: atlas_core, called by devapi/ (REST) and from there by mcp/ tools; reads the Site loaded from data/build/site/<name>/.
Input: location refs (atlas_core.refs), optional radius_m (metres, site frame x east / y north). Layer values are
bilinear point samples (terrain.bilinear) or footprint statistics; evidence layers add provenance and registration
caveats from atlas_core.evidence. Output: dict with one entry per location and a nearest ground-truth (rover sample) note.
Failure: AtlasError for bad refs or more than MAX_LOCATIONS locations, with a hint.
"""
from __future__ import annotations

import math

import numpy as np

from . import evidence as ev
from .errors import AtlasError
from .refs import resolve_ref
from .sets import SetStore
from .site import TIER_NAMES, Layer, Site
from .terrain import bilinear

MAX_LOCATIONS = 10


def _point_value(site: Site, layer: Layer, x: float, y: float) -> dict:
    r, c = site.rc_index(x, y)
    r, c = int(r), int(c)
    n = site.size_px
    if not (0 <= r < n and 0 <= c < n) or bool(layer.unknown[r, c]):
        return {"unknown": True}
    raw = layer.data[r, c]
    if layer.kind == "continuous":
        row, col = site.xy_to_rc(x, y)
        v = bilinear(layer.data, float(row), float(col))
        if not math.isfinite(v):
            v = float(raw)
        return {"value": v, "units": layer.meta.get("units")}
    if layer.kind == "categorical":
        return {"class": (layer.classes or {}).get(int(raw), str(int(raw)))}
    return {"tier": int(raw), "tier_name": TIER_NAMES[int(raw)]}


def _footprint(site: Site, x: float, y: float, radius_m: float):
    """(rows, cols) index arrays of cells whose centre is within radius_m (always >= 1 cell)."""
    n = site.size_px
    k = int(math.ceil(radius_m / site.res_m)) + 1
    r0, c0 = (int(v) for v in site.rc_index(x, y))
    rr = np.arange(max(0, r0 - k), min(n, r0 + k + 1))
    cc = np.arange(max(0, c0 - k), min(n, c0 + k + 1))
    if rr.size == 0 or cc.size == 0:
        return np.array([], int), np.array([], int)
    R, C = np.meshgrid(rr, cc, indexing="ij")
    X, Y = site.rc_to_xy(R, C)
    m = np.hypot(X - x, Y - y) <= radius_m
    if 0 <= r0 < n and 0 <= c0 < n:
        m[r0 - rr[0], c0 - cc[0]] = True
    return R[m], C[m]


def _area_value(site: Site, layer: Layer, rows, cols) -> dict:
    cells = layer.data[rows, cols]
    total = cells.size
    unk = layer.unknown[rows, cols]
    unknown_fraction = float(unk.mean()) if total else 1.0
    known = cells[~unk]
    if known.size == 0:
        return {"unknown": True, "unknown_fraction": 1.0}
    if layer.kind == "continuous":
        return {"mean": float(known.mean()), "min": float(known.min()), "max": float(known.max()),
                "unknown_fraction": unknown_fraction, "units": layer.meta.get("units")}
    vals, counts = np.unique(known, return_counts=True)
    if layer.kind == "categorical":
        shares = {(layer.classes or {}).get(int(v), str(int(v))): float(n) / total
                  for v, n in zip(vals, counts)}
        return {"shares": shares, "unknown_fraction": unknown_fraction}
    shares = {TIER_NAMES[int(v)]: float(n) / total for v, n in zip(vals, counts)}
    best = int(known.max())
    return {"best_tier": best, "best_tier_name": TIER_NAMES[best], "shares": shares,
            "unknown_fraction": unknown_fraction}


def _sample_info(site: Site, poi_id: str) -> dict:
    """Sample POI -> name, source (NASA sample list), mineral statement and its citation if one was read."""
    p = next((q for q in site.pois if q.get("id") == poi_id), {})
    cites = set()
    for layer in site.layers.values():
        for smp in ev._samples(layer):
            if smp["poi"] == poi_id:
                cites.add(smp["citation"])
    minerals = p.get("in_situ_minerals") or []
    return {"poi": f"poi:{poi_id}", "name": p.get("name") or poi_id, "region": p.get("region"),
            "rock_type": p.get("rock_type"), "source": "NASA Perseverance sample list", "source_url": p.get("source_url"),
            "position_uncertainty_m": p.get("position_uncertainty_m"),
            "in_situ_minerals": minerals,
            "mineral_statement": ("; ".join(sorted(cites)) if cites else
                                  "no sample-specific mineral measurement was read for this sample (rock-type label only)")}


def _nearest_ground_truth(site: Site, x: float, y: float) -> dict | None:
    best = None
    for layer in site.layers.values():
        if layer.kind != "evidence":
            continue
        rr, cc = np.nonzero(layer.data == 4)
        if rr.size == 0:
            continue
        X, Y = site.rc_to_xy(rr, cc)
        d = np.hypot(X - x, Y - y)
        i = int(np.argmin(d))
        if best is None or d[i] < best["distance_m"]:
            smp = [q for q in ev._samples(layer)]
            near = min(smp, key=lambda q: math.hypot(q["x"] - X[i], q["y"] - Y[i])) if smp else None
            best = {"what": f"{layer.id} confirmed_in_situ cell", "x": float(X[i]),
                    "y": float(Y[i]), "distance_m": float(d[i]), "layer": layer.id,
                    "kind": "rover sample (tier 4), not an orbital detection"}
            if near:
                best["sample"] = _sample_info(site, near["poi"])
                best["citation"] = near["citation"]
    for p in site.pois:
        if p.get("type") == "sample_site":
            d = math.hypot(p["x"] - x, p["y"] - y)
            if best is None or d < best["distance_m"]:
                info = _sample_info(site, p["id"])
                best = {"what": f"sample site: {p.get('name') or p.get('id')}", "x": float(p["x"]),
                        "y": float(p["y"]), "distance_m": float(d), "kind": "rover sample site (NASA sample list)",
                        "sample": info, "citation": info["mineral_statement"]}
    return best


def describe_location(site: Site, store: SetStore, at, radius_m: float = 0.0,
                      layers: list[str] | None = None) -> dict:
    """Everything known at one or more places (at most 10; more -> AtlasError with hint to use
    get_set paging).

    Returns {"radius_m": float, "locations": [loc, ...]} where loc is:
    {
      "id", "name"?, "x", "y", "lat", "lon",
      "values": {layer_id: value_dict},
      "quality": str,                      # class name of layer "quality_flag" at the point if
                                           # present, else "not recorded"
      "nearest_ground_truth": {"what", "x", "y", "distance_m"} | None,
    }

    value_dict when radius_m == 0 (point):
        continuous   {"value": float (bilinear; nearest cell if a bilinear neighbour is NaN but
                       the containing cell is valid), "units"}
        categorical  {"class": name}                 (containing cell)
        evidence     {"tier": int, "tier_name": str} (containing cell)
        any unknown  {"unknown": True}
    value_dict when radius_m > 0 (all cells whose centre is within radius_m):
        continuous   {"mean", "min", "max", "unknown_fraction", "units"}
        categorical  {"shares": {name: fraction}, "unknown_fraction"}
        evidence     {"best_tier", "best_tier_name", "shares": {tier_name: fraction}, "unknown_fraction"}
        (shares are fractions of ALL cells in the footprint, so shares + unknown_fraction = 1;
         if every cell is unknown the value is {"unknown": True, "unknown_fraction": 1.0})

    nearest_ground_truth: the nearest cell with tier 4 (confirmed_in_situ) in ANY evidence layer,
    or the nearest POI of type "sample_site", whichever is closer, searched over the whole site.
    "what" names the layer or POI.
    """
    if radius_m is None:
        radius_m = 0.0
    radius_m = float(radius_m)
    if radius_m < 0 or not math.isfinite(radius_m):
        raise AtlasError("radius_m must be >= 0.", hint="Use 0 for point values or e.g. 50 for a footprint.")
    members = resolve_ref(site, at, store)
    if not members:
        raise AtlasError("No locations to describe.", hint="Pass a point, POI id or non-empty set.")
    total = len(members)
    truncated = None
    if total > MAX_LOCATIONS:
        members = members[:MAX_LOCATIONS]
        truncated = {"shown": MAX_LOCATIONS, "total": total,
                     "note": (f"Described the first {MAX_LOCATIONS} of {total} locations. Use get_set(set_id, offset, limit) "
                              'to see the rest and pass member ids such as "3.12" to describe others.')}
    layer_ids = list(layers) if layers else list(site.layers)
    selected = [site.layer(l) for l in layer_ids]   # raises AtlasError for unknown ids
    qlayer = site.layers.get("quality_flag")

    out = []
    for m in members:
        x, y = float(m["x"]), float(m["y"])
        values = {}
        if radius_m > 0:
            rows, cols = _footprint(site, x, y, radius_m)
            for l in selected:
                values[l.id] = _area_value(site, l, rows, cols)
        else:
            for l in selected:
                values[l.id] = _point_value(site, l, x, y)
        quality = "not recorded"
        if qlayer is not None:
            q = _point_value(site, qlayer, x, y)
            quality = q.get("class", "no data") if qlayer.kind == "categorical" else "no data"
        lat, lon = site.xy_to_latlon(x, y)
        fr, fc = ev.disc_cells(site, x, y, radius_m)
        evd = {}
        for l in selected:
            if l.kind != "evidence":
                continue
            if radius_m > 0:
                sm = ev.summarize(site, l, fr, fc)
                if sm:
                    evd[l.id] = sm
            else:
                sm = ev.summarize(site, l, fr[:1], fc[:1]) if fr.size else None
                if sm:
                    evd[l.id] = sm
        flags = [f for f in ev.boundary_flags(site, [l.id for l in selected], fr, fc)
                 if not (f in evd and evd[f].get("best_tier") is None)]
        loc = {"id": m.get("id", "point"), "x": x, "y": y, "lat": lat, "lon": lon,
               "values": values, "quality": quality,
               "nearest_ground_truth": _nearest_ground_truth(site, x, y)}
        if evd:
            loc["evidence"] = evd
        if flags:
            loc["near_boundary_of"] = flags
        if m.get("name"):
            loc["name"] = m["name"]
        out.append(loc)
    res = {"radius_m": radius_m, "locations": out}
    flagged = {f for l in out for f in (l.get("near_boundary_of") or [])}
    if flagged:
        res["registration_notes"] = ev.caveat_texts(site, sorted(flagged))
    if truncated:
        res["truncated"] = truncated
    return res

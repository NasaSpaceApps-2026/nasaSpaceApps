"""Evidence provenance, per-tier areas and registration caveats (honesty helpers).

Tier 4 (confirmed_in_situ) is a ROVER SAMPLE claim, never an orbital one. Orbital tiers come from the layer's
orbital instrument (CRISM/MRO for the Jezero minerals). The catalogue entry carries `tier_sources`
({tier name: {kind, platform, instrument, citation, samples?}}); sites without it fall back to the layer's source for
the orbital tiers and say that no rover source is recorded for tier 4.

Registration caveats: the geologic map is offset from our terrain (measured, see the layer's `registration`), CRISM tiers
are 36 m pixels with an unmeasured residual. A result near a unit contact or tier change says so.
"""
from __future__ import annotations

import math

import numpy as np
from scipy import ndimage

from .site import TIER_NAMES, Layer, Site

UNIT_CONTACT_M = 100.0
TIER_CHANGE_M = 36.0
MAX_CAVEAT_CELLS = 600       # window size cap (cells per side) so a huge region cannot make this slow


def orbital_label(layer: Layer) -> str:
    """Short "mission/instrument" label of a layer's orbital source, e.g. "MRO/CRISM" (from meta["source"])."""
    s = layer.meta.get("source") or {}
    inst = str(s.get("instrument") or "?").split(" (")[0].split(",")[0]
    return f"{s.get('mission') or '?'}/{inst}"


def tier_sources(layer: Layer) -> dict:
    """Per-tier provenance {tier name: {kind, platform, instrument, citation, samples?}}: the catalogue's `tier_sources`, else the orbital source for the two orbital tiers only (no rover source is claimed for tier 4)."""
    ts = layer.meta.get("tier_sources")
    if ts:
        return ts
    s = layer.meta.get("source") or {}
    orb = {"kind": "orbital", "platform": s.get("mission"), "instrument": s.get("instrument"), "citation": s.get("citation")}
    return {"weak_orbital": orb, "strong_orbital": orb}


def _samples(layer: Layer) -> list[dict]:
    return (tier_sources(layer).get("confirmed_in_situ") or {}).get("samples") or []


def fmt_area(m2: float) -> str:
    """Area text: km2 with two decimals from 100 000 m2 upward, else whole m2."""
    if m2 >= 1e5:
        return f"{m2 / 1e6:.2f} km2"
    return f"{m2:.0f} m2"


def summarize(site: Site, layer: Layer, rows, cols, with_samples: bool = True) -> dict | None:
    """Per-tier area over the given cells of an evidence layer, plus the rover samples behind tier 4.
    `area_by_tier_m2` maps tier name -> m2 (only tiers present); unknown cells are `no_coverage`."""
    rows, cols = np.asarray(rows), np.asarray(cols)
    if rows.size == 0:
        return None
    t = layer.data[rows, cols]
    cell = site.res_m ** 2
    by = {}
    for v in np.unique(t):
        name = "no_coverage" if v < 0 else TIER_NAMES[int(v)]
        by[name] = round(float((t == v).sum()) * cell, 1)
    known = t[t >= 0]
    out = {"layer": layer.id, "orbital_source": orbital_label(layer), "area_by_tier_m2": by,
           "total_area_m2": round(float(t.size) * cell, 1)}
    if known.size:
        b = int(known.max())
        out["best_tier"], out["best_tier_name"] = b, TIER_NAMES[b]
    else:
        out["best_tier"], out["best_tier_name"] = None, "no_coverage"
    if with_samples and (t == 4).any():
        sel = (t == 4)
        X, Y = site.rc_to_xy(rows[sel], cols[sel])
        hit = []
        for s in _samples(layer):
            d = np.hypot(X - s["x"], Y - s["y"])
            if d.min() <= s.get("radius_m", 50.0) + site.res_m:
                hit.append({"poi": f"poi:{s['poi']}", "name": s["name"], "citation": s["citation"]})
        src = tier_sources(layer).get("confirmed_in_situ") or {}
        out["in_situ"] = {"area_m2": by.get("confirmed_in_situ", 0.0), "source_kind": src.get("kind", "rover_sample"),
                          "platform": src.get("platform", "rover (source not recorded in the catalogue)"),
                          "samples": hit}
    return out


# ---------------------------------------------------------------- registration caveats

def _layer_caveat_text(layer: Layer) -> str:
    reg = layer.meta.get("registration") or {}
    if layer.kind == "categorical":
        d = reg.get("contact_caveat_m", UNIT_CONTACT_M)
        if reg.get("shift_applied"):
            body = (f"the map was offset about {abs(reg.get('mosaic_offset_north_m') or 100):.0f} m from our terrain (measured) and a "
                    f"correction was applied, residual spread up to about {reg.get('residual_spread_max_m', '?')} m")
        else:
            body = "the map is offset about 100 m from our terrain (measured) and no correction was applied"
        return (f"{layer.id} caveat: within {d:.0f} m of a unit contact; {body}, and contacts are drawn at 1:75,000, "
                "so the unit here may be the neighbouring one.")
    d = reg.get("contact_caveat_m", TIER_CHANGE_M)
    return (f"{layer.id} caveat: within {d:.0f} m of a tier change; CRISM pixels are {reg.get('pixel_m', 36):.0f} m and the "
            "registration residual to our terrain was not measured, so the tier here may belong to the neighbouring pixel.")


def registration_layers(site: Site, layer_ids) -> list[Layer]:
    """The subset of layer_ids that carry registration limits (geologic units and evidence layers)."""
    out = []
    for lid in dict.fromkeys(layer_ids):
        l = site.layers.get(lid)
        if l is not None and l.kind in ("categorical", "evidence") and l.meta.get("registration"):
            out.append(l)
    return out


def _caveat_distance(layer: Layer) -> float:
    reg = layer.meta.get("registration") or {}
    return float(reg.get("contact_caveat_m", UNIT_CONTACT_M if layer.kind == "categorical" else TIER_CHANGE_M))


def near_boundary(site: Site, layer: Layer, rows, cols) -> bool:
    """True if any cell within the layer's caveat distance of the footprint (rows, cols) has a different value
    than the footprint's cells (class or tier, no_coverage counts as a value)."""
    rows, cols = np.asarray(rows), np.asarray(cols)
    if rows.size == 0:
        return False
    pad = int(math.ceil(_caveat_distance(layer) / site.res_m)) + 1
    n = site.size_px
    r0, r1 = max(rows.min() - pad, 0), min(rows.max() + pad + 1, n)
    c0, c1 = max(cols.min() - pad, 0), min(cols.max() + pad + 1, n)
    if max(r1 - r0, c1 - c0) > MAX_CAVEAT_CELLS:
        # huge footprint: only its edge matters; sample the window at a coarser step
        step = int(math.ceil(max(r1 - r0, c1 - c0) / MAX_CAVEAT_CELLS))
    else:
        step = 1
    win = layer.data[r0:r1:step, c0:c1:step]
    fp = np.zeros((r1 - r0, c1 - c0), bool)
    fp[rows - r0, cols - c0] = True
    fp = fp[::step, ::step]
    if not fp.any():
        return False
    dist = ndimage.distance_transform_edt(~fp) * (site.res_m * step)
    band = dist <= _caveat_distance(layer)
    inside = np.unique(win[fp])
    outside = np.unique(win[band & ~fp])
    return bool(np.setdiff1d(outside, inside).size) or bool(len(inside) > 1)


def boundary_flags(site: Site, layer_ids, rows, cols) -> list[str]:
    """Ids of layers (units / evidence) whose boundary lies within the caveat distance of the footprint."""
    return [l.id for l in registration_layers(site, layer_ids) if near_boundary(site, l, rows, cols)]


def caveat_texts(site: Site, layer_ids) -> dict[str, str]:
    """One caveat per kind of limit. Evidence layers that share the same limit are grouped into one entry."""
    out: dict[str, str] = {}
    groups: dict[str, list[str]] = {}
    for l in registration_layers(site, layer_ids):
        t = _layer_caveat_text(l)
        if l.kind == "categorical":
            out[l.id] = t
        else:
            groups.setdefault(t.split(" caveat: ", 1)[1], []).append(l.id)
    for body, ids in groups.items():
        who = ", ".join(ids) if len(ids) <= 3 else f"{len(ids)} evidence layers ({', '.join(ids[:2])}, ...)"
        out["evidence_tiers:" + ids[0]] = f"{who} caveat: {body}"
    return out


def disc_cells(site: Site, x: float, y: float, radius_m: float):
    """(rows, cols) of cells whose centre is within radius_m of (x, y) (at least the containing cell)."""
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


def referenced_layers(conds) -> list[str]:
    """Layer ids named in a condition list (including any_of)."""
    out = []
    for c in conds or []:
        if not isinstance(c, dict):
            continue
        if "any_of" in c:
            out.extend(referenced_layers(c["any_of"]))
        elif "evidence" in c:
            out.append(c["evidence"])
        elif "layer" in c:
            out.append(c["layer"])
    return out

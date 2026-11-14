"""/search/targets and /search/sites on top of atlas_core.find_regions.

Targets are evidence layers (minerals etc.) and the classes of categorical layers (geologic units).
Sites are connected regions where the target is present, ranked by best evidence tier (then area), each with
evidence entries the agent can cite. No 0-1 score: evidence is ordinal (see mcp-api-needs.md).
"""
from __future__ import annotations

import math

import numpy as np
from scipy import ndimage

from atlas_core import AtlasError, resolve_ref, route as R, search, travel
from atlas_core.conditions import combine, compile_all
from atlas_core.site import TIER_NAMES, TIERS, Site

FIELDS = ("target", "near", "radius_m", "max_slope_deg", "top_k", "min_tier", "min_area_m2", "profile")


def _src(layer) -> dict:
    s = layer.meta.get("source") or {}
    return {"mission": s.get("mission"), "instrument": s.get("instrument"), "product": s.get("product"),
            "citation": s.get("citation")}


def search_targets(site: Site) -> list[dict]:
    names: dict[str, int] = {}
    for l in site.layers.values():
        if l.kind == "categorical" and l.classes:
            for n in l.classes.values():
                names[n] = names.get(n, 0) + 1
    out = []
    for l in site.layers.values():
        if l.kind == "evidence":
            out.append({"target": l.id, "kind": "evidence", "layer": l.id, "class": None,
                        "title": l.meta.get("title", l.id), "description": l.meta.get("measures"),
                        "tiers": [TIER_NAMES[i] for i in sorted(TIER_NAMES)], **_src(l)})
        elif l.kind == "categorical" and l.classes:
            for code, n in sorted(l.classes.items()):
                out.append({"target": n if names[n] == 1 else f"{l.id}:{n}", "kind": "geologic_unit",
                            "layer": l.id, "class": n, "title": f"{l.meta.get('title', l.id)}: {n}",
                            "description": l.meta.get("measures"), "tiers": None, **_src(l)})
    return out


def search_sites(site: Site, store, body: dict) -> dict:
    if not isinstance(body, dict):
        raise AtlasError("The request body must be a JSON object.")
    unknown = sorted(set(body) - set(FIELDS))
    if unknown or not body.get("target"):
        raise AtlasError("Invalid search/sites request." if not unknown else f"Unknown field(s): {', '.join(unknown)}.",
                         hint="Fields: " + ", ".join(FIELDS) + ". target is required (see GET /search/targets).")
    targets = search_targets(site)
    want = str(body["target"]).strip()
    t = next((x for x in targets if x["target"] == want), None) or \
        next((x for x in targets if x["target"].lower() == want.lower()), None)
    if t is None:
        raise AtlasError(f'Unknown target "{want}".',
                         hint="Valid targets: " + ", ".join(x["target"] for x in targets) + ".")
    top_k = body.get("top_k", 5)
    if not isinstance(top_k, int) or isinstance(top_k, bool) or not 1 <= top_k <= 50:
        raise AtlasError("top_k must be an integer within 1..50.")
    min_area = body.get("min_area_m2", 100.0)
    profile = body.get("profile", "rover_scout")
    near_ref = body.get("near", "rover")
    near_pts = resolve_ref(site, near_ref, store)
    if len(near_pts) != 1:
        raise AtlasError("`near` must be one location.", hint='Use "rover", "base", a POI id or "marker:<id>".')
    near = near_pts[0]

    layer = site.layer(t["layer"])
    if t["kind"] == "evidence":
        min_tier = body.get("min_tier", "weak_orbital")
        if min_tier not in TIERS:
            raise AtlasError(f'Unknown min_tier "{min_tier}".', hint="Use one of: " + ", ".join(TIERS) + ".")
        conds = [{"evidence": layer.id, "min_tier": min_tier}]
        objective = f"best_evidence:{layer.id}"
    else:
        min_tier = None
        conds = [{"layer": layer.id, "op": "in", "value": [t["class"]]}]
        objective = "largest"
    if body.get("max_slope_deg") is not None:
        site.layer("slope_2m")
        conds.append({"layer": "slope_2m", "op": "<=", "value": float(body["max_slope_deg"])})
    within = None
    if body.get("radius_m") is not None:
        within = {"from": near_ref, "max": float(body["radius_m"]), "metric": "straight_m"}

    res = search.find_regions(site, store, conds, None, min_area, within, objective, top_k)

    # per-region evidence: relabel the same mask and read the region around each representative point
    _, _, compiled, allmask = search._compile(site, conds, None)
    mask = allmask
    if within is not None:
        mask = mask & travel.within_mask(site, within, store)[0]
    labels, _ = ndimage.label(mask, structure=np.ones((3, 3), bool))
    slices = ndimage.find_objects(labels)
    reach = travel.travel_field(site, [near], "walk_m", profile)
    slope = site.layers["slope_2m"].data if "slope_2m" in site.layers else None
    src = _src(layer)
    sites = []
    for m in res["results"]:
        r, c = (int(v) for v in site.rc_index(m["x"], m["y"]))
        lab = labels[r, c]
        cells = labels[slices[lab - 1]] == lab if lab else None
        sl = slices[lab - 1] if lab else None
        z = R.terrain_z(site, m["x"], m["y"])
        evidence = []
        if t["kind"] == "evidence":
            tiers = layer.data[sl][cells]
            best = int(tiers.max())
            share = float((tiers >= TIERS[min_tier]).mean())
            evidence.append({"layer": layer.id, **src, "value": TIER_NAMES[best], "tier": best,
                             "area_at_or_above_min_tier_m2": round(float(cells.sum()) * site.res_m ** 2, 1)})
            best_tier, best_name = best, TIER_NAMES[best]
        else:
            evidence.append({"layer": layer.id, **src, "value": t["class"],
                             "area_m2": round(float(cells.sum()) * site.res_m ** 2, 1)})
            best_tier = best_name = None
        mean_slope = None
        if slope is not None:
            vals = slope[sl][cells]
            vals = vals[np.isfinite(vals)]
            if vals.size:
                mean_slope = round(float(vals.mean()), 2)
                evidence.append({"layer": "slope_2m", **_src(site.layers["slope_2m"]), "value": mean_slope,
                                 "units": "degrees (mean over region)"})
        rr, rc = (int(v) for v in site.rc_index(m["x"], m["y"]))
        reach_m = float(reach[rr, rc]) if 0 <= rr < reach.shape[0] and 0 <= rc < reach.shape[1] else math.inf
        sites.append({
            "id": m["id"], "x": m["x"], "y": m["y"], "z": round(z, 2) if math.isfinite(z) else None,
            "lat": m["lat"], "lon": m["lon"], "best_tier": best_tier, "best_tier_name": best_name,
            "area_m2": m["area_m2"], "mean_slope_deg": mean_slope,
            "distance_m": round(math.hypot(m["x"] - near["x"], m["y"] - near["y"]), 1),
            "reachable": bool(math.isfinite(reach_m)),
            "walk_distance_m": round(reach_m, 1) if math.isfinite(reach_m) else None,
            "bbox": m["bbox"], "evidence": evidence,
        })
    unreachable = [s["id"] for s in sites if not s["reachable"]]
    hint = res["hint"]
    if unreachable:
        hint += (f" Sites {', '.join(unreachable)} are not reachable from {near.get('id')} with the {profile} "
                 "slope limit (they are cut off by steep or unknown ground).")
    return {
        "target": t["target"], "kind": t["kind"], "layer": layer.id, "count": res["count"], "set_id": res["set_id"],
        "sites": sites, "near": {"id": near.get("id"), "x": near["x"], "y": near["y"]},
        "filters": {"min_tier": min_tier, "radius_m": body.get("radius_m"), "max_slope_deg": body.get("max_slope_deg"),
                    "min_area_m2": min_area, "reachability_profile": profile},
        "assumptions": res["assumptions"], "diagnostics": res["diagnostics"], "hint": hint,
    }

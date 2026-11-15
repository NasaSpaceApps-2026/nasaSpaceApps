"""/search/targets and /search/sites on top of atlas_core.find_regions.

Targets are evidence layers (minerals etc.) and the classes of categorical layers (geologic units).
Sites are connected regions where the target is present, ranked by best evidence tier (then area), each with
evidence entries the agent can cite. No 0-1 score: evidence is ordinal (see mcp-api-needs.md).
"""
from __future__ import annotations

import difflib
import math

import numpy as np
from scipy import ndimage

from atlas_core import AtlasError, evidence as EV, resolve_ref, route as R, search, travel
from atlas_core.catalog import alias_groups, alias_table, resolution_text, resolve_alias
from atlas_core.site import TIER_NAMES, TIERS, Site

FIELDS = ("target", "near", "radius_m", "max_slope_deg", "top_k", "min_tier", "min_area_m2", "profile")


def _src(layer) -> dict:
    s = layer.meta.get("source") or {}
    return {"mission": s.get("mission"), "instrument": s.get("instrument"), "product": s.get("product"),
            "citation": s.get("citation")}


NON_SCIENCE_LAYERS = {"quality_flag"}


def _science_layers(site: Site):
    """Layers that describe the ground (minerals, geology), not data quality."""
    return [l for l in site.layers.values()
            if l.id not in NON_SCIENCE_LAYERS and l.meta.get("role") != "data_quality"]


def search_targets(site: Site) -> list[dict]:
    """GET /search/targets: evidence layers and categorical classes that can be searched (a class name shared by several layers is prefixed "<layer>:")."""
    names: dict[str, int] = {}
    for l in _science_layers(site):
        if l.kind == "categorical" and l.classes:
            for n in l.classes.values():
                names[n] = names.get(n, 0) + 1
    out = []
    for l in _science_layers(site):
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
    """POST /search/sites: connected regions where `target` is present, ranked by best evidence tier then area, each with citable evidence. Raises AtlasError (hint) for an invalid body or unknown target."""
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
    resolved = None            # how an alias was resolved, e.g. "clay -> fe_mg_smectite, kaolinite, mg_smectite_2"
    group = None               # evidence layer ids when an alias stands for several layers
    if t is None:
        ids = [i for i in (resolve_alias(site, want) or [])
               if site.layers[i].kind == "evidence"]
        if ids:
            resolved = resolution_text(want, ids)
            if len(ids) == 1:
                t = next(x for x in targets if x["target"] == ids[0])
            else:
                group = sorted(ids)
                t = next(x for x in targets if x["target"] == group[0])
    if t is None:
        if not targets:
            raise AtlasError(f'No searchable science targets are loaded for this site, so "{want}" cannot be searched.',
                             hint="This site has terrain layers only (no mineral or geology layers yet). "
                                  "Say that the data cannot answer; terrain questions still work.")
        names = [x["target"] for x in targets]
        pool = {n.lower(): n for n in names}
        for e in alias_table(site).values():
            pool.setdefault(e["alias"].lower(), e["alias"])
        close = [pool[c] for c in difflib.get_close_matches(want.lower(), list(pool), n=3, cutoff=0.6)]
        hint = (f"Did you mean {', '.join(close)}? " if close else "") + "Valid targets: " + ", ".join(names) + "."
        groups = alias_groups(site)
        if groups:
            hint += " Aliases: " + "; ".join(", ".join(g["aliases"][:3]) + " -> " + ", ".join(g["layers"])
                                             for g in groups[:6]) + "."
        raise AtlasError(f'Unknown target "{want}".', hint=hint)
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
    elayers = [site.layer(i) for i in group] if group else [layer]
    if t["kind"] == "evidence":
        min_tier = body.get("min_tier", "weak_orbital")
        if min_tier not in TIERS:
            raise AtlasError(f'Unknown min_tier "{min_tier}".', hint="Use one of: " + ", ".join(TIERS) + ".")
        if group:
            conds = [{"any_of": [{"evidence": l.id, "min_tier": min_tier} for l in elayers]}]
        else:
            conds = [{"evidence": layer.id, "min_tier": min_tier}]
        objective = "best_evidence:" + ",".join(l.id for l in elayers)
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
            entries = []
            for el in elayers:
                tiers = el.data[sl][cells]
                best_l = int(tiers.max())
                rr_, cc_ = np.nonzero(cells)
                sm = EV.summarize(site, el, rr_ + sl[0].start, cc_ + sl[1].start)
                entries.append((best_l, sm, {
                    "layer": el.id, **_src(el), "value": TIER_NAMES[best_l], "tier": best_l,
                    "area_at_or_above_min_tier_m2": round(float(cells.sum()) * site.res_m ** 2, 1),
                    "area_by_tier_m2": sm["area_by_tier_m2"], "orbital_source": sm["orbital_source"],
                    "best_tier_basis": ("rover sample: " + ("; ".join(x["name"] for x in sm["in_situ"]["samples"])
                                                             or "sample not identified")
                                        if best_l == 4 and sm.get("in_situ") else f"orbital: {sm['orbital_source']}"),
                    **({"in_situ": sm["in_situ"]} if sm.get("in_situ") else {})}))
            entries.sort(key=lambda e: -e[0])          # best layer first (stable: catalogue order on ties)
            best, sm, top = entries[0]
            evidence.extend(e[2] for e in entries)
            best_tier, best_name = best, TIER_NAMES[best]
            area_by_tier = sm["area_by_tier_m2"]
            basis = top["best_tier_basis"]
        else:
            evidence.append({"layer": layer.id, **src, "value": t["class"],
                             "area_m2": round(float(cells.sum()) * site.res_m ** 2, 1)})
            best_tier = best_name = None
            area_by_tier = basis = None
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
            "area_m2": m["area_m2"], "area_by_tier_m2": area_by_tier, "best_tier_basis": basis,
            "mean_slope_deg": mean_slope,
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
        "target": want if resolved else t["target"], "kind": t["kind"], "layer": layer.id,
        "layers": [l.id for l in elayers], "resolved": resolved, "count": res["count"], "set_id": res["set_id"],
        "sites": sites, "near": {"id": near.get("id"), "x": near["x"], "y": near["y"]},
        "filters": {"min_tier": min_tier, "radius_m": body.get("radius_m"), "max_slope_deg": body.get("max_slope_deg"),
                    "min_area_m2": min_area, "reachability_profile": profile},
        "assumptions": res["assumptions"], "diagnostics": res["diagnostics"], "hint": hint,
        "registration_notes": EV.caveat_texts(site, [l.id for l in elayers]),
    }

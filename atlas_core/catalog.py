"""Site overview and layer catalogue. Spec: contracts/mcp-tools.md sections 4.1 and 4.2.
OWNER: builder "server". Signatures and return shapes are FROZEN; implement the bodies.
"""
from __future__ import annotations

import copy

import numpy as np

from .errors import AtlasError
from .site import TIER_NAMES, Site

_KIND_ORDER = {"continuous": 0, "categorical": 1, "evidence": 2}
_POI_PRIORITY = {"base": 0, "landing_site": 1, "landing": 1, "landmark": 2, "sample_site": 3,
                 "science_target": 4}


def _measures(layer) -> str:
    return str(layer.meta.get("measures") or "")


def site_overview(site: Site) -> dict:
    """{
      "site": {"name", "lat0", "lon0", "size_m", "res_m",
               "frame": "Site frame: metres, x east, y north, origin at site centre (lat0, lon0)."},
      "layers": [{"id", "kind", "title", "measures"}],
      "profiles": [{"name", "description"}],
      "pois": [{"id", "name", "type", "x", "y"}],          # up to 12, bases and landmarks first
      "rover": {"x", "y"} | None,
      "evidence_tiers": ["not_detected", ..., "confirmed_in_situ"],   # ascending
      "next": "Call describe_layer(id) before relying on an unfamiliar layer; ..."
    }"""
    layers = sorted(site.layers.values(), key=lambda l: (_KIND_ORDER.get(l.kind, 9), l.id))
    pois = sorted(site.pois, key=lambda p: _POI_PRIORITY.get(p.get("type"), 9))[:12]
    profiles = [{"name": n, "description": (p or {}).get("description", "")}
                for n, p in site.profiles.items()]
    rover = None
    if site.rover_xy is not None:
        rover = {"x": float(site.rover_xy[0]), "y": float(site.rover_xy[1])}
    return {
        "site": {
            "name": site.name, "lat0": site.lat0, "lon0": site.lon0,
            "size_m": site.size_px * site.res_m, "res_m": site.res_m,
            "frame": "Site frame: metres, x east, y north, origin at site centre (lat0, lon0).",
        },
        "layers": [{"id": l.id, "kind": l.kind, "title": l.meta.get("title", l.id),
                    "measures": _measures(l)} for l in layers],
        "profiles": profiles,
        "pois": [{"id": p.get("id"), "name": p.get("name"), "type": p.get("type"),
                  "x": p.get("x"), "y": p.get("y")} for p in pois],
        "rover": rover,
        "evidence_tiers": [TIER_NAMES[i] for i in sorted(TIER_NAMES)],
        "next": ("Call describe_layer(id) before relying on an unfamiliar layer; use "
                 "describe_location(at) to inspect a place, find_locations(...) for footprint-sized "
                 "spots and find_regions(...) for connected areas."),
    }


def list_layers(site: Site, kind: str | None = None, query: str | None = None) -> dict:
    """{"layers": [{"id", "kind", "title", "measures", "resolution_m"}]}; query matches
    id/title/measures case-insensitively."""
    if kind is not None and kind not in _KIND_ORDER:
        raise AtlasError(f'Unknown layer kind "{kind}".',
                         hint="Kinds: continuous, categorical, evidence.")
    q = query.strip().lower() if query else None
    out = []
    for l in sorted(site.layers.values(), key=lambda l: (_KIND_ORDER.get(l.kind, 9), l.id)):
        if kind and l.kind != kind:
            continue
        title = str(l.meta.get("title", l.id))
        if q and q not in f"{l.id} {title} {_measures(l)}".lower():
            continue
        out.append({"id": l.id, "kind": l.kind, "title": title, "measures": _measures(l),
                    "resolution_m": l.meta.get("resolution_m")})
    return {"layers": out}


def describe_layer(site: Site, layer_id: str) -> dict:
    """Full catalogue entry (meta) plus: "id", "kind", "classes" (categorical: list of names;
    evidence: tier scale), "coverage_fraction" (share of cells not unknown), and for continuous
    layers "min", "max", "median"."""
    layer = site.layer(layer_id)
    d = copy.deepcopy(layer.meta)
    d["id"] = layer.id
    d["kind"] = layer.kind
    known = ~layer.unknown
    d["coverage_fraction"] = float(known.mean())
    if layer.kind == "categorical":
        d["classes"] = [layer.classes[k] for k in sorted(layer.classes or {})]
    elif layer.kind == "evidence":
        d["classes"] = [f"{i} {TIER_NAMES[i]}" for i in sorted(TIER_NAMES)]
    if layer.kind == "continuous":
        vals = layer.data[known]
        if vals.size:
            d["min"], d["max"], d["median"] = (float(vals.min()), float(vals.max()),
                                                float(np.median(vals)))
        else:
            d["min"] = d["max"] = d["median"] = None
    return d

"""Image overlays for the Jezero viewer: GET /overlay/{kind}.png, /overlay/{kind}.json, /overlay/kinds, /traverse.geojson.

Contract: devapi/OVERLAYS.md. Every PNG is RGBA and covers exactly the site square (row 0 north, col 0 west), at most
2048 px per side. Results (PNG bytes + JSON metadata) are cached in an LRU keyed by kind + normalised params, so the
.png and .json of one parameter set share one computation. Raster work lives in atlas_core.overlay.
"""
from __future__ import annotations

import io
import json
import math
import os
import threading
from collections import OrderedDict

import numpy as np
from fastapi import APIRouter, Request
from fastapi.responses import Response
from PIL import Image

from atlas_core import AtlasError, lake, overlay as O, sun as S, illumination as I
from atlas_core.site import TIER_NAMES

from .util import clean

MAX_PX = 2048
CACHE_SIZE = 48
DEFAULT_LAKE_LEVEL = -2410.0
EVIDENCE_LAYERS = ("olivine", "carbonate", "fe_mg_smectite", "kaolinite", "high_ca_pyroxene", "low_ca_pyroxene",
                   "mg_smectite_2")
SLOPE_PROFILES = ("eva_walkable", "rover_scout", "crew_buggy")
KINDS = ("evidence", "geology", "slope", "lake", "sun", "viewshed", "envelope")

# colour-blind-safe (Okabe-Ito based) palettes
TIER_STYLE = {   # tier code -> (rgb, alpha, label)
    4: ((255, 225, 0), 235, "Confirmed by the rover on the ground"),
    3: ((0, 194, 209), 210, "Strong signal from orbit"),
    2: ((148, 110, 255), 150, "Weak signal from orbit"),
}
NO_COVERAGE_RGB, NO_COVERAGE_ALPHA = (128, 128, 128), 90
GEO_PALETTE = [(230, 159, 0), (86, 180, 233), (0, 158, 115), (240, 228, 66), (0, 114, 178), (213, 94, 0),
               (204, 121, 167), (153, 153, 153)]
SLOPE_STYLE = {0: ((44, 160, 94), 80, "Safe"), 1: ((253, 174, 38), 175, "Caution"), 2: ((215, 25, 28), 205, "No-go")}
SHADOW_RGB = (10, 14, 32)


def _hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(int(v) for v in rgb)


def _src(layer) -> str:
    s = (layer.meta or {}).get("source") or {}
    parts = [s.get("mission"), s.get("instrument")]
    head = " / ".join(p for p in parts if p)
    cite = s.get("citation")
    return "; ".join(p for p in (head, s.get("product"), cite) if p) or "not stated in the catalogue"


class Params:
    """Query params of one request: typed access with defaults, ranges and 'unknown parameter' checking."""
    def __init__(self, q: dict, allowed: tuple[str, ...]):
        unknown = sorted(set(q) - set(allowed))
        if unknown:
            raise AtlasError(f"Unknown parameter(s): {', '.join(unknown)}.",
                             hint="Valid parameters: " + (", ".join(allowed) or "none") + ".")
        self.q = q
        self.used: dict = {}

    def num(self, name, default, lo=None, hi=None, required=False):
        raw = self.q.get(name)
        if raw is None or raw == "":
            if required and default is None:
                raise AtlasError(f"Missing required parameter: {name}.")
            v = default
        else:
            try:
                v = float(raw)
            except ValueError:
                raise AtlasError(f"{name} must be a number, got {raw!r}.") from None
            if not math.isfinite(v):
                raise AtlasError(f"{name} must be finite, got {raw!r}.")
        if v is not None and ((lo is not None and v < lo) or (hi is not None and v > hi)):
            raise AtlasError(f"{name}={v:g} is outside the allowed range {lo:g}..{hi:g}.")
        self.used[name] = None if v is None else round(float(v), 3)
        return v

    def choice(self, name, default, options):
        v = self.q.get(name) or default
        if v not in options:
            raise AtlasError(f"{name}={v!r} is not valid.", hint="Options: " + ", ".join(options) + ".")
        self.used[name] = v
        return v

    def key(self):
        return tuple(sorted(self.used.items()))


# ---- RGBA helpers ------------------------------------------------------------------------------------

def _canvas(shape):
    return np.zeros(shape + (4,), np.uint8)


def _paint(img, mask, rgb, alpha):
    img[mask] = (*rgb, int(alpha))


def _hatch(shape, period=14, width=3):
    r, c = np.indices(shape)
    return ((r + c) % period) < width


def _encode(img: np.ndarray) -> bytes:
    b = io.BytesIO()
    Image.fromarray(img, "RGBA").save(b, format="PNG", compress_level=1)
    return b.getvalue()


def _ramp(t, stops, levels=64):
    """t in 0..1 (array) -> RGB uint8 through colour stops [(pos, rgb), ...], quantised to `levels` steps (lookup table)."""
    xs = [p for p, _ in stops]
    ts = np.linspace(0, 1, levels)
    lut = np.stack([np.interp(ts, xs, [c[i] for _, c in stops]) for i in range(3)], axis=-1).astype(np.uint8)
    idx = np.clip(np.rint(np.clip(t, 0, 1) * (levels - 1)), 0, levels - 1).astype(np.intp)
    return lut[idx]


# ---- the overlay builders: (ctx, Params) -> (img RGBA, meta) ------------------------------------------

class Ctx:
    def __init__(self, site, traverse):
        self.site = site
        self.traverse = traverse
        self.f = max(1, math.ceil(site.size_px / MAX_PX))
        self.px = site.size_px // self.f
        self.sun_cache: dict = {}

    def bounds(self):
        s = self.site
        return {"half_m": s.half_m, "lat0": s.lat0, "lon0": s.lon0, "px": self.px, "res_m": s.res_m * self.f}


def b_evidence(ctx, p: Params):
    layer_id = p.choice("layer", "olivine", EVIDENCE_LAYERS)
    if layer_id not in ctx.site.layers:
        raise AtlasError(f'The site has no layer "{layer_id}".', hint="Layers: " + ", ".join(sorted(ctx.site.layers)) + ".")
    layer = ctx.site.layer(layer_id)
    t = O.downsample_nearest(layer.data, ctx.f)
    img = _canvas(t.shape)
    _paint(img, (t == -1) & _hatch(t.shape), NO_COVERAGE_RGB, NO_COVERAGE_ALPHA)
    legend = []
    for tier in (2, 3, 4):      # weakest first so the strongest is painted on top
        rgb, a, _ = TIER_STYLE[tier]
        _paint(img, t == tier, rgb, a)
    for tier in (4, 3, 2):
        rgb, a, label = TIER_STYLE[tier]
        legend.append({"color": _hex(rgb), "label": label})
    legend.append({"color": _hex(NO_COVERAGE_RGB), "label": "No orbital coverage (hatched grey)"})
    full = layer.data
    cell = ctx.site.res_m ** 2
    tiers = {TIER_NAMES[k]: round(float((full == k).sum()) * cell / 1e6, 4) for k in (4, 3, 2, 1, 0)}
    tiers["no_coverage"] = round(float((full == -1).sum()) * cell / 1e6, 4)
    stats = {"layer": layer_id, "tiers_km2": tiers,
             "detected_km2": round(float((full >= 2).sum()) * cell / 1e6, 4),
             "no_coverage_fraction": round(float((full == -1).mean()), 4)}
    meta = {"title": f"{layer.meta.get('title', layer_id)}: strength of the evidence",
            "legend": legend, "source": _src(layer),
            "caveat": "Orbital detections are 36 m pixels showing the dominant mineral; 'not detected' is left clear because "
                      "it does not prove the mineral is absent.", "stats": stats}
    return img, meta


def b_geology(ctx, p: Params):
    layer = ctx.site.layer("geologic_unit")
    t = O.downsample_nearest(layer.data, ctx.f)
    img = _canvas(t.shape)
    legend = []
    for i, (code, name) in enumerate(sorted((layer.classes or {}).items())):
        rgb = GEO_PALETTE[i % len(GEO_PALETTE)]
        _paint(img, t == code, rgb, 165)
        legend.append({"color": _hex(rgb), "label": name})
    cell = ctx.site.res_m ** 2
    stats = {"units_km2": {name: round(float((layer.data == code).sum()) * cell / 1e6, 4)
                           for code, name in sorted((layer.classes or {}).items())}}
    return img, {"title": "Geologic units", "legend": legend, "source": _src(layer),
                 "caveat": "Unit boundaries are mapped on CTX imagery and are approximate at the scale of tens of metres.",
                 "stats": stats}


def b_slope(ctx, p: Params):
    avail = [n for n, pr in ctx.site.profiles.items() if isinstance(pr, dict) and pr.get("travel")]
    prof = p.choice("profile", "eva_walkable", tuple(avail) or SLOPE_PROFILES)
    cls, params = O.slope_classes(ctx.site, prof)
    t = O.downsample_nearest(cls, ctx.f)
    img = _canvas(t.shape)
    for k, (rgb, a, _) in SLOPE_STYLE.items():
        _paint(img, t == k, rgb, a)
    cau, mx = params["caution_slope_deg"], params["max_slope_deg"]
    legend = [{"color": _hex(SLOPE_STYLE[0][0]), "label": f"Safe (up to {cau:g} degrees)"},
              {"color": _hex(SLOPE_STYLE[1][0]), "label": f"Caution ({cau:g} to {mx:g} degrees)"},
              {"color": _hex(SLOPE_STYLE[2][0]), "label": f"No-go (steeper than {mx:g} degrees)"}]
    known = max(int((cls >= 0).sum()), 1)
    stats = {"profile": prof, "caution_slope_deg": cau, "max_slope_deg": mx, "limits_source": "assumed",
             "safe_fraction": round(float((cls == 0).sum()) / known, 4),
             "caution_fraction": round(float((cls == 1).sum()) / known, 4),
             "no_go_fraction": round(float((cls == 2).sum()) / known, 4)}
    meta = {"title": f"Slope safety for {prof.replace('_', ' ')}", "legend": legend,
            "source": _src(ctx.site.layer("slope_2m")),
            "caveat": "The slope limits for this vehicle or suit are team assumptions, not tested values.", "stats": stats}
    return img, meta


def _named_levels():
    return [{"id": k, "level_m": v["level_m"], "label": v["what"], "kind": v["kind"], "citation": v["source"]}
            for k, v in lake.NAMED_LEVELS.items()]


def _elev_range(site):
    z = site.layer("elevation").data
    return float(np.nanmin(z)), float(np.nanmax(z))


def b_lake(ctx, p: Params):
    zmin, zmax = _elev_range(ctx.site)
    default = min(max(DEFAULT_LAKE_LEVEL, math.floor(zmin)), math.ceil(zmax))
    level = p.num("level", default, math.floor(zmin), math.ceil(zmax))
    depth = O.lake_depth_grid(ctx.site, level)
    water = np.isfinite(depth)
    frac = O.downsample_mean(water, ctx.f)
    d = O.downsample_mean(depth, ctx.f)
    img = _canvas(frac.shape)
    m = frac > 0
    dmax = float(np.nanmax(depth)) if water.any() else 1.0
    rgb = _ramp(np.where(m, d, 0) / max(dmax, 1e-6), [(0, (166, 219, 255)), (0.5, (49, 130, 206)), (1, (8, 40, 110))])
    img[m, :3] = rgb[m]
    img[m, 3] = (np.clip(frac[m], 0, 1) * (130 + 80 * np.clip(d[m] / max(dmax, 1e-6), 0, 1))).astype(np.uint8)
    cell = ctx.site.res_m ** 2
    area = float(water.sum()) * cell / 1e6
    mean_d = float(np.nanmean(depth)) if water.any() else 0.0
    stats = {"level_m": level, "area_km2": round(area, 4), "max_depth_m": round(dmax if water.any() else 0.0, 2),
             "mean_depth_m": round(mean_d, 2), "named_levels": _named_levels(),
             "touches_site_edge": bool(water[0].any() or water[-1].any() or water[:, 0].any() or water[:, -1].any())}
    legend = [{"color": "#a6dbff", "label": "Shallow water"}, {"color": "#3182ce", "label": f"Mid depth (about {dmax / 2:.0f} m)"},
              {"color": "#08286e", "label": f"Deepest (about {dmax:.0f} m)"}]
    meta = {"title": f"Ancient lake at {level:g} m", "legend": legend,
            "source": _src(ctx.site.layer("elevation")) + "; levels: " + "; ".join(sorted({v['source'] for v in lake.NAMED_LEVELS.values()})),
            "caveat": lake.CAVEAT.split(". ")[0].rstrip(".") + ".", "stats": stats}
    return img, meta


def _sun_state(ctx, sol, ltst):
    site = ctx.site
    t = I.resolve_time(None, sol, ltst, site.lon0)
    sp = S.sun_position(t, site.lat0, site.lon0)
    k = ("ss", sp["mission_sol"])
    if k not in ctx.sun_cache:
        ctx.sun_cache[k] = S.sunrise_sunset(t, site.lat0, site.lon0)
    return sp, ctx.sun_cache[k]


def b_sun(ctx, p: Params):
    ltst = p.num("ltst", 12.0, 0, 24)
    cur = S.mission_sol(S.parse_utc(None), ctx.site.lon0)
    sol_v = p.num("sol", float(cur), -1000, 100000)
    sol = int(round(sol_v))
    sp, rs = _sun_state(ctx, sol, ltst)
    el, az = sp["sun_elevation_deg"], sp["sun_azimuth_deg"]
    dem = ctx.site.layer("elevation").data
    if el <= 0:
        shape = (ctx.px, ctx.px)
        cls = None
        dark = np.isfinite(O.downsample_mean(dem, ctx.f))
        img = _canvas(shape)
        _paint(img, dark, SHADOW_RGB, 160)
        lit_f = 0.0
    else:
        cls = O.sun_classes(ctx.site, el, az)
        t = O.downsample_nearest(cls, ctx.f)
        img = _canvas(t.shape)
        _paint(img, t == I.CAST_SHADOW, SHADOW_RGB, 165)
        _paint(img, t == I.SELF_SHADOW, SHADOW_RGB, 120)
        known = max(int((cls != I.UNKNOWN).sum()), 1)
        lit_f = float((cls == I.LIT).sum()) / known
    stats = {"sol": sol, "ltst": round(ltst, 3), "ltst_clock": S.hms(ltst), "utc": sp["utc"],
             "sun_elevation_deg": round(el, 2), "sun_azimuth_deg": round(az, 2),
             "sunrise_ltst": (rs.get("sunrise") or {}).get("ltst"), "sunset_ltst": (rs.get("sunset") or {}).get("ltst"),
             "sunrise_ltst_hours": (rs.get("sunrise") or {}).get("ltst_hours"),
             "sunset_ltst_hours": (rs.get("sunset") or {}).get("ltst_hours"),
             "sun_up": el > 0, "lit_fraction": round(lit_f, 4)}
    if cls is not None:
        known = max(int((cls != I.UNKNOWN).sum()), 1)
        stats["cast_shadow_fraction"] = round(float((cls == I.CAST_SHADOW).sum()) / known, 4)
        stats["self_shadow_fraction"] = round(float((cls == I.SELF_SHADOW).sum()) / known, 4)
    else:
        stats["cast_shadow_fraction"] = stats["self_shadow_fraction"] = 0.0
    legend = [{"color": _hex(SHADOW_RGB), "label": "In shadow (hidden from the sun by terrain)"},
              {"color": "#ffffff", "label": "Lit (clear, no tint)"}]
    if el <= 0:
        legend[0]["label"] = "Sun is below the horizon: no direct sunlight"
    meta = {"title": f"Sun and shadow, sol {sol}, {S.hms(ltst)[:5]} local solar time", "legend": legend,
            "source": "Mars24 solar geometry (Allison and McEwen 2000) on the HiRISE 2 m elevation model (Fergason et al. 2020)",
            "caveat": "Shadow here means no direct sunlight from terrain only; Mars twilight and dust-scattered light are not modelled.",
            "stats": stats}
    return img, meta


def b_viewshed(ctx, p: Params):
    x = p.num("x", 0.0, -ctx.site.half_m, ctx.site.half_m)
    y = p.num("y", 0.0, -ctx.site.half_m, ctx.site.half_m)
    h = p.num("h", 1.7, 0, 1000)
    radius = p.num("radius", 3000.0, 10, 8000)
    vis, st = O.viewshed_mask(ctx.site, x, y, h, radius)
    frac = O.downsample_mean(vis, ctx.f)
    img = _canvas(frac.shape)
    m = frac > 0
    img[m] = (255, 176, 0, 0)
    img[m, 3] = (np.clip(frac[m], 0, 1) * 120).astype(np.uint8)
    stats = {"x": x, "y": y, "h": h, "radius": radius, "visible_km2": round(st["visible_km2"], 4),
             "visible_fraction": round(st["visible_fraction"], 4), "farthest_visible_m": round(st["farthest_visible_m"], 1),
             "observer_elevation_m": round(st["elevation_m"], 2)}
    meta = {"title": f"What can be seen from here (eye height {h:g} m)",
            "legend": [{"color": "#ffb000", "label": f"Visible within {radius:g} m"}, {"color": "#ffffff", "label": "Hidden (clear)"}],
            "source": _src(ctx.site.layer("elevation")),
            "caveat": "Line of sight over the 2 m terrain model only: rocks smaller than a few metres and ground with no data are not modelled.",
            "stats": stats}
    return img, meta


def b_envelope(ctx, p: Params):
    x = p.num("x", 0.0, -ctx.site.half_m, ctx.site.half_m)
    y = p.num("y", 0.0, -ctx.site.half_m, ctx.site.half_m)
    budget = p.num("budget_min", 240.0, 1, 100000)
    reserve = p.num("reserve", 0.2, 0, 0.99)
    avail = [n for n, pr in ctx.site.profiles.items() if isinstance(pr, dict) and pr.get("travel")]
    prof = p.choice("profile", "eva_walkable", tuple(avail) or ("eva_walkable",))
    t1, info = O.envelope_one_way(ctx.site, x, y, prof, budget, reserve)
    ok = np.isfinite(t1)
    d = O.downsample_nearest(t1, ctx.f)          # nearest: one-way times are a smooth field and this halves the cold time
    cover = np.isfinite(d).astype(np.float32)
    limit = info["one_way_limit_min"]
    img = _canvas(d.shape)
    m = cover > 0.25
    rgb = _ramp(np.where(m, np.nan_to_num(d) / max(limit, 1e-6), 0), [(0, (255, 237, 160)), (0.5, (240, 120, 40)), (1, (140, 20, 120))])
    img[m, :3] = rgb[m]
    img[m, 3] = 150
    cell = ctx.site.res_m ** 2
    area = float(ok.sum()) * cell / 1e6
    maxr = 0.0
    if ok.any():
        rr, cc = np.nonzero(ok)
        xs, ys = ctx.site.rc_to_xy(rr, cc)
        maxr = float(np.hypot(xs - x, ys - y).max())
    stats = {"x": x, "y": y, "profile": prof, "budget_min": budget, "reserve_fraction": reserve,
             "usable_min": round(info["usable_min"], 2), "one_way_limit_min": round(limit, 2),
             "area_km2": round(area, 4), "max_radius_m": round(maxr, 1),
             "max_one_way_min": round(float(np.nanmax(t1)), 1) if ok.any() else 0.0,
             "touches_site_edge": bool(ok[0].any() or ok[-1].any() or ok[:, 0].any() or ok[:, -1].any())}
    legend = [{"color": "#ffeda0", "label": "Near the start (a few minutes one way)"},
              {"color": "#f07828", "label": f"About {limit / 2:.0f} minutes one way"},
              {"color": "#8c1478", "label": f"At the limit ({limit:.0f} minutes one way)"}]
    meta = {"title": f"Safe out-and-back reach, {budget:g} min with {reserve * 100:.0f}% reserve", "legend": legend,
            "source": _src(ctx.site.layer("slope_2m")),
            "caveat": "Walking speeds and slope limits are team assumptions with a placeholder speed curve, not measured suit or vehicle data.",
            "stats": stats}
    return img, meta


BUILDERS = {"evidence": (b_evidence, ("layer",)), "geology": (b_geology, ()), "slope": (b_slope, ("profile",)),
            "lake": (b_lake, ("level",)), "sun": (b_sun, ("ltst", "sol")),
            "viewshed": (b_viewshed, ("x", "y", "h", "radius")),
            "envelope": (b_envelope, ("x", "y", "budget_min", "reserve", "profile"))}


def _kinds(ctx):
    site = ctx.site
    zmin, zmax = _elev_range(site)
    zlo, zhi = math.floor(zmin), math.ceil(zmax)
    prof = [n for n, pr in site.profiles.items() if isinstance(pr, dict) and pr.get("travel")]
    pos = lambda name, d: {"name": name, "type": "number", "default": d, "min": -site.half_m, "max": site.half_m, "unit": "m"}
    ev = [l for l in EVIDENCE_LAYERS if l in site.layers]
    return [
        {"kind": "evidence", "title": "Mineral evidence strength", "params": [
            {"name": "layer", "type": "enum", "options": ev, "default": ev[0] if ev else None}]},
        {"kind": "geology", "title": "Geologic units", "params": []},
        {"kind": "slope", "title": "Slope safety", "params": [
            {"name": "profile", "type": "enum", "options": prof, "default": "eva_walkable" if "eva_walkable" in prof else (prof[0] if prof else None)}]},
        {"kind": "lake", "title": "Ancient lake level", "params": [
            {"name": "level", "type": "number", "default": min(max(DEFAULT_LAKE_LEVEL, zlo), zhi), "min": zlo, "max": zhi, "unit": "m"}],
         "named_levels": _named_levels()},
        {"kind": "sun", "title": "Sun and shadow", "params": [
            {"name": "ltst", "type": "number", "default": 12.0, "min": 0, "max": 24, "unit": "h"},
            {"name": "sol", "type": "integer", "default": "current mission sol", "min": 0, "max": 100000}]},
        {"kind": "viewshed", "title": "Viewshed", "params": [
            pos("x", 0.0), pos("y", 0.0),
            {"name": "h", "type": "number", "default": 1.7, "min": 0, "max": 1000, "unit": "m"},
            {"name": "radius", "type": "number", "default": 3000, "min": 10, "max": 8000, "unit": "m"}]},
        {"kind": "envelope", "title": "Safe out-and-back reach", "params": [
            pos("x", 0.0), pos("y", 0.0),
            {"name": "budget_min", "type": "number", "default": 240, "min": 1, "max": 100000, "unit": "min"},
            {"name": "reserve", "type": "number", "default": 0.2, "min": 0, "max": 0.99},
            {"name": "profile", "type": "enum", "options": prof, "default": "eva_walkable" if "eva_walkable" in prof else (prof[0] if prof else None)}]},
    ]


# ---- traverse ----------------------------------------------------------------------------------------

def build_traverse(site, traverse: dict | None) -> dict:
    """Traverse line plus waypoints with sol numbers; each waypoint gets `vertex_index` (position along the line, monotonic)
    so the viewer can draw the line up to a sol with line.coordinates[:vertex_index + 1]."""
    if traverse is None:
        d = os.environ.get("ATLAS_SITE_DIR")
        p = os.path.join(d, "traverse.geojson") if d else None
        if p and os.path.exists(p):
            with open(p) as f:
                traverse = json.load(f)
    if not traverse:
        return {"type": "FeatureCollection", "features": [], "metadata": {"note": "no traverse for this site"}}
    line = next((f for f in traverse["features"] if f["geometry"]["type"] == "LineString"), None)
    pts = [f for f in traverse["features"] if f["geometry"]["type"] == "Point"]
    feats = []
    if line is not None:
        pr = line["properties"]
        vx, vy = np.asarray(pr.get("x", []), float), np.asarray(pr.get("y", []), float)
        feats.append(line)
    else:
        vx = vy = np.array([])
    pts.sort(key=lambda f: f["properties"].get("sol", 0))
    lo = 0
    for f in pts:
        pr = dict(f["properties"])
        if len(vx):
            seg = np.hypot(vx[lo:] - pr["x"], vy[lo:] - pr["y"])
            j = lo + int(np.argmin(seg))
            pr["vertex_index"] = j
            lo = j
        feats.append({"type": "Feature", "geometry": f["geometry"], "properties": pr})
    md = dict(traverse.get("metadata", {}))
    md.update({"sol_first": min((f["properties"]["sol"] for f in pts), default=None),
               "sol_last": max((f["properties"]["sol"] for f in pts), default=None),
               "note": "Waypoints are in sol order; `vertex_index` indexes the LineString coordinates (and properties.x/y)."})
    return {"type": "FeatureCollection", "name": traverse.get("name"), "metadata": md, "features": feats}


# ---- router ------------------------------------------------------------------------------------------

def make_router(site, traverse: dict | None = None) -> APIRouter:
    router = APIRouter(tags=["overlay"])
    ctx = Ctx(site, traverse)
    cache: OrderedDict = OrderedDict()
    lock = threading.Lock()
    trav_cache: dict = {}

    def get(kind: str, request: Request):
        if kind not in BUILDERS:
            raise AtlasError(f'Unknown overlay kind "{kind}".', hint="Kinds: " + ", ".join(KINDS) + ". See /overlay/kinds.")
        fn, allowed = BUILDERS[kind]
        q = {k: v for k, v in request.query_params.items()}
        p = Params(q, allowed)
        # normalise params into a cache key by running the cheap validation first (builders read through Params)
        key_probe = _probe_key(kind, p)
        with lock:
            hit = cache.get(key_probe)
            if hit is not None:
                cache.move_to_end(key_probe)
                return hit
            img, meta = fn(ctx, Params(q, allowed))
            meta["bounds"] = ctx.bounds()
            meta["kind"] = kind
            out = (_encode(img), clean(meta))
            cache[key_probe] = out
            while len(cache) > CACHE_SIZE:
                cache.popitem(last=False)
            return out

    def _probe_key(kind, p):
        # key = kind + raw params (rounded) ; defaults resolved by builders are stable except sol (current sol) -> include it
        items = []
        for k, v in sorted(p.q.items()):
            try:
                items.append((k, round(float(v), 3)))
            except ValueError:
                items.append((k, v))
        extra = ()
        if kind == "sun" and "sol" not in p.q:
            extra = (("sol_now", S.mission_sol(S.parse_utc(None), site.lon0)),)
        return (kind, tuple(items), extra)

    @router.get("/overlay/kinds")
    def overlay_kinds():
        return clean({"kinds": _kinds(ctx), "image": {"format": "image/png RGBA", "max_px": MAX_PX, "px": ctx.px,
                                                      "half_m": site.half_m, "lat0": site.lat0, "lon0": site.lon0,
                                                      "orientation": "row 0 = north edge, col 0 = west edge; covers exactly the site square"}})

    @router.get("/overlay/{kind}.png")
    def overlay_png(kind: str, request: Request):
        png, meta = get(kind, request)
        return Response(png, media_type="image/png", headers={"Cache-Control": "public, max-age=300"})

    @router.get("/overlay/{kind}.json")
    def overlay_json(kind: str, request: Request):
        return get(kind, request)[1]

    @router.get("/traverse.geojson")
    def traverse_geojson():
        if "t" not in trav_cache:
            trav_cache["t"] = build_traverse(site, traverse)
        return Response(json.dumps(clean(trav_cache["t"])), media_type="application/geo+json")

    return router

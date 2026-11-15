"""Ancient-lake reconstruction: flood the PRESENT-DAY topography to a lake level.

flood_at_level(site, store, level_m | named_level, connected_to=None, check=None)

Water mask = elevation <= level AND 4-connected to a seed cell (flood fill). Default seed: the lowest known
cell of the site (the deepest part of the crater floor in the window). `connected_to` (location refs) replaces
the seed: only the basins containing those places are flooded. Isolated depressions that are not connected to
a seed are NOT flooded (they are listed as "below level but not connected"). No-data cells are never water.

Named levels are the published levels in docs/lake-levels.md (every value read in an opened source).
CAVEAT (always returned): modern topography is not the ancient lake floor (erosion, deposition, the delta
itself was built into the lake), so results are a present-day-topography approximation.
"""
from __future__ import annotations

import math

import numpy as np
from scipy import ndimage

from .errors import AtlasError
from .refs import resolve_ref
from .sets import SetStore
from .site import Site

_M24 = "Mangold et al., LPSC 2024 abstract 'Past variations of water level of Jezero paleolake'"
_V25 = "Villette et al., MTT 2025 abstract 'Modeling the hydrological evolution of Jezero's crater lake and its deltaic deposit'"

NAMED_LEVELS: dict[str, dict] = {
    "delta_top_2410": {"level_m": -2410.0, "kind": "measured (minimum water level)", "source": _M24,
                       "what": "uppermost delta foresets observed so far (north of Belva crater); highest delta-recorded lake level"},
    "delta_drying_start_2390": {"level_m": -2390.0, "kind": "modeled", "source": _V25,
                                "what": "upper end of the lake-level range (-2390 to -2500 m) over which the western delta was deposited in the model"},
    "eastern_breach_ca_2400": {"level_m": -2400.0, "kind": "approximate (ca.)", "source": _M24,
                               "what": "elevation of the eastern breach (outlet), 'ca. -2400 m'"},
    "carew_castle_2445": {"level_m": -2445.0, "kind": "measured", "source": _M24,
                          "what": "topset/foreset transition at Carew Castle (fan top)"},
    "pinestand_2448": {"level_m": -2448.0, "kind": "measured (minimum water level)", "source": _M24,
                       "what": "tops of the Pinestand Mountain foresets (topsets eroded)"},
    "fan_front_2470": {"level_m": -2470.0, "kind": "measured", "source": _M24,
                       "what": "upper end of the fan-front topset/foreset transitions (-2470 to -2500 m)"},
    "kodiak_2490": {"level_m": -2490.0, "kind": "measured", "source": _M24,
                    "what": "Kodiak butte topset/foreset level (-2490 and -2500 m quoted)"},
    "delta_low_2500": {"level_m": -2500.0, "kind": "measured", "source": _M24,
                       "what": "lowest delta level, at Eastern Cape and Kodiak"},
    "model_max_fill_2243": {"level_m": -2243.0, "kind": "modeled (not an observed shoreline)", "source": _V25,
                            "what": "lake elevation reached by the initial infilling (V = 450 km3) in the model"},
}

DATUM_NOTE = ("Datum: atlas elevations are above the MOLA areoid (HiRISE DTM, delta-geoid corrected). The cited papers "
              "print levels such as -2410 m without stating the datum in the text read; no offset is applied and a "
              "small offset is unverified. See docs/lake-levels.md.")
CAVEAT = ("PRESENT-DAY TOPOGRAPHY APPROXIMATION: the modern surface differs from the ancient lake floor (erosion, "
          "later deposition, and the western delta itself was built into the lake), so areas and depths are not "
          "ancient bathymetry.")
MAX_SHORELINE_MEMBERS = 30
MAX_LISTED = 40
MAX_CELLS_STORED = 2_000_000


def named_levels_text() -> str:
    """One-line list of the published named lake levels (metres, same datum as the elevation layer), for hints."""
    return "; ".join(f"{k} = {v['level_m']:g} m ({v['kind']})" for k, v in NAMED_LEVELS.items())


def _members_from_mask(site: Site, mask: np.ndarray, connectivity8: bool, limit: int, kind_attr: dict) -> tuple[list, int]:
    n = site.size_px
    lab, cnt = ndimage.label(mask, structure=np.ones((3, 3)) if connectivity8 else None)
    if cnt == 0:
        return [], 0
    sizes = np.bincount(lab.ravel())[1:]
    order = np.argsort(-sizes)[:limit]
    members = []
    for k in order:
        idx = np.flatnonzero(lab.ravel() == k + 1)
        r, c = idx // n, idx % n
        x, y = site.rc_to_xy(r, c)
        cx, cy = float(np.mean(x)), float(np.mean(y))
        j = int(np.argmin((x - cx) ** 2 + (y - cy) ** 2))          # member cell nearest the centroid
        lat, lon = site.xy_to_latlon(float(x[j]), float(y[j]))
        m = {"x": float(x[j]), "y": float(y[j]), "lat": round(lat, 5), "lon": round(lon, 5),
             "score": float(len(idx)), "area_m2": float(len(idx) * site.res_m ** 2),
             "bbox": [float(x.min()), float(y.min()), float(x.max()), float(y.max())],
             "attributes": {"cells": int(len(idx)), **kind_attr}}
        if len(idx) <= MAX_CELLS_STORED:
            m["_cells"] = idx.astype(np.int32)
        members.append(m)
    return members, cnt


def flood_at_level(site: Site, store: SetStore, level_m: float | None = None, named_level: str | None = None,
                   connected_to=None, check=None) -> dict:
    # ---- level ----
    """Flood the present-day DEM to a level (metres, elevation-layer datum): water = elevation <= level AND 4-connected to a seed.
    Give exactly one of `level_m` / `named_level`. `connected_to` (location refs) replaces the default seed (lowest known cell);
    `check` (refs) reports whether those places are underwater. Returns area (m2), depth stats (m), the water body and shoreline stored
    as region sets, plus CAVEAT/DATUM_NOTE. Raises AtlasError (hint) for a bad level, unknown name, or empty/unreachable seeds.
    """
    if (level_m is None) == (named_level is None):
        raise AtlasError("Give exactly one of level_m (metres, same datum as the elevation layer) or named_level.",
                         hint="Named levels: " + named_levels_text() + ".")
    if named_level is not None:
        if named_level not in NAMED_LEVELS:
            raise AtlasError(f'Unknown named level "{named_level}".', hint="Named levels: " + ", ".join(NAMED_LEVELS) + ".")
        info = dict(NAMED_LEVELS[named_level])
        level = float(info.pop("level_m"))
        level_info = {"named_level": named_level, **info}
    else:
        if isinstance(level_m, bool) or not isinstance(level_m, (int, float)) or not math.isfinite(level_m):
            raise AtlasError(f"level_m must be a finite number, got {level_m!r}.", hint="Use metres, e.g. -2500.")
        level = float(level_m)
        level_info = {"named_level": None, "kind": "user-specified", "source": "user-specified level"}

    z = site.layer("elevation").data
    known = np.isfinite(z)
    if not known.any():
        raise AtlasError("The elevation layer has no data.")
    n, res = site.size_px, site.res_m
    cell_area = res * res
    zmin, zmax = float(np.nanmin(z)), float(np.nanmax(z))
    below = known & (z <= level)

    notes: list[str] = []
    # ---- seeds ----
    seeds: list[dict] = []
    if connected_to is not None:
        pts = resolve_ref(site, connected_to, store)
        for p in pts:
            r, c = (int(v) for v in site.rc_index(p["x"], p["y"]))
            if 0 <= r < n and 0 <= c < n and below[r, c]:
                seeds.append({"x": float(p["x"]), "y": float(p["y"]), "r": r, "c": c, "id": p.get("id")})
        if not seeds:
            raise AtlasError(f"None of the connected_to locations is below {level:g} m, so there is no water there.",
                             hint=f"The site spans {zmin:.1f} to {zmax:.1f} m. Pick a lower place or a higher level.")
        if len(seeds) < len(pts):
            notes.append(f"{len(pts) - len(seeds)} connected_to location(s) are above the level and were ignored.")
        seed_desc = "connected_to locations"
    else:
        r0, c0 = (int(v) for v in np.unravel_index(int(np.nanargmin(np.where(known, z, np.inf))), z.shape))
        x0, y0 = site.rc_to_xy(r0, c0)
        if below[r0, c0]:
            seeds.append({"x": float(x0), "y": float(y0), "r": r0, "c": c0, "id": "lowest_cell"})
        seed_desc = "lowest known cell of the site (deepest part of the crater floor in this window)"

    if seeds:
        lab, _ = ndimage.label(below)                      # 4-connected
        keep = np.unique([lab[s["r"], s["c"]] for s in seeds])
        water = np.isin(lab, keep)
        del lab
    else:
        water = np.zeros_like(below)
        notes.append(f"Level {level:g} m is below the lowest known ground ({zmin:.1f} m): no water in this window.")

    nw = int(water.sum())
    isolated = below & ~water
    out: dict = {
        "level_m": level, "level": level_info, "seed": seed_desc,
        "seeds": [{k: s[k] for k in ("id", "x", "y")} for s in seeds[:5]],
        "site_elevation_range_m": [round(zmin, 2), round(zmax, 2)],
        "water_cells": nw, "area_m2": round(nw * cell_area, 1), "area_km2": round(nw * cell_area / 1e6, 4),
        "fraction_of_site": round(nw / float(n * n), 4),
        "isolated_below_level_cells": int(isolated.sum()),
        "isolated_below_level_area_m2": round(float(isolated.sum()) * cell_area, 1),
    }
    if nw:
        depth = level - z[water].astype(np.float64)
        di = int(np.argmax(depth))
        rr, cc = np.nonzero(water)
        dx, dy = site.rc_to_xy(rr[di], cc[di])
        out["depth_m"] = {"max": round(float(depth.max()), 2), "mean": round(float(depth.mean()), 2),
                          "median": round(float(np.median(depth)), 2)}
        out["volume_m3_in_window"] = round(float(depth.sum()) * cell_area, 1)
        out["deepest_point"] = {"x": float(dx), "y": float(dy), "elevation_m": round(float(z[rr[di], cc[di]]), 2)}
        edge = bool(water[0].any() or water[-1].any() or water[:, 0].any() or water[:, -1].any())
        out["touches_site_edge"] = edge
        if edge:
            notes.append("The water reaches the edge of the site window, so the lake continues beyond it: area and "
                         "volume are LOWER BOUNDS for this level.")
    else:
        out["depth_m"] = None
        out["touches_site_edge"] = False
    n_unknown = int((~known).sum())
    if n_unknown:
        notes.append(f"{n_unknown} no-data cell(s) in the window were never counted as water.")
    if out["isolated_below_level_cells"] and connected_to is None:
        notes.append(f"{out['isolated_below_level_cells']} cell(s) lie below the level but are not connected to the "
                     "lake body and were NOT flooded (pass connected_to to flood a specific basin).")

    # ---- shoreline (wet cells with a dry or unknown-free neighbour) and the water body as sets ----
    if nw:
        shore = water & ~ndimage.binary_erosion(water, border_value=1)       # site edge is not a shoreline
        shore &= ~ndimage.binary_dilation(~known)                            # next to no-data: not a real shore
        shore_members, n_shore = _members_from_mask(
            site, shore, True, MAX_SHORELINE_MEMBERS, {"level_m": level})
        sset = store.create("regions", "flood_at_level",
                            {"level_m": level, "named_level": named_level, "what": "shoreline"}, shore_members)
        ncells = int(shore.sum())
        out["shoreline"] = {"set_id": sset.set_id, "segments": n_shore, "listed": len(shore_members),
                            "cells": ncells, "approx_length_m": round(ncells * res, 1),
                            "note": "Shoreline = lake cells next to dry ground (the contour at the level), one region "
                                    "per connected segment, largest first; length is cells x cell size (approximate). "
                                    "Site-edge cells are excluded."}
        wm, _ = _members_from_mask(site, water, False, 1, {"level_m": level})
        for m in wm:                                   # representative point = deepest cell, not the centroid
            m["x"], m["y"] = out["deepest_point"]["x"], out["deepest_point"]["y"]
            m["attributes"]["max_depth_m"] = out["depth_m"]["max"]
        wset = store.create("regions", "flood_at_level",
                            {"level_m": level, "named_level": named_level, "what": "water body"}, wm)
        out["water_set_id"] = wset.set_id
    else:
        out["shoreline"] = None
        out["water_set_id"] = None

    # ---- what is underwater ----
    if check is not None:
        targets = resolve_ref(site, check, store)
        label = "locations given in `check`"
    else:
        targets = [{"id": f"poi:{p['id']}", "name": p.get("name"), "type": p.get("type"), "x": p["x"], "y": p["y"]}
                   for p in site.pois if p.get("x") is not None and p.get("y") is not None]
        label = "points of interest, sample sites and named regions"
    under, dry, disconnected, unknown = [], [], [], 0
    for t in targets:
        r, c = (int(v) for v in site.rc_index(t["x"], t["y"]))
        if not (0 <= r < n and 0 <= c < n) or not known[r, c]:
            unknown += 1
            continue
        e = float(z[r, c])
        row = {"id": t.get("id"), "name": t.get("name"), "type": t.get("type"), "x": round(float(t["x"]), 2),
               "y": round(float(t["y"]), 2), "elevation_m": round(e, 2)}
        if water[r, c]:
            row["depth_m"] = round(level - e, 2)
            under.append(row)
        elif e <= level:
            row["note"] = "below the level but not connected to the lake body"
            disconnected.append(row)
        else:
            row["height_above_level_m"] = round(e - level, 2)
            dry.append(row)
    under.sort(key=lambda d: -d["depth_m"])
    dry.sort(key=lambda d: d["height_above_level_m"])
    by_type: dict[str, int] = {}
    for u in under:
        by_type[str(u.get("type"))] = by_type.get(str(u.get("type")), 0) + 1
    out["underwater"] = {"checked": label, "count": len(under), "by_type": by_type, "listed": under[:MAX_LISTED],
                         "above_water_count": len(dry), "nearest_above_water": dry[:10],
                         "below_level_but_disconnected": disconnected[:10], "unknown_count": unknown}
    notes.extend([DATUM_NOTE, CAVEAT])
    out["notes"] = notes
    out["hint"] = ("Use the shoreline set with nearest(...) or set_view to show it; compare levels with named_level "
                   "(docs/lake-levels.md).")
    return out

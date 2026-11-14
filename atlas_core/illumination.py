"""Terrain illumination on the site DEM: sun angle at the site, incidence angle, cast and self shadows,
lit fractions and time-window search. Sun geometry comes from atlas_core.sun (Mars24).

Method (DEM = site layer "elevation", 2 m cells at Jezero; heights in metres):
  * Sun direction in the local frame (x east, y north, z up): s = (sin az cos el, cos az cos el, sin el),
    az clockwise from north. The site is small (about 8 km) so one sun direction (evaluated at the site centre
    for whole-site masks, at the target for point queries) is used for every cell.
  * Surface normal n = (-dz/dx, -dz/dy, 1)/norm from the Horn (1981) 3x3 gradient on the DEM (same operator as
    the slope_2m layer). Incidence angle i = arccos(n . s). Self-shadow (terrain faces away from the sun)
    is n . s <= 0.
  * Cast shadow: a cell is shadowed if terrain between it and the sun rises above the sight line to the sun-disc
    centre. Point and region queries march a ray toward the sun with bilinear height samples every res/2 m, up
    to where the sight line clears the highest terrain or leaves the site. Whole-site masks use a sweep: the DEM is
    resampled (bilinear) onto a grid whose columns run along the sun azimuth; the running "shadow plane" height
    h_j = max(z_j, h_{j-1} - tan(el) * res) is propagated away from the sun; a cell is in shadow when the plane
    arriving from upwind is above its ground. Azimuths that are multiples of 90 deg need no resampling (exact).

NOT modelled: terrain outside the site extent (shadows cast by distant rims onto the site are missed, and
cells near the sun-side edge are optimistic), no-data DEM cells (never cast shadows; cells in or next to them are
reported unknown), rocks and boulders below the DEM resolution, atmospheric scattering and twilight (Mars has
a long dusty twilight: "shadow" here means no direct sun, not darkness), the finite solar disc (penumbra of
a few cells at low sun), and Mars surface albedo or reflected light.
"""
from __future__ import annotations

import math
from datetime import timedelta

import numpy as np
from scipy import ndimage

from . import sun as S
from .errors import AtlasError
from .refs import resolve_ref
from .sets import SetStore
from .site import Site

LIT, CAST_SHADOW, SELF_SHADOW, UNKNOWN = 0, 1, 2, -1
CLASS_NAMES = {LIT: "lit", CAST_SHADOW: "cast_shadow", SELF_SHADOW: "self_shadow", UNKNOWN: "unknown"}
MAX_SAMPLE_CELLS = 400
EPS_M = 1e-4
LOW = -1.0e9
MODEL_NOTE = ("Geometric direct-sun model on the 2 m DEM: no atmospheric refraction, dust scattering or twilight "
              "(Mars twilight is long, so 'shadow' means no direct sun, not darkness), sun disc treated as a point, "
              "no terrain beyond the site edge, no rocks below DEM resolution.")


# ---- DEM helpers ------------------------------------------------------------------------------------

def _dem(site: Site) -> np.ndarray:
    return site.layer("elevation").data


def _filled(site: Site) -> np.ndarray:
    """DEM with no-data cells replaced by the grid minimum (they never block a ray)."""
    z = _dem(site)
    bad = ~np.isfinite(z)
    if not bad.any():
        return z.astype(np.float32, copy=False)
    out = z.copy()
    out[bad] = np.nanmin(z) - 1.0
    return out.astype(np.float32)


def sun_vector(el_deg: float, az_deg: float) -> np.ndarray:
    e, a = math.radians(el_deg), math.radians(az_deg)
    return np.array([math.sin(a) * math.cos(e), math.cos(a) * math.cos(e), math.sin(e)])


def _horn_gradients(z: np.ndarray, res: float):
    """(dz/dx east, dz/dy north) on the grid, Horn 3x3, edge-padded. NaN propagates."""
    p = np.pad(z.astype(np.float64), 1, mode="edge")
    a, b, c = p[:-2, :-2], p[:-2, 1:-1], p[:-2, 2:]
    d, f = p[1:-1, :-2], p[1:-1, 2:]
    g, h, i = p[2:, :-2], p[2:, 1:-1], p[2:, 2:]
    dzdx = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * res)
    dzdy = -((g + 2 * h + i) - (a + 2 * b + c)) / (8 * res)     # rows run south, so flip for north
    return dzdx, dzdy


def cos_incidence_grid(z: np.ndarray, res: float, s: np.ndarray) -> np.ndarray:
    gx, gy = _horn_gradients(z, res)
    return ((-gx * s[0] - gy * s[1] + s[2]) / np.sqrt(gx * gx + gy * gy + 1.0)).astype(np.float32)


def _cell_cos_incidence(dem: np.ndarray, rows: np.ndarray, cols: np.ndarray, res: float, s: np.ndarray):
    n = dem.shape[0]
    r0, r1 = np.clip(rows - 1, 0, n - 1), np.clip(rows + 1, 0, n - 1)
    c0, c1 = np.clip(cols - 1, 0, n - 1), np.clip(cols + 1, 0, n - 1)
    zz = lambda r, c: dem[r, c].astype(np.float64)
    a, b, c_ = zz(r0, c0), zz(r0, cols), zz(r0, c1)
    d, f = zz(rows, c0), zz(rows, c1)
    g, h, i = zz(r1, c0), zz(r1, cols), zz(r1, c1)
    gx = ((c_ + 2 * f + i) - (a + 2 * d + g)) / (8 * res)
    gy = -((g + 2 * h + i) - (a + 2 * b + c_)) / (8 * res)
    return (-gx * s[0] - gy * s[1] + s[2]) / np.sqrt(gx * gx + gy * gy + 1.0)


# ---- shadow casting: whole grid ---------------------------------------------------------------------

def _sweep(z: np.ndarray, drop: float) -> np.ndarray:
    """z[j, :] with the sun upwind at j=0 (sun on the low-j side). Returns the shadow-plane height arriving at
    each cell from upwind (so cell is in shadow when arrive > z)."""
    arrive = np.full_like(z, LOW)
    h = z[0].copy()
    for j in range(1, z.shape[0]):
        arrive[j] = h - drop
        h = np.maximum(z[j], arrive[j])
    return arrive


def cast_shadow_grid(z: np.ndarray, res: float, el_deg: float, az_deg: float) -> np.ndarray:
    """Boolean grid of cells shadowed by other terrain. `z` has no NaN (see _filled)."""
    if el_deg <= 0:
        return np.ones(z.shape, bool)
    drop = math.tan(math.radians(el_deg)) * res
    z = z.astype(np.float32)
    a = az_deg % 360.0
    # exact axis-aligned cases (sun toward west/east/north/south)
    if abs(a - 270.0) < 1e-6:                      # sun in the west: shadows run east (increasing col)
        return _sweep(np.ascontiguousarray(z.T), drop).T > z + EPS_M
    if abs(a - 90.0) < 1e-6:                       # sun in the east
        zt = np.ascontiguousarray(z[:, ::-1].T)
        return (_sweep(zt, drop).T[:, ::-1]) > z + EPS_M
    if abs(a - 180.0) < 1e-6:                      # sun in the south: shadows run north (decreasing row)
        zt = np.ascontiguousarray(z[::-1, :])
        return (_sweep(zt, drop)[::-1, :]) > z + EPS_M
    if abs(a) < 1e-6 or abs(a - 360.0) < 1e-6:     # sun in the north
        return _sweep(np.ascontiguousarray(z), drop) > z + EPS_M
    n = z.shape[0]
    ar = math.radians(a)
    dc, dr = -math.sin(ar), math.cos(ar)           # direction AWAY from the sun, in (col, row) units
    pc, pr = -dr, dc                               # perpendicular
    m = int(math.ceil(n * math.sqrt(2.0))) + 4
    o, c0 = (n - 1) / 2.0, (m - 1) / 2.0
    fwd = np.array([[dr, pr], [dc, pc]])           # (ju, iv) -> (row, col)
    off = np.array([o, o]) - fwd @ np.array([c0, c0])
    zr = ndimage.affine_transform(z, fwd, offset=off, output_shape=(m, m), order=1, mode="constant",
                                  cval=LOW, output=np.float32)
    arrive = _sweep(zr, drop)
    inv = np.array([[dr, dc], [pr, pc]])           # (row, col) -> (ju, iv)
    off2 = np.array([c0, c0]) - inv @ np.array([o, o])
    back = ndimage.affine_transform(np.maximum(arrive, LOW), inv, offset=off2, output_shape=(n, n), order=1,
                                    mode="constant", cval=LOW, output=np.float32)
    return back > z + EPS_M


# ---- shadow casting: ray march for points -----------------------------------------------------------

def points_lit(zfill: np.ndarray, res: float, rows: np.ndarray, cols: np.ndarray, el_deg: float, az_deg: float,
               chunk: int = 256) -> np.ndarray:
    """Boolean per cell centre: True if the sight line to the sun is not blocked by terrain inside the site."""
    rows = np.asarray(rows, float)
    cols = np.asarray(cols, float)
    if el_deg <= 0:
        return np.zeros(rows.shape, bool)
    tan_e = math.tan(math.radians(el_deg))
    ar = math.radians(az_deg)
    uc, ur = math.sin(ar), -math.cos(ar)            # toward the sun in (col, row) cell units
    n = zfill.shape[0]
    zmax = float(zfill.max())
    z0 = zfill[rows.astype(int), cols.astype(int)].astype(np.float64)
    step_m = res / 2.0
    out = np.ones(rows.shape, bool)
    for s0 in range(0, rows.size, chunk):
        sl = slice(s0, s0 + chunk)
        r0, c0, zz = rows[sl], cols[sl], z0[sl]
        dmax_m = np.minimum((zmax - zz) / tan_e, n * res * 1.5)      # beyond this the line is above all terrain
        kmax = int(np.ceil(dmax_m.max() / step_m)) if dmax_m.size else 0
        ok = np.ones(r0.shape, bool)
        for k0 in range(1, kmax + 1, 512):
            ks = np.arange(k0, min(k0 + 512, kmax + 1))
            d = ks * step_m
            rr = r0[:, None] + ur * d[None, :] / res
            cc = c0[:, None] + uc * d[None, :] / res
            inside = (rr >= 0) & (rr <= n - 1) & (cc >= 0) & (cc <= n - 1)
            if not inside.any():
                break
            h = ndimage.map_coordinates(zfill, [rr.ravel(), cc.ravel()], order=1, mode="nearest").reshape(rr.shape)
            line = zz[:, None] + d[None, :] * tan_e
            blocked = (h > line + EPS_M) & inside & (d[None, :] <= dmax_m[:, None])
            ok &= ~blocked.any(axis=1)
            if not ok.any():
                break
        out[sl] = ok
    return out


# ---- time specification -----------------------------------------------------------------------------

def resolve_time(utc=None, sol=None, ltst=None, lon_east: float = S.LANDING_LON_E):
    """-> datetime (UTC). Either `utc` (ISO string, datetime, "now"/None) or `sol` + `ltst` (hours or "HH:MM")."""
    if ltst is not None:
        h = parse_clock(ltst)
        if sol is None:
            sol = S.mission_sol(S.parse_utc(utc), lon_east)
        return S.utc_for_ltst(int(sol), h, lon_east)
    if sol is not None:
        raise AtlasError("A sol needs a local true solar time.", hint='Pass ltst, e.g. sol=1200, ltst="16:00".')
    return S.parse_utc(utc)


def parse_clock(v) -> float:
    if isinstance(v, bool):
        raise AtlasError(f"Cannot read local time {v!r}.")
    if isinstance(v, (int, float)):
        h = float(v)
    else:
        try:
            parts = str(v).strip().split(":")
            h = float(parts[0]) + (float(parts[1]) / 60 if len(parts) > 1 else 0) + (float(parts[2]) / 3600 if len(parts) > 2 else 0)
        except (ValueError, IndexError):
            raise AtlasError(f'Cannot read local time "{v}".', hint='Use "HH:MM" (0-24) or decimal hours.') from None
    if not 0 <= h <= 24:
        raise AtlasError(f"Local solar time must be within 0..24 hours, got {v!r}.")
    return h % 24.0


# ---- targets ----------------------------------------------------------------------------------------

def _target_cells(site: Site, store, target, radius_m: float | None) -> list[dict]:
    """-> list of {id, name, x, y, rows, cols, kind}: a point (or footprint disc) or a region member's cells."""
    n, res = site.size_px, site.res_m
    out = []
    for m in resolve_ref(site, target, store):
        cells = m.get("_cells")
        if cells is not None and len(cells):
            cells = np.asarray(cells)
            if len(cells) > MAX_SAMPLE_CELLS:
                cells = cells[np.linspace(0, len(cells) - 1, MAX_SAMPLE_CELLS).astype(int)]
            rows, cols = cells // n, cells % n
            kind = "region"
        elif radius_m and radius_m > res:
            k = int(math.ceil(radius_m / res))
            r0, c0 = (int(v) for v in site.rc_index(m["x"], m["y"]))
            rr, cc = np.mgrid[-k:k + 1, -k:k + 1]
            keep = (rr * rr + cc * cc) * res * res <= radius_m * radius_m
            rows, cols = (r0 + rr[keep]), (c0 + cc[keep])
            ok = (rows >= 0) & (rows < n) & (cols >= 0) & (cols < n)
            rows, cols = rows[ok], cols[ok]
            if len(rows) > MAX_SAMPLE_CELLS:
                sel = np.linspace(0, len(rows) - 1, MAX_SAMPLE_CELLS).astype(int)
                rows, cols = rows[sel], cols[sel]
            kind = f"footprint r={radius_m:g} m"
        else:
            r0, c0 = site.rc_index(m["x"], m["y"])
            rows, cols = np.array([int(r0)]), np.array([int(c0)])
            kind = "point"
        out.append({"id": m.get("id"), "name": m.get("name"), "x": m["x"], "y": m["y"], "lat": m.get("lat"),
                    "lon": m.get("lon"), "rows": rows.astype(int), "cols": cols.astype(int), "kind": kind})
    if not out:
        raise AtlasError("The target resolved to no locations.", hint="Pass a point, POI id or non-empty set.")
    return out


def _eval_cells(site: Site, zfill, rows, cols, sunpos: dict) -> dict:
    """Per-cell lit/incidence arrays for one sun position."""
    dem = _dem(site)
    el, az = sunpos["sun_elevation_deg"], sunpos["sun_azimuth_deg"]
    s = sun_vector(el, az)
    known = np.ones(rows.shape, bool)
    n = site.size_px
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            known &= np.isfinite(dem[np.clip(rows + dr, 0, n - 1), np.clip(cols + dc, 0, n - 1)])
    ci = _cell_cos_incidence(zfill, rows, cols, site.res_m, s)
    self_sh = ci <= 0
    lit_ray = points_lit(zfill, site.res_m, rows, cols, el, az)
    cls = np.where(~known, UNKNOWN, np.where(self_sh, SELF_SHADOW, np.where(~lit_ray, CAST_SHADOW, LIT)))
    inc = np.degrees(np.arccos(np.clip(ci, -1, 1)))
    return {"cls": cls, "incidence": inc, "known": known}


def _summarise(ev: dict, el: float, max_inc: float | None = None) -> dict:
    cls, inc, known = ev["cls"], ev["incidence"], ev["known"]
    nk = int(known.sum())
    d = {"cells_sampled": int(cls.size), "cells_unknown": int((~known).sum())}
    if nk == 0:
        d.update(lit_fraction=None, note="No DEM data at this location (unknown, not shadowed).")
        return d
    lit = cls == LIT
    d["lit_fraction"] = float(lit[known].mean())
    d["cast_shadow_fraction"] = float((cls == CAST_SHADOW)[known].mean())
    d["self_shadow_fraction"] = float((cls == SELF_SHADOW)[known].mean())
    d["incidence_deg_mean"] = float(inc[known].mean())
    d["incidence_deg_min"] = float(inc[known].min())
    d["incidence_deg_max"] = float(inc[known].max())
    if max_inc is not None:
        d["meets_incidence_fraction"] = float((lit & (inc <= max_inc))[known].mean())
    return d


# ---- public: illumination_at ------------------------------------------------------------------------

def illumination_at(site: Site, store: SetStore, target=None, utc=None, sol=None, ltst=None,
                    radius_m: float | None = None, make_set: bool = True, min_region_area_m2: float = 100.0) -> dict:
    """Sun angles at the site and illumination of `target` (point, footprint, region or set) at one instant.
    With no target, computes the whole-site lit/cast-shadow/self-shadow mask and stores it as a regions set."""
    t = resolve_time(utc, sol, ltst, S.LANDING_LON_E if site.lon0 is None else site.lon0)
    sp = S.sun_position(t, site.lat0, site.lon0)
    el, az = sp["sun_elevation_deg"], sp["sun_azimuth_deg"]
    res = {"time": {k: sp[k] for k in ("utc", "mission_sol", "ltst", "lmst", "ls_deg", "season")},
           "sun": {"elevation_deg": el, "azimuth_deg": az,
                   "azimuth_note": "clockwise from north (90 = east, 180 = south, 270 = west)"},
           "assumptions": [{"what": "geometric direct sun on 2 m DEM; " + MODEL_NOTE, "source": "assumed"}],
           "model_note": MODEL_NOTE}
    zfill = _filled(site)
    if target is not None:
        items = _target_cells(site, store, target, radius_m)
        if el <= 0:
            res["status"] = "sun_below_horizon"
            res["locations"] = [{"id": i["id"], "x": i["x"], "y": i["y"], "lit_fraction": 0.0, "kind": i["kind"]}
                                for i in items[:10]]
            res["lit_fraction"] = 0.0
            return res
        locs, allcls, allinc = [], [], []
        for it in items:
            ev = _eval_cells(site, zfill, it["rows"], it["cols"], sp)
            d = {"id": it["id"], "name": it["name"], "x": it["x"], "y": it["y"], "kind": it["kind"]}
            d.update(_summarise(ev, el))
            locs.append(d)
            allcls.append(ev["cls"][ev["known"]])
            allinc.append(ev["incidence"][ev["known"]])
        cl = np.concatenate(allcls) if allcls else np.array([])
        res["status"] = "day"
        res["locations"] = locs[:10]
        if len(locs) > 10:
            res["truncated"] = {"shown": 10, "total": len(locs)}
        res["lit_fraction"] = float((cl == LIT).mean()) if cl.size else None
        res["incidence_deg_mean"] = float(np.concatenate(allinc).mean()) if cl.size else None
        res["sampled_cells"] = int(cl.size)
        if len(locs) == 1 and locs[0].get("lit_fraction") is not None:
            f = locs[0]["lit_fraction"]
            res["verdict"] = ("lit" if f >= 0.999 else "in shadow" if f <= 0.001 else "partly shadowed")
        return res

    # whole site mask
    dem = _dem(site)
    unk = ~np.isfinite(dem)
    if el <= 0:
        res["status"] = "sun_below_horizon"
        res["lit_fraction"] = 0.0
        res["note"] = "The sun is below the geometric horizon at the site: no direct sunlight anywhere."
        return res
    s = sun_vector(el, az)
    cast = cast_shadow_grid(zfill, site.res_m, el, az)
    ci = cos_incidence_grid(zfill, site.res_m, s)
    cls = np.where(ci <= 0, SELF_SHADOW, np.where(cast, CAST_SHADOW, LIT)).astype(np.int8)
    cls[unk | ~np.isfinite(_horn_gradients(dem, site.res_m)[0])] = UNKNOWN
    known = cls != UNKNOWN
    cnt = {CLASS_NAMES[c]: int((cls == c).sum()) for c in (LIT, CAST_SHADOW, SELF_SHADOW, UNKNOWN)}
    res["status"] = "day"
    res["cells"] = cnt
    res["lit_fraction"] = float((cls == LIT).sum() / max(1, known.sum()))
    res["cast_shadow_fraction"] = float((cls == CAST_SHADOW).sum() / max(1, known.sum()))
    res["self_shadow_fraction"] = float((cls == SELF_SHADOW).sum() / max(1, known.sum()))
    res["incidence_deg_mean_lit"] = float(np.degrees(np.arccos(np.clip(ci[cls == LIT], -1, 1))).mean()) if cnt["lit"] else None
    if make_set:
        res.update(_mask_set(site, store, cls, min_region_area_m2, sp))
    return res


def _mask_set(site: Site, store: SetStore, cls: np.ndarray, min_area: float, sp: dict, per_class: int = 25) -> dict:
    n, res = site.size_px, site.res_m
    members = []
    for code in (SELF_SHADOW, CAST_SHADOW, LIT):
        labels, nl = ndimage.label(cls == code, structure=np.ones((3, 3), bool))
        if nl == 0:
            continue
        counts = np.bincount(labels.ravel(), minlength=nl + 1)
        ids = [i for i in np.argsort(-counts[1:]) + 1 if counts[i] * res * res >= min_area][:per_class]
        slices = ndimage.find_objects(labels)
        for i in ids:
            sl = slices[i - 1]
            reg = labels[sl] == i
            rows, cols = np.nonzero(reg)
            rows, cols = rows + sl[0].start, cols + sl[1].start
            cx, cy = site.rc_to_xy(rows, cols)
            ar_, ac_ = np.unravel_index(int(np.argmax(ndimage.distance_transform_edt(np.pad(reg, 1))[1:-1, 1:-1])), reg.shape)
            x, y = (float(v) for v in site.rc_to_xy(sl[0].start + ar_, sl[1].start + ac_))
            lat, lon = site.xy_to_latlon(x, y)
            members.append({"x": x, "y": y, "lat": round(lat, 5), "lon": round(lon, 5),
                            "score": float(counts[i] * res * res), "area_m2": float(counts[i] * res * res),
                            "bbox": [float(site.rc_to_xy(0, sl[1].start)[0]) - res / 2,
                                     float(site.rc_to_xy(sl[0].stop - 1, 0)[1]) - res / 2,
                                     float(site.rc_to_xy(0, sl[1].stop - 1)[0]) + res / 2,
                                     float(site.rc_to_xy(sl[0].start, 0)[1]) + res / 2],
                            "attributes": {"illumination_class": CLASS_NAMES[code],
                                           "centroid_x": float(cx.mean()), "centroid_y": float(cy.mean())},
                            "_cells": (rows * n + cols).astype(np.int32)})
    query = {"tool": "illumination_at", "utc": sp["utc"], "sun_elevation_deg": sp["sun_elevation_deg"],
             "sun_azimuth_deg": sp["sun_azimuth_deg"], "min_region_area_m2": min_area,
             "classes": CLASS_NAMES, "per_class_limit": per_class}
    s = store.create("regions", "illumination_at", query, members)
    return {"set_id": s.set_id, "set_count": s.count,
            "set_note": (f"Set holds up to {per_class} largest connected regions each of self_shadow, cast_shadow "
                         f"and lit (>= {min_area:g} m2); member attributes.illumination_class says which. "
                         "Use the set as a location in illumination_at or find_lit_windows.")}


# ---- public: time-window search ---------------------------------------------------------------------

def _passes(site, zfill, items, t, min_el, max_inc, min_frac, lat, lon):
    sp = S.sun_position(t, lat, lon)
    el = sp["sun_elevation_deg"]
    if el < min_el or el <= 0:
        return False, sp, 0.0
    tot = ok = 0
    for it in items:
        ev = _eval_cells(site, zfill, it["rows"], it["cols"], sp)
        good = (ev["cls"] == LIT) & ev["known"]
        if max_inc is not None:
            good &= ev["incidence"] <= max_inc
        k = ev["known"]
        tot += int(k.sum())
        ok += int(good.sum())
    frac = ok / tot if tot else 0.0
    return frac >= min_frac - 1e-9 and tot > 0, sp, frac


def find_lit_windows(site: Site, store: SetStore, target, utc_from=None, utc_to=None, sol=None, ltst_from=None,
                     ltst_to=None, step_min: float = 10.0, min_elevation_deg: float = 10.0,
                     max_incidence_deg: float | None = None, radius_m: float | None = None,
                     min_lit_fraction: float | None = None, max_steps: int = 3000) -> dict:
    """Windows between two times during which `target` is lit (direct sun, cast shadows included) with sun
    elevation >= min_elevation_deg and (optionally) incidence <= max_incidence_deg."""
    lon = site.lon0
    if sol is not None or ltst_from is not None or ltst_to is not None:
        if sol is None:
            sol = S.mission_sol(S.parse_utc(utc_from), lon)
        a = parse_clock(0 if ltst_from is None else ltst_from)
        b = parse_clock(24 if ltst_to is None else ltst_to) if ltst_to != 24 else 24.0
        t0 = S.utc_for_ltst(int(sol), a, lon)
        t1 = S.utc_for_ltst(int(sol), b % 24.0, lon)
        if t1 <= t0:
            t1 = S.utc_for_ltst(int(sol) + 1, b % 24.0, lon)
    else:
        t0 = S.parse_utc(utc_from)
        t1 = S.parse_utc(utc_to) if utc_to is not None else t0 + timedelta(seconds=S.SOL_SECONDS)
    if t1 <= t0:
        raise AtlasError("The end time must be after the start time.")
    if step_min <= 0:
        raise AtlasError("step_min must be > 0.")
    n_steps = int((t1 - t0).total_seconds() / 60.0 / step_min) + 1
    if n_steps > max_steps:
        raise AtlasError(f"{n_steps} time steps requested (limit {max_steps}).",
                         hint="Use a shorter span or a larger step_min (try 15 to 30).")
    if not 0 <= min_elevation_deg < 90:
        raise AtlasError("min_elevation_deg must be within 0..90.")
    items = _target_cells(site, store, target, radius_m)
    multi = sum(len(i["rows"]) for i in items) > 1
    if min_lit_fraction is None:
        min_lit_fraction = 0.9 if multi else 1.0
    zfill = _filled(site)
    lat = site.lat0

    def ok(t):
        return _passes(site, zfill, items, t, min_elevation_deg, max_incidence_deg, min_lit_fraction, lat, lon)

    times = [t0 + timedelta(minutes=step_min * k) for k in range(n_steps)]
    if times[-1] < t1:
        times.append(t1)
        n_steps += 1
    flags, fracs = [], []
    for t in times:
        p, sp, f = ok(t)
        flags.append(p)
        fracs.append(f)

    def refine(ta, tb):   # ta fails, tb passes (or vice versa): bisect to ~1 s
        pa = ok(ta)[0]
        for _ in range(9):
            tm = ta + (tb - ta) / 2
            if ok(tm)[0] == pa:
                ta = tm
            else:
                tb = tm
        return ta + (tb - ta) / 2

    windows = []
    i = 0
    while i < n_steps:
        if not flags[i]:
            i += 1
            continue
        j = i
        while j + 1 < n_steps and flags[j + 1]:
            j += 1
        start = times[i] if i == 0 else refine(times[i - 1], times[i])
        end = times[j] if j == n_steps - 1 else refine(times[j], times[j + 1])
        mid = start + (end - start) / 2
        wins = {"start_utc": start, "end_utc": end}
        sp_s, sp_e = S.sun_position(start, lat, lon), S.sun_position(end, lat, lon)
        sp_m = S.sun_position(mid, lat, lon)
        windows.append({
            "start_utc": sp_s["utc"], "end_utc": sp_e["utc"],
            "start_ltst": sp_s["ltst"], "end_ltst": sp_e["ltst"], "mission_sol": sp_s["mission_sol"],
            "duration_min": round((end - start).total_seconds() / 60.0, 1),
            "sun_elevation_deg_start": sp_s["sun_elevation_deg"], "sun_elevation_deg_end": sp_e["sun_elevation_deg"],
            "sun_elevation_deg_max": max(sp_s["sun_elevation_deg"], sp_e["sun_elevation_deg"], sp_m["sun_elevation_deg"]),
            "clipped_start": i == 0, "clipped_end": j == n_steps - 1})
        i = j + 1
    total = sum(w["duration_min"] for w in windows)
    sp0 = S.sun_position(t0, lat, lon)
    out = {"count": len(windows), "windows": windows, "total_lit_minutes": round(total, 1),
           "query": {"utc_from": sp0["utc"], "utc_to": S.sun_position(t1, lat, lon)["utc"],
                     "ltst_from": sp0["ltst"], "ltst_to": S.sun_position(t1, lat, lon)["ltst"],
                     "step_min": step_min, "min_elevation_deg": min_elevation_deg,
                     "max_incidence_deg": max_incidence_deg, "min_lit_fraction": min_lit_fraction,
                     "targets": [{"id": it["id"], "x": it["x"], "y": it["y"], "kind": it["kind"],
                                  "cells": int(len(it["rows"]))} for it in items[:10]]},
           "peak_lit_fraction": float(max(fracs)) if fracs else 0.0,
           "assumptions": [{"what": f"min_elevation_deg = {min_elevation_deg:g}", "source": "user-specified or default"},
                           {"what": f"min_lit_fraction = {min_lit_fraction:g}", "source": "user-specified or default"},
                           {"what": MODEL_NOTE, "source": "assumed"}],
           "model_note": MODEL_NOTE}
    if not windows:
        out["hint"] = (f"No lit time at these limits (best lit fraction {out['peak_lit_fraction']:.2f}). Relax "
                       "min_elevation_deg, max_incidence_deg or min_lit_fraction, or widen the time range.")
    else:
        out["hint"] = ("Window edges are bisected to about 1 s; start/end LTST is local TRUE solar time at the site "
                       "centre. Call illumination_at for the sun angle and incidence at a given time.")
    return out

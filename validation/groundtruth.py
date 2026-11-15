"""Independent ground-truth checks for Ares Atlas (checks 1-4 and 6 data side). Read-only on the site and raw data.
Run: .venv/bin/python validation/groundtruth.py   -> prints JSON, writes validation/groundtruth_results.json
"""
import json, math, sys
from pathlib import Path
import numpy as np
import rasterio

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "data/build/site/jezero"
RAW = ROOT / "data/raw/science"
sys.path.insert(0, str(ROOT))
from atlas_core.site import load_site  # noqa: E402

site = load_site(SITE)
R = site.radius_m


def samp(layer, x, y, bilinear=False):
    a = site.layers[layer].data
    if bilinear:
        r, c = site.xy_to_rc(x, y)
        r0, c0 = int(np.floor(r)), int(np.floor(c))
        if r0 < 0 or c0 < 0 or r0 + 1 >= a.shape[0] or c0 + 1 >= a.shape[1]:
            return float("nan")
        fr, fc = r - r0, c - c0
        return float(a[r0, c0] * (1 - fr) * (1 - fc) + a[r0, c0 + 1] * (1 - fr) * fc + a[r0 + 1, c0] * fr * (1 - fc) + a[r0 + 1, c0 + 1] * fr * fc)
    r, c = site.rc_index(x, y)
    r, c = int(r), int(c)
    if r < 0 or c < 0 or r >= a.shape[0] or c >= a.shape[1]:
        return None
    return a[r, c]


def footprint(layer, x, y, rad, reducer):
    a = site.layers[layer].data
    r, c = site.rc_index(x, y)
    n = int(math.ceil(rad / site.res_m))
    rr, cc = np.mgrid[-n:n + 1, -n:n + 1]
    m = (rr ** 2 + cc ** 2) * site.res_m ** 2 <= rad ** 2
    R0, C0 = int(r) + rr[m], int(c) + cc[m]
    ok = (R0 >= 0) & (C0 >= 0) & (R0 < a.shape[0]) & (C0 < a.shape[1])
    v = a[R0[ok], C0[ok]].astype(float)
    v = v[np.isfinite(v)]
    return float(reducer(v)) if len(v) else float("nan")


def stats(x, y):
    x, y = np.asarray(x), np.asarray(y)
    res = y - x
    k, b = np.polyfit(x, y, 1)
    return {"n": int(len(x)), "r": float(np.corrcoef(x, y)[0, 1]), "bias_deg": float(res.mean()),
            "rmse_deg": float(math.sqrt((res ** 2).mean())), "fit_rover_on_dem": [float(k), float(b)]}


def check1():
    wp = json.loads((RAW / "mmgis/M20_waypoints.json").read_text())["features"]
    rows = []
    for f in wp:
        p, (lon, lat, *_) = f["properties"], f["geometry"]["coordinates"]
        t = p.get("tilt")
        if t is None or t < -9000 or p.get("Note") == "Site increment, no motion.":
            continue
        x, y = site.latlon_to_xy(lat, lon)
        if not site.in_bounds(x, y):
            continue
        rows.append((x, y, float(t), p["sol"]))
    out = {"n_waypoints_in_site": len(rows)}
    rov = np.array([r[2] for r in rows])
    for name, rad, red in [("point", 0, None), ("3m_mean", 3, np.mean), ("3m_max", 3, np.max), ("5m_mean", 5, np.mean), ("5m_max", 5, np.max)]:
        v = np.array([samp("slope_2m", r[0], r[1], True) if rad == 0 else footprint("slope_2m", r[0], r[1], rad, red) for r in rows])
        ok = np.isfinite(v)
        s = stats(v[ok], rov[ok])
        # stats(dem, rover): residual = rover - dem; flip to dem - rover for comparability with 09_validate
        s["bias_deg"] = -s["bias_deg"]
        s["mean_dem"] = float(v[ok].mean()); s["mean_rover"] = float(rov[ok].mean())
        # spearman
        from scipy.stats import spearmanr
        s["spearman"] = float(spearmanr(v[ok], rov[ok])[0])
        out[name] = s
    # robustness: restrict to rover tilt < 8 deg (drops the few steep stops that dominate r), 5 m mean
    v = np.array([footprint("slope_2m", r[0], r[1], 5, np.mean) for r in rows]); k = np.isfinite(v) & (rov < 8)
    out["5m_mean_tilt_lt_8deg"] = {"n": int(k.sum()), "r": float(np.corrcoef(v[k], rov[k])[0, 1]),
                                   "rmse_deg": float(math.sqrt(((v[k] - rov[k]) ** 2).mean()))}
    out["share_stops_tilt_ge_10deg"] = float((rov >= 10).mean())
    out["n_stops_tilt_ge_10deg"] = int((rov >= 10).sum())
    return out


def check6():
    """Our eva_walkable speed curve (atlas_core/travel.py: flat * exp(-3.5 tan(slope)), flat = profiles.yaml 0.9 m/s)."""
    flat = 0.9
    return {f"{d} deg": {"m_per_s": round(flat * math.exp(-3.5 * math.tan(math.radians(d))), 3),
                         "km_per_h": round(3.6 * flat * math.exp(-3.5 * math.tan(math.radians(d))), 2)} for d in (0, 4, 8, 15, 20, 25)}


def contains_unit(x, y):
    v = samp("geologic_unit", x, y)
    return None if v is None else int(v)


def poi_list():
    return [f["properties"] for f in json.loads((SITE / "pois.geojson").read_text())["features"] if f["properties"]["type"] == "sample_site"]


def check2():
    layer = site.layers["geologic_unit"]
    cls = layer.classes
    reg = layer.meta["registration"]
    dx, dy = reg["applied_shift_east_m"], reg["applied_shift_north_m"]
    rows = []
    for p in poi_list():
        x, y = p["x"], p["y"]
        after = contains_unit(x, y)
        # polygons were moved by (dx,dy): unshifted polygon at (x,y) is the raster at (x+dx, y+dy)
        before = contains_unit(x + dx, y + dy)
        dc = footprint("dist_to_unit_contact_m", x, y, 2, np.min)
        # is the unit uniform across the position-uncertainty disc (after shift)?
        n = int(min(p["position_uncertainty_m"], 150) / site.res_m)
        rr, cc = np.mgrid[-n:n + 1, -n:n + 1]
        mk = np.hypot(rr, cc) * site.res_m <= min(p["position_uncertainty_m"], 150)
        rA, cA = site.rc_index(x, y); a = layer.data
        uni = len(np.unique(a[int(rA) + rr[mk], int(cA) + cc[mk]]))
        rows.append({"dist_to_contact_m": dc, "units_in_unc_disc": int(uni), "id": p["id"], "sample": p["id"].split("_")[1], "region": p["region"], "rock_type": p["rock_type"],
                     "unc_m": p["position_uncertainty_m"], "after": cls.get(after), "before": cls.get(before)})
    return rows


def check3():
    """Orbital CRISM tier at the in-situ sample sites, with the confirmed_in_situ override REMOVED: re-decode the Parente map
    with sitebuild.science (same decoding as the build) and read tiers at the point, and max/min within 36 m and 72 m."""
    from sitebuild import science as S
    cls, sf, black, white, foot = S.parente_classes()
    ev = S.build_evidence(cls, sf, black, white)
    names = {0: "not_detected", 2: "weak_orbital", 3: "strong_orbital", -1: "no_coverage"}
    out = []
    for p in poi_list():
        for m in p["in_situ_minerals"]:
            t = ev[m]
            res = {"id": p["id"], "mineral": m, "in_situ_tier": "confirmed_in_situ (rover statement, unit/section level)"}
            for rad in (0, 36, 72):
                if rad == 0:
                    v = [int(samp_arr(t, p["x"], p["y"]))]
                else:
                    a = t; r, c = site.rc_index(p["x"], p["y"]); r, c = int(r), int(c)
                    n = int(rad / site.res_m)
                    rr, cc = np.mgrid[-n:n + 1, -n:n + 1]
                    mk = np.hypot(rr, cc) * site.res_m <= rad
                    v = list(np.unique(a[r + rr[mk], c + cc[mk]]).astype(int))
                res[f"orbital_within_{rad}m"] = [names.get(x, str(x)) for x in sorted(v)]
            cl = int(samp_arr(cls, p["x"], p["y"]))
            res["decoded_prototype_at_point"] = S.PROTOS[cl][0] if cl >= 0 else None
            out.append(res)
    return out


def samp_arr(a, x, y):
    r, c = site.rc_index(x, y)
    return a[int(r), int(c)]


def check4(level=-2410.0):
    out = []
    for p in poi_list():
        z = samp("elevation", p["x"], p["y"], True)
        out.append({"id": p["id"], "region": p["region"], "z_site": float(z), "z_poi": p["z"], "unit": site.layers["geologic_unit"].classes.get(contains_unit(p["x"], p["y"])),
                    "rel_m": float(z - level)})
    return out


if __name__ == "__main__":
    res = {"check1": check1(), "check2": check2(), "check3": check3(), "check4": check4(), "check6": check6()}
    (ROOT / "validation/groundtruth_results.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res["check1"], indent=1))

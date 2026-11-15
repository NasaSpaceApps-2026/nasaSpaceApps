"""Check 5: line of sight from the landing site (Mastcam-Z, camera ~2 m above surface per mastcamz.asu.edu tech-specs) to the
delta remnant / scarp / Kodiak sectors named in NASA releases. Run: .venv/bin/python validation/los_cases.py"""
import json, math, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import groundtruth as g
from atlas_core.visibility import line_of_sight

site = g.site
LX, LY = 1844.26, -432.71  # landing POI
U = site.layers["geologic_unit"]
unit_of = {v: k for k, v in U.classes.items()}
rng = np.random.default_rng(0)


def sector_cells(az_c, half_az, r0, r1, unit=None):
    """Cells in sector (azimuth deg clockwise from N) range r0..r1 m from the landing site, optionally in a unit."""
    n = site.size_px
    rr, cc = np.mgrid[0:n:2, 0:n:2]
    x, y = site.rc_to_xy(rr, cc)
    dx, dy = x - LX, y - LY
    d = np.hypot(dx, dy)
    az = (np.degrees(np.arctan2(dx, dy)) + 360) % 360
    da = np.abs((az - az_c + 180) % 360 - 180)
    m = (d >= r0) & (d <= r1) & (da <= half_az) & np.isfinite(g.site.layers["elevation"].data[rr, cc])
    if unit is not None:
        m &= U.data[rr, cc] == unit_of[unit]
    return x[m], y[m]


def vis_share(xs, ys, eye=2.0, tgt=0.0, k=250):
    if len(xs) == 0:
        return None
    idx = rng.choice(len(xs), min(k, len(xs)), replace=False)
    ok = 0
    for i in idx:
        r = line_of_sight(site, {"x": LX, "y": LY}, {"x": float(xs[i]), "y": float(ys[i])}, eye, tgt)
        ok += bool(r["clear"])
    return ok / len(idx), len(xs)


cases = {
    "A delta remnant W ~2.3 km (PIA24485, Mastcam-Z 22 Feb 2021, from landing site)": (270, 25, 2100, 2500),
    "B delta scarp NW ~2.2 km (NASA 7 Oct 2021 release)": (315, 25, 2000, 2400),
    "C Kodiak SW ~2.2 km (same release)": (225, 25, 2000, 2400),
}
if __name__ == "__main__":
    out = {}
    for name, (az, ha, r0, r1) in cases.items():
        res = {}
        for label, unit in (("NHjf2_cells", "Jezero fan 2 unit (NHjf2)"), ("all_cells_same_sector", None), ("Njf_floor_cells_same_sector", "Jezero floor unit (Njf)")):
            xs, ys = sector_cells(az, ha, r0, r1, unit)
            r = {}
            for tgt in (0.0, 5.0, 20.0):
                v = vis_share(xs, ys, 2.0, tgt)
                r[f"target_{tgt:g}m"] = None if v is None else {"visible_share": round(v[0], 3), "cells_in_sector(step2)": v[1]}
            res[label] = r
        # eye height sensitivity for the fan cells
        xs, ys = sector_cells(az, ha, r0, r1, "Jezero fan 2 unit (NHjf2)")
        res["NHjf2_eye_sensitivity_tgt0"] = {f"eye_{e}": (None if len(xs) == 0 else round(vis_share(xs, ys, e, 0.0)[0], 3)) for e in (0.5, 1.7, 2.0, 2.12)}
        out[name] = res
    print(json.dumps(out, indent=1))
    Path(__file__).with_name("los_results.json").write_text(json.dumps(out, indent=1))
    # extra: where the fan-unit cells in the SW half-plane actually are (bearing/range) and their visibility
    xs, ys = sector_cells(225, 45, 1000, 4000, "Jezero fan 2 unit (NHjf2)")
    d = np.hypot(xs - LX, ys - LY); az = (np.degrees(np.arctan2(xs - LX, ys - LY)) + 360) % 360
    ex = {"n_fan_cells_step2": int(len(xs)), "bearing_min_max": [float(az.min()), float(az.max())],
          "range_min_max_m": [float(d.min()), float(d.max())]}
    for tgt in (0.0, 5.0):
        v = vis_share(xs, ys, 2.0, tgt, k=400)
        ex[f"visible_share_tgt{tgt:g}"] = v[0]
    print(json.dumps(ex)); (Path(__file__).with_name("los_results.json")).write_text(json.dumps({"cases": out, "sw_fan_extra": ex}, indent=1))

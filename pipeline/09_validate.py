"""Scientific validation of the site model against REAL Perseverance data. Regenerates docs/science-validation.md.

Checks (all against NASA/JPL rover data, not against our own derived layers):
  1. Elevation: DEM height at each real waypoint vs the rover-reported elevation.  [frame/alignment check;
     NOT independent: the rover's elevations are tied to this HiRISE DTM]
  2. Tilt: rover-measured total tilt (IMU) vs tilt of a plane fitted to our DEM over a 3 m footprint.
     [independent: IMU vs orbital stereo terrain]
  3. Hazard agreement: share of the real traverse length per slope class on our slope raster.
Run: .venv/bin/python pipeline/09_validate.py
"""
import datetime
import json
import math
import sys
from pathlib import Path

import numpy as np
import rasterio

sys.path.insert(0, str(Path(__file__).parent))
from site_cfg import LAT0, LON0, R, SIZE  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RAW, B = ROOT / "data" / "raw", ROOT / "data" / "build"
CAUTION, NOGO, ASTRO_NOGO = 15.0, 20.0, 25.0

with rasterio.open(B / "analysis" / "dem.tif") as d:
    DEM2, RES2 = d.read(1).astype(np.float64), d.res[0]
with rasterio.open(B / "analysis" / "slope.tif") as d:
    SLOPE2 = d.read(1).astype(np.float64)
HALF = DEM2.shape[0] * RES2 / 2
core = None
cj = B / "web" / "dem1m_core.json"
if cj.exists():
    m = json.loads(cj.read_text())
    core = (m, np.fromfile(B / "web" / "dem1m_core.u16", dtype="<u2").reshape(m["height"], m["width"]).astype(np.float64) * m["step"] + m["base"])


def bilin(a, cf, rf):
    h, w = a.shape
    if cf < 0 or rf < 0 or cf > w - 1 or rf > h - 1:
        return float("nan")
    c0, r0 = int(cf), int(rf)
    c1, r1 = min(c0 + 1, w - 1), min(r0 + 1, h - 1)
    fc, fr = cf - c0, rf - r0
    return (a[r0, c0] * (1 - fc) + a[r0, c1] * fc) * (1 - fr) + (a[r1, c0] * (1 - fc) + a[r1, c1] * fc) * fr


def z(x, y):
    """Best available DEM height: 1 m core grid where it exists, else the 2 m grid. Pixel centres."""
    if core:
        m, a = core
        v = bilin(a, (x - m["x0"]) / m["res_m"] - 0.5, (m["y_top"] - y) / m["res_m"] - 0.5)
        if not math.isnan(v):
            return v
    return bilin(DEM2, (x + HALF) / RES2 - 0.5, (HALF - y) / RES2 - 0.5)


def to_xy(lon, lat):
    return R * math.cos(math.radians(LAT0)) * math.radians(lon - LON0), R * math.radians(lat - LAT0)


def plane_tilt(x, y, half=1.5):
    """Tilt (deg) of a least-squares plane over a (2*half)^2 footprint, 9 samples."""
    pts = [(x + i * half, y + j * half) for i in (-1, 0, 1) for j in (-1, 0, 1)]
    zs = np.array([z(px, py) for px, py in pts])
    if np.isnan(zs).any():
        return float("nan")
    A = np.c_[[p[0] - x for p in pts], [p[1] - y for p in pts], np.ones(len(pts))]
    gx, gy, _ = np.linalg.lstsq(A, zs, rcond=None)[0]
    return math.degrees(math.atan(math.hypot(gx, gy)))


wp = json.loads((RAW / "m20_waypoints.json").read_text())["features"]
tv = json.loads((RAW / "m20_traverse.json").read_text())["features"]

# ---- 1 + 2: per-waypoint
dz, dem_tilt, rov_tilt, sols = [], [], [], []
for f in wp:
    p, (lon, lat, *_) = f["properties"], f["geometry"]["coordinates"]
    x, y = to_xy(lon, lat)
    if abs(x) > SIZE / 2 - 20 or abs(y) > SIZE / 2 - 20:
        continue
    e, t = p.get("elev_geoid"), p.get("tilt")
    if e is not None and e > -9000:
        dz.append(z(x, y) - e)
    if t is not None and t > -9000 and p.get("Note") != "Site increment, no motion.":
        tt = plane_tilt(x, y)
        if not math.isnan(tt):
            dem_tilt.append(tt); rov_tilt.append(float(t)); sols.append(p["sol"])
dz, dem_tilt, rov_tilt = np.array(dz), np.array(dem_tilt), np.array(rov_tilt)
resid = dem_tilt - rov_tilt
r = float(np.corrcoef(dem_tilt, rov_tilt)[0, 1]) if len(dem_tilt) > 2 else float("nan")
# regression of rover tilt on DEM tilt
slope_fit, icpt = np.polyfit(dem_tilt, rov_tilt, 1)

# ---- 3: hazard agreement along the real traverse (2 m slope raster)
lens = np.zeros(4)  # <15, 15-20, 20-25, >=25
for f in tv:
    g = f["geometry"]
    for line in (g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]):
        xy = [to_xy(c[0], c[1]) for c in line]
        for (x0, y0), (x1, y1) in zip(xy[:-1], xy[1:]):
            n = max(1, int(math.hypot(x1 - x0, y1 - y0) / 2))
            for k in range(n):
                px, py = x0 + (x1 - x0) * (k + .5) / n, y0 + (y1 - y0) * (k + .5) / n
                s = bilin(SLOPE2, (px + HALF) / RES2 - 0.5, (HALF - py) / RES2 - 0.5)
                if math.isnan(s):
                    continue
                seg = math.hypot(x1 - x0, y1 - y0) / n
                lens[0 if s < CAUTION else 1 if s < NOGO else 2 if s < ASTRO_NOGO else 3] += seg
tot = lens.sum()
share = lens / tot * 100

res = {"generated": datetime.datetime.now().isoformat(timespec="seconds"),
       "elevation": {"n": int(len(dz)), "mean_m": float(dz.mean()), "sd_m": float(dz.std()), "p95_abs_m": float(np.percentile(abs(dz), 95))},
       "tilt": {"n": int(len(dem_tilt)), "pearson_r": r, "bias_deg": float(resid.mean()), "rmse_deg": float(math.sqrt((resid ** 2).mean())),
                "mean_rover_tilt_deg": float(rov_tilt.mean()), "mean_dem_tilt_deg": float(dem_tilt.mean()),
                "max_rover_tilt_deg": float(rov_tilt.max()), "fit_rover_vs_dem": [float(slope_fit), float(icpt)]},
       "hazard": {"traverse_m": float(tot), "pct_lt15": float(share[0]), "pct_15_20": float(share[1]), "pct_20_25": float(share[2]), "pct_ge25": float(share[3])}}
ctxp = B / "ctx" / "stats.json"
ctx = json.loads(ctxp.read_text()) if ctxp.exists() else None
res["ctx"] = ctx
(ROOT / "docs").mkdir(exist_ok=True)
(ROOT / "docs" / "science-validation.json").write_text(json.dumps(res, indent=2))

ctx_rows = ""
if ctx:
    t, bf = ctx["terrain_ctx_minus_hirise_m"], ctx["brightness_fit_hirise_vs_ctx"]
    ctx_rows = f"""### Context block (NASA CTX around the HiRISE site)

| # | Check | Result | What it does and does not show |
|---|---|---|---|
| 4 | CTX 20 m DTM vs HiRISE 1 m DTM over the site (HiRISE block-averaged to 16 m) | mean {t['mean']:+.2f} m, sd {t['sd']:.2f} m, 95% within {t['p95_abs']:.1f} m, correlation {t['corr']:.4f} | Two different NASA/USGS stereo products agree on terrain to a few metres. The offset and spread are what you would expect from a 20 m product against a 1 m one; it is the size of the vertical step at the seam between them. |
| 5 | CTX vs HiRISE brightness over the site (3.5 m grid), n = {bf['n_px']:,} px | correlation {bf['corr']:.2f}; fitted HiRISE = {bf['gain']:.2f} x CTX + {bf['offset']:.0f} | CTX brightness was linearly matched to HiRISE so the two imagery layers join without a tone jump. Cosmetic matching, not radiometric calibration; the two cameras and sun angles differ. |
| 6 | Context data gaps | DTM {ctx['ctx_dem_nan_share']*100:.2f}% and ortho {ctx['ortho_nan_share']*100:.2f}% of the block lacked data (edges); the DTM gaps were nearest-neighbour filled | Filled DTM cells are not flagged as unknown in the viewer yet. |
"""
md = f"""# Science validation log

Auto-generated by `pipeline/09_validate.py` on {res['generated']}. Re-run after any data or pipeline change. Every number below is
computed from NASA/JPL Mars 2020 rover data against our site model (USGS HiRISE DTM, 1-2 m). No number here is hand-entered.

## Results

| # | Check | Result | What it does and does not show |
|---|---|---|---|
| 1 | DEM height vs rover-reported elevation, {res['elevation']['n']} real waypoints | mean {res['elevation']['mean_m']:+.2f} m, sd {res['elevation']['sd_m']:.2f} m, 95% within {res['elevation']['p95_abs_m']:.2f} m | Confirms our coordinate frame and alignment (lat/lon to x/y to DEM pixel). **Not independent accuracy:** the rover's elevations are tied to this same HiRISE DTM, so agreement is expected. |
| 2 | Rover IMU tilt vs DEM plane-fit tilt (3 m footprint), {res['tilt']['n']} drive stops | r = {res['tilt']['pearson_r']:.2f}; bias {res['tilt']['bias_deg']:+.2f} deg; RMSE {res['tilt']['rmse_deg']:.2f} deg; mean rover {res['tilt']['mean_rover_tilt_deg']:.1f} deg vs DEM {res['tilt']['mean_dem_tilt_deg']:.1f} deg; max rover tilt {res['tilt']['max_rover_tilt_deg']:.1f} deg | **Independent check** of slope from orbital stereo against the rover's own inclinometer. A 3 m plane fit smooths rocks, wheel-scale relief and rover suspension, so a gap is expected; the correlation is the meaningful number. |
| 3 | Real traverse length by slope class on our 2 m slope raster, {res['hazard']['traverse_m'] / 1000:.1f} km | <15 deg {res['hazard']['pct_lt15']:.1f}%; 15-20 deg {res['hazard']['pct_15_20']:.1f}%; 20-25 deg {res['hazard']['pct_20_25']:.1f}%; >=25 deg {res['hazard']['pct_ge25']:.1f}% | The rover is *avoiding* steep ground by design (its route is planned around hazards), so a high safe share supports the hazard map but is not proof of it. Slope is at 2 m scale. |

{ctx_rows}

## Assumptions register (anything not measured from NASA data)

| Assumption | Where | Status |
|---|---|---|
| Rover slope limits: caution 15 deg, no-go 20 deg | api/profiles.yaml, web rover physics | **Assumed.** Perseverance's real limits are not public here; check #2 shows the rover's max measured tilt was {res['tilt']['max_rover_tilt_deg']:.1f} deg. |
| Astronaut walking limits 15/25 deg, speed curve | api/profiles.yaml | **Assumed**, Earth-derived hiking function used as a placeholder. |
| Display speed factor x200 | rover physics | Display choice only, not science. |
| Overall surface hue of the colourised imagery | pipeline/colorize.py ANCHOR | **Assumed** typical Mars tone. Brightness/detail are real HiRISE; only relative colour variation comes from Viking. Marked false colour in the manifest. |
| Imagery below 1 m outside the 25 cm core | pipeline/00_fetch.py | Real HiRISE averaged to 1 m (not 25 cm) outside the core block. |
| Terrain below 1 m | viewer | No data exists; the viewer interpolates. Rocks and small relief are **not** modelled. |
| Site vertical datum | site_cfg.py | Heights are MOLA areoid-referenced as given by USGS ("DeltaGeoid"); we draw them on a sphere of radius 3,396,190 m. Cesium Mars uses its own ellipsoid, so the seam against Cesium's surroundings may have a vertical offset. **Unmeasured.** |

## Known gaps in validation

- No independent vertical-accuracy test of the HiRISE DTM itself (would need e.g. lander-frame ground truth or published DTM error studies; USGS publishes a vertical precision for HiRISE DTMs, which we have not yet cited).
- Hazard thresholds are not validated against rover capability.
- Route planner and rover simulator are not yet validated against real drives (PRD "route realism" check).
"""
(ROOT / "docs" / "science-validation.md").write_text(md)
print(md.split("## Assumptions")[0].split("## Results")[1])

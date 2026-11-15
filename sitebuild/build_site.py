#!/usr/bin/env python
"""Build data/build/site/jezero/ : the site directory read by atlas_core.load_site.

Architecture: offline build step at the start of the data path (raw NASA/USGS products -> sitebuild ->
data/build/site/<name>/ -> atlas_core.load_site -> devapi / MCP / agent).
Inputs: HiRISE DTM (1 m, Fergason et al. 2020) with a CTX DTM (20 m) fill, windowed and cached under data/raw/
(reruns do not refetch); the shared frame (centre, size, CRS) from pipeline/site_cfg.py.
Outputs: elevation, slope_2m, rough_5x5 and quality_flag GeoTIFFs on the 2 m grid (x east / y north metres, row 0
north), site.yaml (catalogue entries), profiles.yaml (values marked "assumed" where no citation exists), pois.geojson
and QC figures; sitebuild.science and sitebuild.geomorph then add the science layers.
Method: reprojection to the site frame, slope by atlas_core.terrain.slope_deg (Horn 1981), roughness as the 5x5
standard deviation, seam/noise detectors (seams.py, noise.py) that flag cells but never alter elevations.

Run:  .venv/bin/python -m sitebuild.build_site   (or python sitebuild/build_site.py)
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import rasterio
import yaml
from rasterio.enums import Resampling
from rasterio.warp import reproject
from scipy import ndimage

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "sitebuild"))
sys.path.insert(0, str(ROOT / "pipeline"))   # site_cfg.py (shared frame, read-only)
import os
os.chdir(ROOT)

from atlas_core.site import MARS_RADIUS_M
from atlas_core.terrain import roughness, slope_deg, bilinear
from fetch_hirise import get_ctx, get_hirise
from seams import detect_seams
from noise import detect_noise, BLOCK as NOISE_BLOCK
from site_cfg import LAT0, LON0, SIZE
from site_frame import site_crs, site_transform

NAME = "jezero"
RES = 2.0
N = int(round(SIZE / RES))
HALF = N * RES / 2
LANDING = (18.4447, 77.4508)
OUT = ROOT / "data/build/site/jezero"

HI_SRC = {
    "mission": "MRO",
    "instrument": "HiRISE (stereo DTM, MOLA-registered)",
    "product": "Mars 2020 TRN HiRISE DTM Mosaic (JEZ_hirise_soc_006, 1 m, blend40)",
    "citation": "Fergason et al. 2020, USGS Astrogeology, doi:10.5066/P9REJ9JN",
    "url": "https://astrogeology.usgs.gov/search/map/mars_2020_terrain_relative_navigation_hirise_dtm_mosaic",
}
CTX_SRC = {
    "mission": "MRO",
    "instrument": "CTX (stereo DTM, MOLA-registered)",
    "product": "Mars 2020 TRN CTX DTM Mosaic (JEZ_ctx_B_soc_008, 20 m)",
    "citation": "Fergason et al. 2020, USGS Astrogeology, doi:10.5066/P9REJ9JN",
    "url": "https://astrogeology.usgs.gov/search/map/mars_2020_terrain_relative_navigation_ctx_dtm_mosaic",
}
QUALITY_CLASSES = {0: "hirise_dtm", 1: "ctx_fill", 2: "hirise_seam_suspect", 3: "hirise_noisy"}


def warp(src, src_tr, src_crs, resampling, dst_nodata=np.nan, src_nodata=np.nan):
    """Reproject a source raster onto the site grid (N x N, RES m, site CRS); cells with no source data stay NaN."""
    dst = np.full((N, N), np.nan, np.float32)
    reproject(src, dst, src_transform=src_tr, src_crs=src_crs, src_nodata=src_nodata,
              dst_transform=site_transform(N, RES), dst_crs=site_crs(),
              dst_nodata=dst_nodata, resampling=resampling, num_threads=4)
    return dst


def hillshade(z, res, az=315, alt=35):
    """Lambertian hillshade (0-1) of a DEM for QC figures only; `az`/`alt` are sun azimuth and altitude in degrees, `res` the cell size in metres."""
    zz = np.where(np.isfinite(z), z, np.nanmean(z))
    gy, gx = np.gradient(zz, res)
    sl = np.arctan(np.hypot(gx, gy)); asp = np.arctan2(-gx, gy)
    a, e = np.radians(az), np.radians(alt)
    return np.clip(np.sin(e) * np.cos(sl) + np.cos(e) * np.sin(sl) * np.cos(a - asp), 0, 1)


def write_tif(path, arr, dtype, nodata):
    """Write one single-band, tiled, deflate-compressed GeoTIFF on the site grid."""
    with rasterio.open(path, "w", driver="GTiff", width=N, height=N, count=1, dtype=dtype,
                       crs=site_crs(), transform=site_transform(N, RES),
                       nodata=nodata, compress="deflate", tiled=True, blockxsize=256, blockysize=256) as d:
        d.write(arr.astype(dtype), 1)


def main():
    """Build the whole site directory (layers, catalogue, profiles, POIs, figures, report); prints a build report to stdout."""
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    print("fetching/reading HiRISE window ..."); sys.stdout.flush()
    hi, hi_tr, hi_crs = get_hirise(LAT0, LON0, HALF)
    ctx, ctx_tr, ctx_crs = get_ctx(LAT0, LON0, HALF)
    print(f"  HiRISE window {hi.shape}, {np.isnan(hi).mean():.2%} nodata  ({time.time()-t0:.0f}s)")

    # --- seam detection in the SOURCE grid (1 m), before any resampling --------------
    seam_rows, seam_cells, seam_info = detect_seams(hi)
    print(f"  seam detector: {seam_info}")

    # --- noisy-DTM zones in the SOURCE grid (see sitebuild/noise.py) ---------------------
    noise_blk, noise_z, noise_info = detect_noise(hi)
    print(f"  noise detector: {noise_info}")
    noise_src = np.zeros(hi.shape, np.float32)
    nb_r, nb_c = noise_blk.shape
    noise_src[:nb_r * NOISE_BLOCK, :nb_c * NOISE_BLOCK] = np.kron(noise_blk, np.ones((NOISE_BLOCK, NOISE_BLOCK)))

    # --- elevation --------------------------------------------------------------------
    # 1 m -> 2 m with Resampling.average (area-weighted mean over the 2 m footprint, valid
    # source pixels only). Averaging is the physically right aggregation for a height field:
    # it keeps mean elevation and low-pass filters the 1 m stereo noise before the Horn
    # slope is taken at 2 m, instead of aliasing it (nearest/bilinear/cubic sample points
    # and do not integrate over the cell). A 2 m cell is accepted as HiRISE only if >=99.9%
    # of its footprint is valid source data, so edge cells never mix in nodata silently.
    hi_valid_src = np.isfinite(hi).astype(np.float32)
    hi2 = warp(hi, hi_tr, hi_crs, Resampling.average)
    frac = warp(hi_valid_src, hi_tr, hi_crs, Resampling.average, src_nodata=None)
    frac = np.nan_to_num(frac, nan=0.0)
    hi_ok = np.isfinite(hi2) & (frac >= 0.999)
    hi2[~hi_ok] = np.nan
    ctx2 = warp(ctx, ctx_tr, ctx_crs, Resampling.bilinear)   # 20 m -> 2 m: smooth interpolation
    both = hi_ok & np.isfinite(ctx2)
    dz = hi2[both] - ctx2[both]
    print(f"  HiRISE-CTX elevation difference: median {np.median(dz):+.2f} m, "
          f"RMS {np.sqrt(np.mean(dz**2)):.2f} m (no offset applied)")
    elev = np.where(hi_ok, hi2, ctx2).astype(np.float32)
    fill = ~hi_ok & np.isfinite(ctx2)

    # --- seam mask on the 2 m grid -----------------------------------------------------
    seam_f = warp(seam_cells.astype(np.float32), hi_tr, hi_crs, Resampling.average, src_nodata=None)
    seam2 = np.nan_to_num(seam_f, nan=0.0) > 0.0     # any overlap with a flagged source cell

    noise2 = np.nan_to_num(warp(noise_src, hi_tr, hi_crs, Resampling.average, src_nodata=None), nan=0.0) > 0.5

    # --- derived layers ----------------------------------------------------------------
    slope = slope_deg(elev, RES)
    rough = roughness(elev)

    # quality_flag: a cell is class 0 only if every elevation in its 5x5 window (the support of
    # both the Horn 3x3 slope and the 5x5 roughness) is clean HiRISE data.
    k = np.ones((5, 5), bool)
    near_fill = ndimage.binary_dilation(fill, k)
    near_seam = ndimage.binary_dilation(seam2 & hi_ok, k)
    q = np.zeros((N, N), np.int16)
    q[near_seam] = 2
    q[noise2 & hi_ok] = 3        # noisy-texture zone outranks a seam suspect (broader, more consequential)
    q[near_fill] = 1
    q[~np.isfinite(elev)] = -1

    # --- write rasters -----------------------------------------------------------------
    write_tif(OUT / "elevation.tif", elev, "float32", np.nan)
    write_tif(OUT / "slope_2m.tif", slope, "float32", np.nan)
    write_tif(OUT / "rough_5x5.tif", rough, "float32", np.nan)
    write_tif(OUT / "quality_flag.tif", q, "int16", -1)

    # --- catalogue ---------------------------------------------------------------------
    hi_frac = float(hi_ok.mean())
    qual_note = ("Cells are class 0 only if every elevation in their 5x5 window is HiRISE data "
                 "without suspected seam artefacts; see quality_flag. Values near CTX fill or seam "
                 "suspects are kept (never altered) but flagged.")
    layers = [
        dict(id="elevation", kind="continuous", file="elevation.tif", title="Elevation (MOLA-referenced)",
             units="m", resolution_m=RES,
             measures="Surface elevation above the MOLA areoid (delta-geoid corrected), 1 m HiRISE stereo DTM averaged to 2 m; CTX 20 m DTM (bilinear) where HiRISE has no data.",
             does_not_measure="Rocks, boulders and ripples smaller than a few metres; vertical accuracy of the stereo DTM is not independently verified here. Absolute height is tied to MOLA.",
             quality="HiRISE cells are as published (not altered). CTX-filled cells are much smoother (20 m native) and may differ from HiRISE by metres; no offset is applied. Elevations in suspected seam rows are unmodified. " + qual_note,
             coverage="site", source=HI_SRC | {"also_used": CTX_SRC}),
        dict(id="slope_2m", kind="continuous", file="slope_2m.tif", title="Slope over 2 m grid (Horn)",
             units="degrees", resolution_m=RES,
             measures="Surface slope from the 2 m elevation, Horn 3x3 finite difference (about 6 m support).",
             does_not_measure="Slopes of features smaller than about 4-6 m (individual rocks, small ripples); direction of slope.",
             quality="NaN where any of 3x3 neighbours is unknown. Computed on CTX-filled or seam-suspect elevation where quality_flag != hirise_dtm; those values are retained but unreliable. " + qual_note,
             coverage="site", source=HI_SRC | {"also_used": CTX_SRC}),
        dict(id="rough_5x5", kind="continuous", file="rough_5x5.tif", title="Roughness (5x5 elevation std)",
             units="m", resolution_m=RES,
             measures="Standard deviation of elevation in a 5x5 cell (10 m) window, no plane removal, so on tilted ground it includes the slope contribution.",
             does_not_measure="Rock abundance or grain-scale texture; sub-2 m roughness.",
             quality="NaN where any window cell is unknown. CTX-filled windows are artificially smooth (low roughness is NOT evidence of smooth ground there). " + qual_note,
             coverage="site", source=HI_SRC | {"also_used": CTX_SRC}),
        dict(id="quality_flag", kind="categorical", file="quality_flag.tif", title="Elevation data quality",
             units=None, resolution_m=RES,
             measures="Where the derived terrain layers rest on HiRISE DTM data, CTX fill, or HiRISE data with suspected seam artefacts (5x5 window support).",
             does_not_measure="Vertical accuracy; unflagged HiRISE cells can still contain stereo errors the detector cannot see.",
             quality=("Seam detector is heuristic and conservative (flags suspects, never edits data). -1 = no data from either source. "
                      "hirise_noisy (3) marks contiguous zones (>= 4 ha) where the HiRISE DTM has markedly higher cell-to-cell "
                      "high-frequency elevation energy (median |Laplacian| per 16 m block, robust z > 3 seeded / > 1.5 grown after smoothing) than the rest of "
                      "this site: stereo-matching noise that inflates slope_2m and rough_5x5 without the ground being steeper. "
                      "Elevations there are unaltered; the detector is relative to this site, cannot separate noise from genuinely rough terrain, "
                      "and zone edges are only about 16-50 m accurate. Precedence where flags overlap: ctx_fill > hirise_noisy > hirise_seam_suspect."),
             coverage="site", source={"mission": "Ares Atlas pipeline", "instrument": "derived", "product": "sitebuild/build_site.py, sitebuild/seams.py, sitebuild/noise.py",
                                      "citation": "derived from Fergason et al. 2020 (doi:10.5066/P9REJ9JN)", "url": HI_SRC["url"]},
             classes=QUALITY_CLASSES),
    ]
    cfg = dict(name=NAME, lat0=LAT0, lon0=LON0, radius_m=MARS_RADIUS_M, res_m=RES, size_px=N,
               crs=site_crs(), layers=layers)
    (OUT / "site.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))
    (OUT / "profiles.yaml").write_text(yaml.safe_dump(profiles(), sort_keys=False))

    # --- POIs --------------------------------------------------------------------------
    from atlas_core import load_site
    site = load_site(OUT)
    x, y = site.latlon_to_xy(*LANDING)
    r, c = site.xy_to_rc(x, y)
    z = bilinear(site.layer("elevation").data, float(r), float(c))
    ri, ci = site.rc_index(x, y)
    landing_q = int(site.layer("quality_flag").data[ri, ci])
    common = dict(x=round(float(x), 2), y=round(float(y), 2), z=round(z, 2), lat=LANDING[0], lon=LANDING[1],
                  mission="Mars 2020", source_url="https://science.nasa.gov/mission/mars-2020-perseverance/")
    pois = [
        dict(id="octavia_e_butler_landing", name="Octavia E. Butler Landing", type="landmark", **common,
             description="Perseverance touchdown site, 18.4447 N, 77.4508 E (planetocentric lat, east lon), as published for Mars 2020. Position here is the published coordinate, not re-measured from imagery."),
        dict(id="base", name="Mission base (assumed)", type="base", **common,
             description="ASSUMED mission base placed at the Perseverance landing site for planning queries. Not a real facility."),
    ]
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]}, "properties": p}
        for p in pois]}
    (OUT / "pois.geojson").write_text(json.dumps(fc, indent=1))

    # --- INTERIM science layers + points (traverse, samples, geologic units, CRISM evidence) ---
    import science
    science.run(OUT)

    # --- terrain-science layers (aspect, curvature, TPI, TRI, relief, outcrop exposure, distances, diversity) ---
    import geomorph
    geomorph.run(OUT)

    # --- reports + figures -------------------------------------------------------------
    s = slope[np.isfinite(slope)]
    print("\n== REPORT ==")
    print(f"centre {LAT0} N {LON0} E; HiRISE valid fraction {hi_frac:.4f}")
    print(f"landing site x={x:.1f} y={y:.1f} in grid={site.in_bounds(x, y)} quality_flag={landing_q} elev={z:.2f} m")
    print(f"elevation range {np.nanmin(elev):.1f} .. {np.nanmax(elev):.1f} m (HiRISE-only: {np.nanmin(hi2):.1f} .. {np.nanmax(hi2):.1f})")
    print("slope p50/p95/p99/max: " + " / ".join(f"{v:.2f}" for v in (*np.percentile(s, [50, 95, 99]), s.max())))
    for code, nm in [(0, "hirise_dtm"), (1, "ctx_fill"), (2, "hirise_seam_suspect"), (3, "hirise_noisy"), (-1, "no_data")]:
        print(f"  quality {code:2d} {nm:20s} {np.mean(q == code):.4%}")
    figures(site, elev, slope, q, hi, seam_rows, seam2, x, y)
    noise_figure(elev, slope, rough, q, noise_z, noise_blk, hi.shape)
    zn = q == 3
    if zn.any():
        print(f"  noisy zone: median slope {np.nanmedian(slope[zn]):.2f} deg vs {np.nanmedian(slope[q == 0]):.2f} deg on clean HiRISE; "
              f"median rough {np.nanmedian(rough[zn]):.3f} vs {np.nanmedian(rough[q == 0]):.3f} m")
        ys, xs = np.where(zn)
        print(f"  noisy zone x range {-HALF + xs.min()*RES:.0f}..{-HALF + xs.max()*RES:.0f} m, y range {HALF - ys.max()*RES:.0f}..{HALF - ys.min()*RES:.0f} m; "
              f"{zn.sum()*RES*RES/1e4:.1f} ha, {(xs*RES - HALF < -2750).mean():.1%} of its cells at new x < -2750 m (= old x < -2300)")
    print(f"done in {time.time()-t0:.0f}s")


def profiles():
    """profiles.yaml content: traveller/landing profiles. Every value here is source "assumed" (team assumption pending a citation); the query engine echoes that label in its results."""
    A = "assumed"
    return {
        "landing_pad": {
            "description": "Touchdown pad for a crewed lander. Pad scale, not a full landing ellipse. All thresholds are team assumptions pending cited values.",
            "footprint_radius_m": {"value": 50, "source": A},
            "conditions": [
                {"layer": "slope_2m", "op": "<=", "value": 10, "source": A},
                {"layer": "rough_5x5", "op": "<=", "value": 0.5, "source": A},
                {"layer": "quality_flag", "op": "in", "value": ["hirise_dtm"], "source": "assumed (pads must not rest on CTX-filled or seam-suspect data)"},
            ],
        },
        "eva_walkable": {
            "description": "Astronaut on foot (PRD traveller profile astronaut_walk). Speeds and limits are PRD placeholders, not measured suit data.",
            "travel": {
                "flat_speed_mps": {"value": 0.9, "source": A},
                "caution_slope_deg": {"value": 15, "source": A},
                "max_slope_deg": {"value": 25, "source": A},
                "speed_curve": "tobler_placeholder",
                "speed_curve_note": "Earth-derived hiking function speed = flat*exp(-k*|tan(slope)+c|); placeholder shape, not Mars/suit data.",
                "eva_limit_h": {"value": 8, "source": A},
                "reserve_margin": {"value": 0.2, "source": A},
            },
            "conditions": [{"layer": "slope_2m", "op": "<=", "value": 25, "source": A}],
        },
        "rover_scout": {
            "description": "Small scouting rover, speed of the order of Perseverance's top speed (assumed, not verified against a NASA fact sheet here). Display may scale speed up.",
            "travel": {
                "flat_speed_mps": {"value": 0.04, "source": A},
                "caution_slope_deg": {"value": 15, "source": A},
                "max_slope_deg": {"value": 20, "source": A},
                "speed_curve": "tobler_placeholder",
            },
            "conditions": [{"layer": "slope_2m", "op": "<=", "value": 20, "source": A}],
        },
    }


def noise_figure(elev, slope, rough, q, noise_z, noise_blk, src_shape):
    """docs/noise_check.png: where the detector fires, and side-by-side 1 km crops at equal
    display scale (inside the zone vs just outside) so the texture difference can be judged by eye."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ext = (-HALF, HALF, -HALF, HALF)
    hs = hillshade(elev, RES)
    zn = q == 3
    fig = plt.figure(figsize=(20, 13))
    a0 = fig.add_subplot(2, 3, 1); a0.imshow(hs, cmap="gray", extent=ext)
    a0.contour(np.linspace(-HALF, HALF, N), np.linspace(HALF, -HALF, N), zn, [0.5], colors="m", linewidths=1.2)
    a0.set_title("hillshade + hirise_noisy boundary (magenta)")
    a1 = fig.add_subplot(2, 3, 2)
    im = a1.imshow(noise_z, cmap="magma", vmin=-2, vmax=6, extent=(0, 1, 0, 1)); a1.set_title("smoothed robust z of block high-frequency energy (source window)")
    plt.colorbar(im, ax=a1, fraction=.046)
    a2 = fig.add_subplot(2, 3, 3); im = a2.imshow(slope, cmap="RdYlGn_r", vmin=0, vmax=30, extent=ext)
    a2.contour(np.linspace(-HALF, HALF, N), np.linspace(HALF, -HALF, N), zn, [0.5], colors="k", linewidths=1)
    a2.set_title("slope 2 m (deg) + zone")
    # crops: 1 km square centred on the densest in-zone and a clean point at similar slope scale
    ys, xs = np.where(zn)
    if len(ys):
        sm = ndimage.uniform_filter(zn.astype(np.float32), 500)
        r1, c1 = np.unravel_index(np.argmax(sm), sm.shape)
        cl = ndimage.uniform_filter((q == 0).astype(np.float32), 500)
        cl[:, :N // 3] = 0                                    # clean reference taken from the eastern 2/3
        r2, c2 = np.unravel_index(np.argmax(cl), cl.shape)
        for k, (r, c, ttl) in enumerate([(r1, c1, "INSIDE noisy zone"), (r2, c2, "clean HiRISE reference")]):
            r = int(np.clip(r, 250, N - 250)); c = int(np.clip(c, 250, N - 250))
            sl = (slice(r - 250, r + 250), slice(c - 250, c + 250))
            a = fig.add_subplot(2, 3, 4 + k); a.imshow(hs[sl], cmap="gray", vmin=0, vmax=1)
            a.set_title(f"{ttl}: 1 km, centre x={-HALF + c*RES:.0f} y={HALF - r*RES:.0f}\nmedian slope {np.nanmedian(slope[sl]):.1f} deg, rough {np.nanmedian(rough[sl]):.2f} m", fontsize=10)
        a5 = fig.add_subplot(2, 3, 6)
        b = np.linspace(0, 30, 61)
        a5.hist(slope[q == 0], b, density=True, alpha=.6, label="clean HiRISE (class 0)", color="#2e8b57")
        a5.hist(slope[zn], b, density=True, alpha=.6, label="hirise_noisy (class 3)", color="#7a3fb5")
        a5.set_xlabel("slope 2 m (deg)"); a5.legend(); a5.set_title("slope distributions (terrain differs too)", fontsize=10)
    plt.tight_layout(); plt.savefig(ROOT / "docs/noise_check.png", dpi=60); plt.close()


def figures(site, elev, slope, q, hi, seam_rows, seam2, lx, ly):
    """Write the QC figures (hillshade, quality flags, seams, noise) under docs/."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap
    ext = (-HALF, HALF, -HALF, HALF)
    hs = hillshade(elev, RES)
    fig, ax = plt.subplots(1, 3, figsize=(21, 7.4))
    ax[0].imshow(hs, cmap="gray", extent=ext); ax[0].set_title("Hillshade (az 315, alt 35), north up")
    im = ax[1].imshow(slope, cmap="RdYlGn_r", vmin=0, vmax=30, extent=ext); ax[1].set_title("Slope 2 m (deg)")
    plt.colorbar(im, ax=ax[1], fraction=.046)
    cm = ListedColormap(["#000000", "#2e8b57", "#e0a030", "#d03030", "#7a3fb5"])
    ax[2].imshow(q, cmap=cm, vmin=-1, vmax=3, extent=ext, interpolation="nearest")
    ax[2].set_title("quality_flag: green HiRISE, amber CTX, red seam, purple noisy, black none")
    for a in ax:
        a.plot(lx, ly, "c+", ms=18, mew=2.5); a.annotate("landing", (lx, ly), (lx + 150, ly + 150), color="c", fontsize=11)
        a.set_xlabel("x east (m)"); a.set_ylabel("y north (m)")
    plt.tight_layout(); (ROOT / "docs").mkdir(exist_ok=True); plt.savefig(ROOT / "docs/site_overview.png", dpi=70); plt.close()

    # seam check: full hillshade with flagged cells in red, plus zoom on the densest flagged area
    flagged = seam2 & (q != -1)
    rgb = np.dstack([hs, hs, hs])
    rgb[flagged] = [1, 0, 0]
    fig, ax = plt.subplots(1, 2, figsize=(20, 10))
    ax[0].imshow(rgb, extent=ext); ax[0].set_title(f"HiRISE seam-suspect cells (red) on hillshade; {flagged.sum()} cells, {len(seam_rows)} source rows")
    rows_with = np.where(flagged.sum(1) > 0)[0]
    if len(rows_with):
        cnt = ndimage.uniform_filter1d(flagged.sum(1).astype(float), 400)
        r0 = int(np.clip(np.argmax(cnt), 300, N - 300))
    else:
        r0 = N // 2
    sl = slice(r0 - 300, r0 + 300); cs = slice(N // 2 - 300, N // 2 + 300)
    ax[1].imshow(rgb[sl, cs], extent=(-300 * RES, 300 * RES, HALF - (r0 + 300) * RES, HALF - (r0 - 300) * RES))
    ax[1].set_title(f"zoom: 1.2 km crop, rows {r0-300}..{r0+300}")
    plt.tight_layout(); plt.savefig(ROOT / "docs/seam_check.png", dpi=60); plt.close()


if __name__ == "__main__":
    main()

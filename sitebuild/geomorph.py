"""Terrain-science (geomorphometry) layers for the Ares Atlas query engine.

Run by build_site.py after science.run() (or alone:  .venv/bin/python -m sitebuild.geomorph). Reads the built site
directory (elevation, slope_2m, geologic_unit, the 7 CRISM evidence layers, traverse.geojson), writes one GeoTIFF per
layer next to them, replaces its own entries in site.yaml (idempotent) and writes docs/geomorph_check.png.

All raster maths lives in atlas_core/terrain.py (so tests can run it on analytic surfaces). Layers (2 m grid):

  aspect_deg, aspect_class      Horn (1981) downslope direction; undefined (NaN / 'flat') below 1 deg slope
  curvature_profile, _plan      Zevenbergen and Thorne (1987) on a 5 m-sigma Gaussian-smoothed DEM
  tpi_50m                       Weiss (2001) topographic position index, 50 m disc
  tri                           Riley et al. (1999) terrain ruggedness index, 3x3
  local_relief_50m, _200m       max - min elevation within a disc
  outcrop_exposure              local_relief_50m where >= 2 % of the 50 m disc is steeper than 25 deg, else 0
  dist_to_traverse_m            distance to the Perseverance traverse line
  dist_to_unit_contact_m        distance to the nearest mapped contact between two geologic units
  unit_diversity_100m           number of distinct geologic units within 100 m
  mineral_diversity_100m        number of distinct CRISM minerals (tier >= weak_orbital) within 100 m
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import rasterio
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "sitebuild"))
sys.path.insert(0, str(ROOT / "pipeline"))

from atlas_core import terrain as T                      # noqa: E402
from atlas_core.site import TIERS                          # noqa: E402
from site_frame import site_crs, site_transform            # noqa: E402

OUT = ROOT / "data/build/site/jezero"
MIN_ASPECT_SLOPE = 1.0          # deg: below this aspect is undefined
STEEP_DEG = 25.0                # outcrop_exposure steepness threshold
STEEP_FRAC = 0.02               # ... applied to this fraction of the 50 m disc
SMOOTH_SIGMA_M = 5.0
EVIDENCE = ["carbonate", "olivine", "fe_mg_smectite", "high_ca_pyroxene", "low_ca_pyroxene", "kaolinite", "mg_smectite_2"]

CIT_HORN = "Horn, B.K.P. (1981) Hill shading and the reflectance map, Proc. IEEE 69(1):14-47, doi:10.1109/PROC.1981.11918"
CIT_ZT = "Zevenbergen, L.W. and Thorne, C.R. (1987) Quantitative analysis of land surface topography, Earth Surf. Process. Landforms 12:47-56, doi:10.1002/esp.3290120107"
CIT_WEISS = "Weiss, A.D. (2001) Topographic position and landforms analysis, poster, ESRI International User Conference, San Diego"
CIT_RILEY = "Riley, S.J., DeGloria, S.D. and Elliot, R. (1999) A terrain ruggedness index that quantifies topographic heterogeneity, Intermountain J. Sciences 5(1-4):23-27"
CIT_EVANS = "Evans, I.S. (1972) General geomorphometry, derivatives of altitude, and descriptive statistics, in Chorley (ed.) Spatial Analysis in Geomorphology, Methuen"
CIT_EDT = "Maurer, C.R., Qi, R. and Raghavan, V. (2003) A linear time algorithm for computing exact Euclidean distance transforms, IEEE TPAMI 25(2):265-270 (scipy.ndimage.distance_transform_edt)"

HI_URL = "https://astrogeology.usgs.gov/search/map/mars_2020_terrain_relative_navigation_hirise_dtm_mosaic"
DTM_NOTE = (" Inherits every limitation of the elevation layer: where quality_flag is not hirise_dtm (CTX fill, seam suspect, noisy DTM) "
            "the value is retained but unreliable (CTX fill is far smoother than reality; noisy zones inflate ruggedness).")


def _src(citation, product="sitebuild/geomorph.py (atlas_core/terrain.py)"):
    return {"mission": "Ares Atlas pipeline", "instrument": "derived", "product": product,
            "citation": citation + "; applied to Fergason et al. 2020 (doi:10.5066/P9REJ9JN)", "url": HI_URL}


def write_tif(path, arr, dtype, nodata, n, res):
    with rasterio.open(path, "w", driver="GTiff", width=n, height=n, count=1, dtype=dtype, crs=site_crs(),
                       transform=site_transform(n, res), nodata=nodata, compress="deflate", tiled=True,
                       blockxsize=256, blockysize=256) as d:
        d.write(arr.astype(dtype), 1)


def line_distance(xs, ys, n: int, res: float) -> np.ndarray:
    """Distance (m) from each cell centre to a polyline given in site-frame metres. The polyline is rasterised
    (all cells it touches) on a canvas enlarged to include parts of the line that lie OUTSIDE the grid, so cells near the
    edge are not biased by the clipped line. Accuracy about one cell (~1.4 m at 2 m)."""
    from rasterio.features import rasterize
    from rasterio.transform import from_origin
    from shapely.geometry import LineString
    half = n * res / 2
    xs, ys = np.asarray(xs, float), np.asarray(ys, float)
    lo_x, hi_x = min(-half, xs.min()), max(half, xs.max())
    lo_y, hi_y = min(-half, ys.min()), max(half, ys.max())
    pad = 2 * res
    W = int(np.ceil((hi_x - lo_x + 2 * pad) / res)); Hh = int(np.ceil((hi_y - lo_y + 2 * pad) / res))
    x0, y0 = lo_x - pad, hi_y + pad                      # north-west corner of the canvas
    tr = from_origin(x0, y0, res, res)
    m = rasterize([(LineString(np.c_[xs, ys]), 1)], out_shape=(Hh, W), transform=tr, all_touched=True, dtype="uint8").astype(bool)
    d = T.distance_to(m, res)
    c0 = int(round((-half - x0) / res)); r0 = int(round((y0 - half) / res))
    return d[r0:r0 + n, c0:c0 + n]


def _entries(res, report, geo_meta):
    nan_note = "NaN where any input cell is unknown. "
    win_note = "NaN within one window radius of the grid edge (the window would be cut). "
    reg = geo_meta.get("registration", {})
    reg_txt = ""
    if reg:
        if reg.get("shift_applied"):
            reg_txt = (f"The geologic polygons were shifted {reg['applied_shift_east_m']:+.1f} m east / {reg['applied_shift_north_m']:+.1f} m north "
                       f"to correct a measured mosaic offset; the per-tile residual spread is up to {reg.get('residual_spread_max_m', 25):.0f} m "
                       f"(rms {reg.get('residual_spread_rms_m', 0):.0f} m), so expect about +-25 m of unexplained registration error plus "
                       "contact-drawing error at 1:75,000 (the map's own readme: 100-200 m offsets before correction). ")
        else:
            reg_txt = "NO registration shift was applied: the map readme states 100-200 m offsets, so contacts may be that far off. "
    E = []

    def add(**kw):
        kw.setdefault("coverage", "site")
        kw.setdefault("resolution_m", res)
        kw["quality"] = kw["quality"] + " INTERIM / derived layer."
        E.append(kw)

    add(id="aspect_deg", kind="continuous", file="aspect_deg.tif", title="Aspect (downslope direction)", units="degrees",
        measures=("Direction the ground faces, i.e. the compass bearing of steepest DESCENT, degrees clockwise from north (0 = faces north, 90 = east, "
                  "180 = south, 270 = west), Horn 3x3 finite difference on the 2 m elevation (about 6 m support), same gradient as slope_2m. "
                  "'North-facing' means aspect_deg within 45 deg of 0 (or aspect_class N / NE / NW)."),
        does_not_measure=("Aspect on ground flatter than 1 degree (undefined, stored as NaN: absence of a value there means 'flat', not 'unknown'; "
                          "use aspect_class to tell flat from no-data), or the facing of features smaller than about 6 m. It says nothing about "
                          "illumination: sun exposure depends on sun position, latitude and horizon shadowing, not on aspect alone."),
        quality=nan_note + "NaN where slope < 1 deg (a gradient that small is comparable to DTM stereo noise at 2 m)." + DTM_NOTE,
        method=f"Horn 3x3 gradient (dz/dx, dz/dy); aspect = atan2(-dz/dx, -dz/dy_north) mod 360; undefined if slope < {MIN_ASPECT_SLOPE} deg.",
        source=_src(CIT_HORN))
    add(id="aspect_class", kind="categorical", file="aspect_class.tif", title="Aspect class (8-way + flat)", units=None,
        measures=("Eight 45-degree compass sectors of aspect_deg (N = 337.5-22.5, NE = 22.5-67.5, ...) plus 'flat' where slope < 1 degree. "
                  "Query 'north-facing slopes' as aspect_class in [N, NE, NW] (or == N for strictly north)."),
        does_not_measure="Sun exposure or shading; the facing of sub-6 m features. 'flat' means the slope is under 1 degree, not that the area is level at all scales.",
        quality="-1 = elevation unknown in the 3x3 neighbourhood. Sector edges are arbitrary: a cell at 22 deg and one at 23 deg land in different classes." + DTM_NOTE,
        method="floor(((aspect + 22.5) mod 360) / 45); slope < 1 deg -> flat.",
        source=_src(CIT_HORN), classes={int(k): v for k, v in T.ASPECT_CLASSES.items()})
    sm_txt = (f"computed on the elevation after Gaussian smoothing (sigma {SMOOTH_SIGMA_M:.0f} m = 2.5 cells, kernel truncated at 3 sigma = 15 m radius, "
              f"full width at half maximum about {2.355 * SMOOTH_SIGMA_M:.0f} m) to remove 1-2 m stereo noise; features narrower than roughly 10-15 m are suppressed. ")
    sign_txt = ("Sign convention (both layers): POSITIVE = convex (crest, spur, dome), NEGATIVE = concave (hollow, valley, bowl); values are 1/m, so a "
                "paraboloid z = a r^2 gives -2a. (Zevenbergen and Thorne's raw plan-curvature formula has the opposite sign to their profile formula; "
                "plan is flipped here so both read the same way.) ")
    cq = (nan_note + "NaN where the smoothed gradient is about zero (flow direction undefined) and where any cell of the smoothing kernel is unknown. " + DTM_NOTE)
    add(id="curvature_profile", kind="continuous", file="curvature_profile.tif", title="Profile curvature (smoothed)", units="1/m",
        measures=("Curvature of the surface along the downslope direction (convex breaks of slope at the top of scarps = positive, concave bases = negative), "
                  "Zevenbergen and Thorne 3x3 partial-quartic, " + sm_txt + sign_txt),
        does_not_measure="Sub-10 m relief, rock texture, or absolute slope. Magnitudes depend on the smoothing scale and are not comparable with curvature at other scales.",
        quality=cq, method="Zevenbergen-Thorne D,E,F,G,H coefficients on the smoothed DEM: profile = -2(DG^2+EH^2+FGH)/(G^2+H^2).",
        source=_src(CIT_ZT))
    add(id="curvature_plan", kind="continuous", file="curvature_plan.tif", title="Plan curvature (smoothed)", units="1/m",
        measures=("Curvature of the contour line across the slope (spurs / ridges diverge = positive, gullies / hollows converge = negative), "
                  "Zevenbergen and Thorne 3x3 partial-quartic, " + sm_txt + sign_txt),
        does_not_measure="Sub-10 m relief; flow accumulation (this is local shape only, not drainage area).",
        quality=cq, method="plan = -2(DH^2+EG^2-FGH)/(G^2+H^2) (sign flipped from Z&T so + = convex).", source=_src(CIT_ZT))
    add(id="tpi_50m", kind="continuous", file="tpi_50m.tif", title="Topographic position index (50 m disc)", units="m",
        measures=("Cell elevation minus the mean elevation of the other cells within 50 m (disc, centre excluded) on the 2 m grid. "
                  "Positive = above its surroundings (ridge, crest, mesa top), negative = below (hollow, channel, crater floor next to a rim), about 0 = flat or mid-slope."),
        does_not_measure="Position at other scales (a hill-top at 500 m scale can still be negative against a nearby 50 m bump); not a landform classification by itself.",
        quality=nan_note + win_note + "NaN if fewer than 90% of the disc cells are known." + DTM_NOTE,
        method="z - mean(z over 50 m disc excluding centre); FFT disc convolution.", source=_src(CIT_WEISS))
    add(id="tri", kind="continuous", file="tri.tif", title="Terrain ruggedness index (3x3)", units="m",
        measures="Riley et al. ruggedness: square root of the summed squared elevation differences between a cell and its 8 neighbours (2 m cells, 6 m window). Unit is metres; includes the slope contribution (a smooth 30 deg ramp has a high TRI).",
        does_not_measure="Roughness at rock scale (<2 m) or ruggedness at larger scales; not slope-independent (use rough_5x5 and slope_2m for comparison).",
        quality=nan_note + "NaN on the outermost cell ring." + DTM_NOTE,
        method="sqrt(sum_{8 neighbours}(z_i - z_centre)^2).", source=_src(CIT_RILEY))
    add(id="local_relief_50m", kind="continuous", file="local_relief_50m.tif", title="Local relief (50 m radius)", units="m",
        measures="Maximum minus minimum elevation within 50 m (disc, centre to cell-centre distance) of each cell: a proxy for the height of a scarp, cliff or incised wall in the neighbourhood.",
        does_not_measure="Whether the relief is bedrock, loose talus, a crater rim or a dune; the height of a single exposed section (it can include the regional slope: 30 deg ground gives about 58 m over a 100 m diameter).",
        quality=nan_note + win_note + "Unknown cells inside the disc are ignored (the extremes of the known cells are used)." + DTM_NOTE,
        method="max(z) - min(z) over a 50 m disc (exact disc via row-wise 1-D extremum filters).", source=_src(CIT_EVANS))
    add(id="local_relief_200m", kind="continuous", file="local_relief_200m.tif", title="Local relief (200 m radius)", units="m",
        measures="Maximum minus minimum elevation within 200 m of each cell: highlights large landforms (delta-front scarps, crater rim, mesa edges) against flat floor.",
        does_not_measure="Composition or structure; it includes the regional tilt (up to roughly 2 x 200 x tan(slope) on a uniform slope).",
        quality=nan_note + win_note + "Unknown cells inside the disc are ignored." + DTM_NOTE,
        method="max(z) - min(z) over a 200 m disc.", source=_src(CIT_EVANS))
    add(id="outcrop_exposure", kind="continuous", file="outcrop_exposure.tif", title="Outcrop exposure (steep-face relief)", units="m",
        measures=(f"Height proxy for an exposed vertical section: local_relief_50m where at least {STEEP_FRAC:.0%} of the cells within 50 m have slope_2m >= {STEEP_DEG:.0f} degrees "
                  "(a steep face is actually present in the window), else 0. High values mean tall steep walls nearby, the places where stratigraphy can be seen in section."),
        does_not_measure=("Rock vs sand or regolith: the layer cannot tell bedrock from talus, a dune slip face or a crater wall, and it does not know bed orientation, "
                          "layering, accessibility or dust cover. It is a heuristic screening index, not an outcrop map; the 25 deg and 2 % thresholds are our assumptions."),
        quality=nan_note + win_note + "Steep cells where slope_2m is NaN count as not steep. Stereo noise (quality_flag hirise_noisy) can create false steep cells." + DTM_NOTE,
        method=f"relief50 if disc_fraction(slope_2m >= {STEEP_DEG}) >= {STEEP_FRAC} else 0 (our heuristic, no published method).",
        source=_src("heuristic combining slope (Horn 1981) and local relief (Evans 1972)"))
    add(id="dist_to_traverse_m", kind="continuous", file="dist_to_traverse_m.tif", title="Distance to Perseverance traverse", units="m",
        measures="Euclidean distance from the cell centre to the nearest point of the Perseverance traverse line (traverse.geojson, NASA MMGIS drive segments, straight lines between published vertices).",
        does_not_measure="Distance along any drivable route; whether the line of sight or terrain between the cell and the traverse is passable; the rover's true path between vertices (vertices are up to about 300 m apart).",
        quality=(nan_note + f"Accurate to about one cell (rasterised line, ~1.5 m). Parts of the traverse outside the grid are included, so edge cells are unbiased. "
                 f"Traverse covers sols up to the last waypoint in the file ({report.get('traverse_last_sol', 'see traverse.geojson')}); later drives are absent. "
                 "Position accuracy of the traverse in this frame is not independently verified."),
        method="distance_transform_edt of the rasterised traverse polyline.", source=_src(CIT_EDT, "traverse.geojson (sitebuild/science.py)"))
    add(id="dist_to_unit_contact_m", kind="continuous", file="dist_to_unit_contact_m.tif", title="Distance to geologic-unit contact", units="m",
        measures="Euclidean distance from the cell centre to the nearest boundary between two different mapped geologic units (cells touching a different known unit; the boundary with 'no polygon' is not a contact).",
        does_not_measure="Real contact positions at metre accuracy, or whether the contact is conformable, gradational or a fault; units hidden under the eolian bedform unit give no contact.",
        quality=(reg_txt + "Treat distances below about 25 m (and below 100-200 m if registration is doubted) as 'at the contact', not as a measured offset. "
                 "NaN where geologic_unit has no polygon. Contacts at the grid edge are truncated. 1:75,000 map contacts are generalised."),
        method="distance_transform_edt of 4-neighbour boundaries of the geologic_unit raster (after the registration shift).",
        source=_src(CIT_EDT, "geologic_unit layer (USGS SIM 3464, Sun and Stack 2020, doi:10.3133/sim3464)"), registration=reg)
    add(id="unit_diversity_100m", kind="continuous", file="unit_diversity_100m.tif", title="Geologic unit diversity (100 m)", units="count",
        measures="Number of distinct mapped geologic units having at least one cell within 100 m of the cell (integer stored as float). 1 = deep inside one unit, 2+ = near a contact, 3+ = junctions.",
        does_not_measure="Lithologic diversity (units are map bodies, not compositions) or the area share of each unit.",
        quality=(reg_txt + win_note + "NaN where geologic_unit has no polygon at the cell. Cells with unmapped neighbours get a lower bound. 100 m is comparable to the registration uncertainty, so counts of 2 versus 1 are robust only beyond about 25 m of a contact."),
        method="count of unit classes with distance_transform_edt <= 100 m.", source=_src(CIT_EDT, "geologic_unit layer (USGS SIM 3464)"), registration=reg)
    add(id="mineral_diversity_100m", kind="continuous", file="mineral_diversity_100m.tif", title="CRISM mineral diversity (100 m)", units="count",
        measures=("Number of distinct CRISM mineral layers (of 7: carbonate, olivine, Fe/Mg-smectite, high-Ca pyroxene, low-Ca pyroxene, kaolinite, Mg-smectite-2) with at least one "
                  "cell of tier >= weak_orbital within 100 m (integer stored as float). Counts layers, not independent minerals: mixed prototypes appear in more than one layer, and pyroxene/smectite variants count separately."),
        does_not_measure=("Mineral abundance, absence, or true diversity. no_coverage (-1) cells are never counted as absence, so the value is a LOWER BOUND wherever coverage is partial; "
                          "not_detected means a different prototype matched, not that the mineral is missing."),
        quality=("NaN where no cell of any of the 7 layers has coverage (tier >= 0) within 100 m, and within 100 m of the grid edge. CRISM is 36 m native, so a 100 m disc spans only about 3 source pixels; the CRISM-to-terrain registration residual "
                 "was not measured (tens of metres or more). Detection sensitivity varies between the mosaicked CRISM scenes."),
        method="count of evidence layers with any (tier >= 2) cell within 100 m; no_coverage ignored.",
        source={"mission": "MRO", "instrument": "CRISM (derived count)", "product": "7 evidence layers (Parente et al. 2021, Zenodo 5575824)",
                "citation": "Parente et al. (2021) doi:10.5281/zenodo.5575824; count method " + CIT_EDT, "url": "https://zenodo.org/records/5575824"})
    return E


def run(out_dir: Path = OUT, make_figure: bool = True):
    out_dir = Path(out_dir)
    t0 = time.time()
    from atlas_core import load_site
    cfg = yaml.safe_load((out_dir / "site.yaml").read_text())
    n, res = cfg["size_px"], cfg["res_m"]
    site = load_site(out_dir)
    elev = site.layer("elevation").data
    slope = site.layer("slope_2m").data
    unit = site.layer("geologic_unit").data
    geo_meta = site.layer("geologic_unit").meta
    L = {}

    def lap(name):
        print(f"  geomorph {name:24s} {time.time() - t0:6.1f}s"); sys.stdout.flush()

    L["aspect_deg"] = T.aspect_deg(elev, res, MIN_ASPECT_SLOPE); lap("aspect_deg")
    L["aspect_class"] = T.aspect_class(elev, res, MIN_ASPECT_SLOPE); lap("aspect_class")
    L["curvature_profile"], L["curvature_plan"] = T.curvature_zt(elev, res, SMOOTH_SIGMA_M); lap("curvature")
    L["tpi_50m"] = T.tpi(elev, 50.0, res); lap("tpi_50m")
    L["tri"] = T.tri_riley(elev); lap("tri")
    L["local_relief_50m"] = T.local_relief(elev, 50.0, res); lap("local_relief_50m")
    L["local_relief_200m"] = T.local_relief(elev, 200.0, res); lap("local_relief_200m")
    steep = np.isfinite(slope) & (slope >= STEEP_DEG)
    frac = T.disc_fraction(steep, 50.0, res)
    ox = np.where(frac >= STEEP_FRAC, L["local_relief_50m"], 0.0).astype(np.float32)
    ox[~np.isfinite(L["local_relief_50m"]) | ~np.isfinite(frac)] = np.nan
    L["outcrop_exposure"] = ox; lap("outcrop_exposure")

    tl = json.loads((out_dir / "traverse.geojson").read_text())
    line = next(f for f in tl["features"] if f["geometry"]["type"] == "LineString")["properties"]
    L["dist_to_traverse_m"] = line_distance(line["x"], line["y"], n, res)
    sols = [f["properties"]["sol"] for f in tl["features"] if f["geometry"]["type"] == "Point" and "sol" in f["properties"]]
    L["dist_to_traverse_m"][~np.isfinite(elev)] = np.nan; lap("dist_to_traverse_m")

    d = T.distance_to(T.contact_mask(unit), res)
    d[unit < 0] = np.nan
    L["dist_to_unit_contact_m"] = d; lap("dist_to_unit_contact_m")
    ud = T.count_within([unit == k for k in sorted(set(np.unique(unit)) - {-1})], 100.0, res)
    ud[unit < 0] = np.nan
    L["unit_diversity_100m"] = ud; lap("unit_diversity_100m")
    ev = [site.layer(e).data for e in EVIDENCE]
    md = T.count_within([e >= TIERS["weak_orbital"] for e in ev], 100.0, res)
    covered = np.zeros((n, n), bool)
    for e in ev:
        covered |= e >= 0
    md[T.distance_to(covered, res) > 100.0 + 1e-6] = np.nan
    L["mineral_diversity_100m"] = md; lap("mineral_diversity_100m")

    report = {"traverse_last_sol": max(sols) if sols else None}
    entries = _entries(res, report, geo_meta)
    for e in entries:
        arr = L[e["id"]]
        if e["kind"] == "categorical":
            write_tif(out_dir / e["file"], arr, "int16", -1, n, res)
        else:
            write_tif(out_dir / e["file"], arr, "float32", np.nan, n, res)
    own = {e["id"] for e in entries}
    cfg["layers"] = [l for l in cfg["layers"] if l["id"] not in own] + entries
    (out_dir / "site.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))
    print(f"  geomorph layers written ({len(entries)}) in {time.time() - t0:.0f}s")
    if make_figure:
        figure(out_dir, elev, L, n, res)
    return report


def figure(out_dir, elev, L, n, res):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap
    sys.path.insert(0, str(ROOT / "sitebuild"))
    from build_site import hillshade
    half = n * res / 2
    ext = (-half, half, -half, half)
    st = 2
    fig, ax = plt.subplots(2, 4, figsize=(24, 12.4))
    ax = ax.ravel()
    ax[0].imshow(hillshade(elev, res)[::st, ::st], cmap="gray", extent=ext); ax[0].set_title("hillshade (az 315, alt 35)")
    cm = ListedColormap(["#1f77b4", "#17becf", "#2ca02c", "#bcbd22", "#ff7f0e", "#d62728", "#9467bd", "#e377c2", "#dddddd"])
    ax[1].imshow(np.where(L["aspect_class"] < 0, 8, L["aspect_class"])[::st, ::st], cmap=cm, vmin=0, vmax=8, extent=ext, interpolation="nearest")
    ax[1].set_title("aspect_class (N blue, NE cyan, E green, SE olive, S orange, SW red, W purple, NW pink, flat grey)", fontsize=9)
    im = ax[2].imshow(L["tpi_50m"][::st, ::st], cmap="RdBu_r", vmin=-6, vmax=6, extent=ext); ax[2].set_title("tpi_50m (m)"); plt.colorbar(im, ax=ax[2], fraction=.046)
    im = ax[3].imshow(L["local_relief_200m"][::st, ::st], cmap="magma", vmin=0, vmax=120, extent=ext); ax[3].set_title("local_relief_200m (m)"); plt.colorbar(im, ax=ax[3], fraction=.046)
    im = ax[4].imshow(L["outcrop_exposure"][::st, ::st], cmap="magma", vmin=0, vmax=40, extent=ext); ax[4].set_title("outcrop_exposure (m)"); plt.colorbar(im, ax=ax[4], fraction=.046)
    im = ax[5].imshow(np.log10(1 + L["dist_to_traverse_m"][::st, ::st]), cmap="viridis_r", extent=ext); ax[5].set_title("log10(1 + dist_to_traverse_m)"); plt.colorbar(im, ax=ax[5], fraction=.046)
    im = ax[6].imshow(L["unit_diversity_100m"][::st, ::st], cmap="viridis", vmin=1, vmax=4, extent=ext); ax[6].set_title("unit_diversity_100m"); plt.colorbar(im, ax=ax[6], fraction=.046)
    im = ax[7].imshow(L["mineral_diversity_100m"][::st, ::st], cmap="viridis", vmin=0, vmax=4, extent=ext); ax[7].set_title("mineral_diversity_100m"); plt.colorbar(im, ax=ax[7], fraction=.046)
    for a in ax:
        a.set_xticks([]); a.set_yticks([])
    plt.tight_layout(); (ROOT / "docs").mkdir(exist_ok=True); plt.savefig(ROOT / "docs/geomorph_check.png", dpi=55); plt.close()


if __name__ == "__main__":
    run()

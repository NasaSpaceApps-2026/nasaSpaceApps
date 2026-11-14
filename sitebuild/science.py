"""INTERIM science layers and points for the Ares Atlas query engine (our site dir only).

Run by build_site.py after the terrain layers are written (or alone:  .venv/bin/python -m sitebuild.science).
Everything here is interim and is meant to be replaced by the team pipeline's official layers
(pipeline/ is owned by Caden) when they exist. Nothing here edits pipeline/, contracts/ or data/SOURCES.md.

Adds to data/build/site/jezero/:
  traverse.geojson          Perseverance traverse (NASA MMGIS) + end-of-drive waypoints, for devapi /validation
  geologic_unit.tif         categorical, USGS SIM 3464 units rasterised to the 2 m grid
  carbonate.tif ... (7)     evidence layers from the Parente et al. CRISM mineral map (Zenodo 5575824)
  pois.geojson              + sample_site POIs (approximate, derived from rover waypoints by sol) and
                            named_region POIs (centroids of derived sample positions, method labelled)
  site.yaml                 + catalogue entries;  SOURCES_site.md  + provenance section

Raw downloads are cached under data/raw/science/ and never refetched when present.
"""
from __future__ import annotations

import html
import json
import math
import re
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import rasterio
import yaml
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.warp import reproject, transform_geom

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "sitebuild"))
sys.path.insert(0, str(ROOT / "pipeline"))     # site_cfg (read-only import via site_frame)

from atlas_core.site import MARS_RADIUS_M, TIERS  # noqa: E402
from site_frame import site_crs, site_transform   # noqa: E402

RAW = ROOT / "data/raw/science"
OUT = ROOT / "data/build/site/jezero"
RES = 2.0
N = 4096
HALF = N * RES / 2
RETRIEVED = "2026-11-14"

INTERIM = ("INTERIM layer built by the query-engine team (sitebuild/science.py) from the public source cited; "
           "to be replaced by the data pipeline's official layer when available.")

URL_WAYPOINTS = "https://mars.nasa.gov/mmgis-maps/M20/Layers/json/M20_waypoints.json"
URL_TRAVERSE = "https://mars.nasa.gov/mmgis-maps/M20/Layers/json/M20_traverse.json"
URL_SAMPLES = "https://science.nasa.gov/mission/mars-2020-perseverance/mars-rock-samples/"
URL_PARENTE = "https://zenodo.org/records/5575824"
URL_PARENTE_TIF = "https://zenodo.org/api/records/5575824/files/jezeroFinal.tif/content"
URL_PARENTE_README = "https://zenodo.org/api/records/5575824/files/Jezero_readme.pdf/content"
URL_SIM = "https://pubs.usgs.gov/sim/3464/sim3464_database.zip"
URL_FARLEY = "https://doi.org/10.1126/science.abo2196"
URL_WILLIAMS = ("https://ntrs.nasa.gov/api/citations/20230000588/downloads/"
                "Williams%20et%20al_LPSC%202023%20Delta%20Front%20Overview%20abstract_v5.pdf")
URL_HUROWITZ = "https://goldschmidtabstracts.info/2023/18487.pdf"


# ------------------------------------------------------------------ fetching (cached)
def fetch(url: str, path: Path, resume: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not resume:
        return path
    print(f"  downloading {url} -> {path.name}"); sys.stdout.flush()
    if resume:       # large file: curl with resume (-C -)
        subprocess.run(["curl", "-sSL", "-C", "-", "-o", str(path), url], check=True)
    else:
        req = urllib.request.Request(url, headers={"User-Agent": "ares-atlas-sitebuild"})
        with urllib.request.urlopen(req, timeout=120) as r, open(path, "wb") as f:
            f.write(r.read())
    return path


# ------------------------------------------------------------------ geometry helpers
def lonlat_to_xy(lon, lat):
    """Site-frame metres via the same spherical equirectangular maths as atlas_core.Site (lat0, lon0 from site.yaml)."""
    return (MARS_RADIUS_M * math.cos(math.radians(LAT0)) * np.radians(np.asarray(lon, float) - LON0),
            MARS_RADIUS_M * np.radians(np.asarray(lat, float) - LAT0))


from site_cfg import LAT0, LON0  # noqa: E402


def in_site(x, y, margin=0.0):
    return (np.abs(x) <= HALF + margin) & (np.abs(y) <= HALF + margin)


# ------------------------------------------------------------------ 1. traverse, waypoints
def load_waypoints():
    p = fetch(URL_WAYPOINTS, RAW / "mmgis/M20_waypoints.json")
    feats = json.loads(p.read_text())["features"]
    out = []
    for f in feats:
        pr = f["properties"]
        lon, lat = f["geometry"]["coordinates"][:2]
        x, y = lonlat_to_xy(lon, lat)
        out.append(dict(sol=int(pr["sol"]), rmc=pr["RMC"], lon=lon, lat=lat, x=float(x), y=float(y),
                        final=pr.get("final"), note=pr.get("Note"), dist_total_m=pr.get("dist_total_m")))
    return out


def build_traverse(wps):
    p = fetch(URL_TRAVERSE, RAW / "mmgis/M20_traverse.json")
    segs = json.loads(p.read_text())["features"]
    segs = sorted(segs, key=lambda f: (f["properties"]["sol"]))
    keep, coords = [], []
    for f in segs:
        c = np.array([cc[:2] for cc in f["geometry"]["coordinates"]], float)
        x, y = lonlat_to_xy(c[:, 0], c[:, 1])
        if in_site(x, y, 100.0).any():
            keep.append(f["properties"]["sol"])
            coords.extend(c.tolist())
    # drop consecutive duplicate vertices
    line = [coords[0]]
    for c in coords[1:]:
        if c != line[-1]:
            line.append(c)
    line = np.array(line)
    lx, ly = lonlat_to_xy(line[:, 0], line[:, 1])
    jumps = np.hypot(np.diff(lx), np.diff(ly))
    in_wp = [w for w in wps if abs(w["x"]) <= HALF and abs(w["y"]) <= HALF]
    feats = [{"type": "Feature",
              "geometry": {"type": "LineString", "coordinates": [[round(a, 7), round(b, 7)] for a, b in line]},
              "properties": {"name": "Perseverance traverse (drive segments, sols %d-%d, clipped to the site + 100 m)" % (min(keep), max(keep)),
                             "sol_first": min(keep), "sol_last": max(keep), "n_segments": len(keep),
                             "max_vertex_gap_m": round(float(jumps.max()), 1),
                             "x": [round(float(v), 1) for v in lx], "y": [round(float(v), 1) for v in ly],
                             "frame": "site frame metres (x east, y north) matching the lon/lat coordinates"}}]
    for w in in_wp:
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [w["lon"], w["lat"]]},
                      "properties": {"sol": w["sol"], "rmc": w["rmc"], "x": round(w["x"], 1), "y": round(w["y"], 1),
                                     "final": w["final"]}})
    fc = {"type": "FeatureCollection",
          "name": "M20_traverse_sites_clipped",
          "metadata": {"source": "NASA/JPL-Caltech Mars 2020 MMGIS (M20_traverse.json, M20_waypoints.json)",
                       "urls": [URL_TRAVERSE, URL_WAYPOINTS], "retrieved": RETRIEVED,
                       "etag_waypoints": "6e29cc7bd422e3da1a1e78e0c4855dc1", "etag_traverse": "fafb42a07541225925386bcb7b0935a7",
                       "lat_convention": "planetocentric, east-positive longitude", "note": INTERIM},
          "features": feats}
    (OUT / "traverse.geojson").write_text(json.dumps(fc))
    info = dict(n_vertices=len(line), n_waypoints=len(in_wp), sol_first=min(keep), sol_last=max(keep),
                max_gap_m=float(jumps.max()), length_m=float(np.hypot(np.diff(lx), np.diff(ly)).sum()))
    print("  traverse:", info)
    return info


def landing_offset(wps, landing_xy):
    w0 = wps[0]          # sol 13, site 3 drive 0: "Site increment, no motion" = the landing localisation
    return float(math.hypot(w0["x"] - landing_xy[0], w0["y"] - landing_xy[1])), w0


# ------------------------------------------------------------------ 2. samples
def load_samples():
    p = fetch(URL_SAMPLES, RAW / "nasa/mars-rock-samples.html")
    t = p.read_text(encoding="utf8")
    i = t.find("<table")
    tb = t[i:t.find("</table>", i)]
    rows = []
    for r in re.findall(r"<tr.*?</tr>", tb, re.S):
        cells = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c))).strip()
                 for c in re.findall(r"<t[hd].*?</t[hd]>", r, re.S)]
        rows.append(cells)
    head, body = rows[0], rows[1:]
    out = []
    for c in body:
        d = dict(zip(head, c))
        if not d["Sol Sealed"].isdigit():
            continue
        out.append(dict(no=int(d["Sample No."]), name=d["Sample Name"], feature=d["Feature Name"],
                        region=d["Sampling Location"], sol=int(d["Sol Sealed"]), date=d["Date Sealed"],
                        sample_type=d["Sample Type"], rock_type=d["Rock Type"]))
    (RAW / "nasa/samples_parsed.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    return out


def slug(s):
    s = re.sub(r"\s*\(aka.*?\)", "", s)
    s = s.encode("ascii", "ignore").decode() if False else s
    import unicodedata
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


# In-situ mineral statements read in this session (see docs/data-inventory-minerals.md section 4 and the
# source files cached in data/raw/science/papers/). Key: sample number -> {layer: (statement, citation)}.
SEITAH = ("Farley et al. 2022, Science 377, doi:10.1126/science.abo2196 (abstract, Europe PMC 36007009): the Seitah unit is "
          "'a coarsely crystalline olivine-rich rock' with 'magnesium-iron carbonates along grain boundaries'; core samples of "
          "these rocks are stored aboard Perseverance. This is a unit-level statement applied here at the sample site; "
          "the abstract does not give per-sample mineralogy.")
AMALIK = ("Williams et al. 2023, LPSC 54 abstract (NTRS 20230000588): at Cape Nukshak 'primary mineral phases (olivine, pyroxenes) "
          "were detected by SCAM LIBS and VISIR'; Amalik is the location of cores Shuyak and Mageik. Hurowitz et al. 2023 "
          "(Goldschmidt, doi:10.7185/gold2023.18487): PIXL finds an 'altered olivine-dominated' source at the base of the Cape Nukshak "
          "section (Amalik member). Section-level statements applied at the sample site; no per-sample mineralogy was read.")
SEITAH_CITE = "Farley et al. 2022, Science 377, doi:10.1126/science.abo2196 (Seitah unit statement, unit-level)"
AMALIK_CITE = ("Williams et al. 2023, LPSC 54 (NTRS 20230000588) and Hurowitz et al. 2023 (doi:10.7185/gold2023.18487) "
               "(Cape Nukshak / Amalik section-level statements)")
INSITU_CITE = {4: SEITAH_CITE, 5: SEITAH_CITE, 6: SEITAH_CITE, 7: SEITAH_CITE, 14: AMALIK_CITE, 15: AMALIK_CITE}
INSITU = {
    4: {"olivine": SEITAH, "carbonate": SEITAH}, 5: {"olivine": SEITAH, "carbonate": SEITAH},
    6: {"olivine": SEITAH, "carbonate": SEITAH}, 7: {"olivine": SEITAH, "carbonate": SEITAH},
    14: {"olivine": AMALIK}, 15: {"olivine": AMALIK},
}
SERPENTINE_NOTE = (" Williams et al. 2023 also report VISIR signatures at Amalik most consistent with a Mg-rich serpentine; "
                   "serpentine is not an evidence layer here.")


def derive_sample_positions(samples, wps):
    """Position = last waypoint at or before the sol sealed. Uncertainty = chord between that waypoint and the
    next waypoint at/after the sol (the rover was somewhere in between; a lower bound on the spread), floor 10 m."""
    res = []
    for s in samples:
        prev = [w for w in wps if w["sol"] <= s["sol"]][-1]
        nxt = [w for w in wps if w["sol"] >= s["sol"]]
        chord = math.hypot(nxt[0]["x"] - prev["x"], nxt[0]["y"] - prev["y"]) if nxt else float("nan")
        res.append(dict(s, wp=prev, next_wp=nxt[0] if nxt else None, chord_m=chord,
                        unc_m=max(10.0, chord) if math.isfinite(chord) else 50.0))
    return res


# ------------------------------------------------------------------ 3. geologic units (USGS SIM 3464)
def sim_gdb() -> Path:
    gdb = RAW / "usgs_sim3464/Nili.gdb"
    if gdb.exists():
        return gdb
    zp = fetch(URL_SIM, RAW / "usgs_sim3464/sim3464_database.zip", resume=True)
    with zipfile.ZipFile(zp) as z:
        for n in z.namelist():
            if "/Geodatabase/Nili.gdb/" in n and not n.startswith("__MACOSX"):
                tgt = gdb / n.split("/Geodatabase/Nili.gdb/")[1]
                if n.endswith("/"):
                    tgt.mkdir(parents=True, exist_ok=True)
                else:
                    tgt.parent.mkdir(parents=True, exist_ok=True)
                    tgt.write_bytes(z.read(n))
    return gdb


# Apply the measured registration shift to the geologic polygons (sitebuild/measure_geo_offset.py). Applied only when
# the measurement is consistent across tiles (spread of the per-tile shift vectors < GEO_SHIFT_MAX_SPREAD_M).
APPLY_GEO_SHIFT = True
GEO_SHIFT_MAX_SPREAD_M = 30.0
UNIT_FIX = {"Crater riim unit": "Crater rim unit"}      # typo in the database attribute table (1 polygon)


def geo_shift(info=None):
    """(dx, dy) in metres to ADD to the polygon coordinates so they line up with our elevation frame, or None.
    The measurement says the mosaic (and polygons) lie +dx east / +dy north of our frame, so we move them by -dx, -dy."""
    info = info if info is not None else geo_offset_info()
    if not APPLY_GEO_SHIFT or not info or not info.get("consistent_lt_30m"):
        return None
    if info.get("tile_spread_max_m", 1e9) >= GEO_SHIFT_MAX_SPREAD_M:
        return None
    return (-float(info["median_dx_east_m"]), -float(info["median_dy_north_m"]))


def build_geologic_unit(shift=None):
    from pyogrio.raw import read
    from shapely.affinity import translate
    from shapely.geometry import shape as _shape
    from shapely import from_wkb
    from shapely.geometry import mapping
    gdb = sim_gdb()
    meta, _, geom, fields = read(str(gdb), layer="GeoUnits_v1")
    src = CRS.from_wkt(meta["crs"])
    dst = CRS.from_string(site_crs())
    cols = [str(c) for c in meta["fields"]]
    ui, ni, si, bi = cols.index("unit"), cols.index("name"), cols.index("symbol_noSub"), cols.index("subscript")
    recs = []
    for k in range(len(geom)):
        name = UNIT_FIX.get(fields[ni][k], fields[ni][k])
        sym = (fields[si][k] or "") + (fields[bi][k] or "")
        recs.append((name, sym, from_wkb(geom[k])))
    names = sorted({(n, s) for n, s, _ in recs})
    # polygons are transformed MTM -> site frame (same Mars 2000 sphere, R=3396190)
    items = []
    for name, sym, g in recs:
        gg = transform_geom(src, dst, mapping(g))
        if shift is not None:
            gg = mapping(translate(_shape(gg), shift[0], shift[1]))
        items.append((name, sym, gg, g.area))
    # class codes: unit names sorted by area inside the site, largest first -> stable and readable
    tr = site_transform(N, RES)
    tmp = {}
    for name, sym, gg, a in items:
        tmp.setdefault((name, sym), []).append(gg)
    # exclude names that have no pixel in the site
    pix = {}
    for key, gl in tmp.items():
        m = rasterize([(g, 1) for g in gl], out_shape=(N, N), transform=tr, fill=0, dtype="uint8")
        pix[key] = int(m.sum())
    order = [k for k, v in sorted(pix.items(), key=lambda kv: -kv[1]) if v > 0]
    unit = np.full((N, N), -1, np.int16)
    # paint larger polygons first so small ones win where polygons overlap
    shapes = sorted(((gg, order.index((name, sym))) for name, sym, gg, a in items if (name, sym) in order),
                    key=lambda t: -_area(t[0]))
    unit = rasterize(shapes, out_shape=(N, N), transform=tr, fill=-1, dtype="int16")
    classes = {i: f"{n} ({s})" if s else n for i, (n, s) in enumerate(order)}
    return unit.astype(np.int16), classes


def _area(g):
    from shapely.geometry import shape
    return shape(g).area


# ------------------------------------------------------------------ 4. CRISM mineral map (Parente et al.)
# prototype classes from Jezero_readme.pdf (key page): name -> bold RGB (medium = 0.6 x bold in every case)
PROTOS = [
    # id,                 readme name,        bold RGB,        mixed prototype?
    ("olivine",           "Olivine",          (255, 0, 0)),
    ("carbonate",         "Mg-Carbonate",     (0, 255, 0)),
    ("fe_mg_smectite",    "Fe/Mg-Smectite",   (0, 255, 255)),
    ("olivine_smectite",  "Olivine+Smectite", (255, 128, 114)),
    ("olv_smect_22",      "Olv. Smect + 2.2um", (128, 128, 0)),
    ("carb_22",           "Carb. + 2.2um",    (255, 215, 0)),
    ("high_ca_pyroxene",  "High Ca. Pyroxene", (255, 128, 0)),
    ("low_ca_pyroxene",   "Low Ca. Pyroxene", (218, 165, 32)),
    ("kaolinite",         "Kaolinite",        (139, 69, 19)),
    ("mg_smectite_2",     "Mg-Smectite 2",    (0, 0, 255)),
]
# evidence layer -> (pure prototype, [mixed prototypes that contain the mineral])
MINERAL_LAYERS = {
    "carbonate":        (["carbonate"], ["carb_22"]),
    "olivine":          (["olivine"], ["olivine_smectite", "olv_smect_22"]),
    "fe_mg_smectite":   (["fe_mg_smectite"], ["olivine_smectite", "olv_smect_22"]),
    "high_ca_pyroxene": (["high_ca_pyroxene"], []),
    "low_ca_pyroxene":  (["low_ca_pyroxene"], []),
    "kaolinite":        (["kaolinite"], []),
    "mg_smectite_2":    (["mg_smectite_2"], []),
}
MINERAL_TITLE = {
    "carbonate": "Carbonate (CRISM, Mg-carbonate prototype)",
    "olivine": "Olivine (CRISM prototype)",
    "fe_mg_smectite": "Fe/Mg smectite (CRISM prototype)",
    "high_ca_pyroxene": "High-Ca pyroxene (CRISM prototype)",
    "low_ca_pyroxene": "Low-Ca pyroxene (CRISM prototype)",
    "kaolinite": "Kaolinite (CRISM prototype)",
    "mg_smectite_2": "Mg smectite 2 (CRISM prototype)",
}
# Everyday words users say for each evidence layer. A word listed under several layers is a group alias
# (find_sites / evidence conditions turn it into an any_of over those layers).
MINERAL_ALIASES = {
    "carbonate": ["carbonates", "carbonate rock", "carbonate rocks", "magnesium carbonate", "mg carbonate"],
    "olivine": ["olivine-rich rock", "olivine-rich rocks", "olivine rich", "olivine rock", "olivine bearing rock"],
    "fe_mg_smectite": ["clay", "clays", "clay minerals", "phyllosilicate", "phyllosilicates", "smectite", "smectites",
                       "fe/mg smectite", "fe-mg smectite"],
    "mg_smectite_2": ["clay", "clays", "clay minerals", "phyllosilicate", "phyllosilicates", "smectite", "smectites",
                      "mg smectite", "mg-smectite"],
    "kaolinite": ["clay", "clays", "clay minerals", "phyllosilicate", "phyllosilicates", "kaolin"],
    "high_ca_pyroxene": ["pyroxene", "pyroxenes", "high-ca pyroxene", "hcp"],
    "low_ca_pyroxene": ["pyroxene", "pyroxenes", "low-ca pyroxene", "lcp"],
}
DECODE_MAX_RESID = 14      # RGB units: max distance of a pixel colour from the nearest scaled prototype colour
STRONG_S = 0.8             # brightness factor s (pixel = s x bold colour): s >= 0.8 -> bold -> strong_orbital
MIN_S = 0.4                # below this the colour is too close to black to decode -> no_coverage


def parente_classes():
    """Warp the RGB map to the 2 m site grid (nearest; 36 m native) and decode.
    Returns (cls, s, black): cls int8 index into PROTOS, -1 undecodable; s brightness factor; masks."""
    tif = fetch(URL_PARENTE_TIF, RAW / "parente/jezeroFinal.tif")
    fetch(URL_PARENTE_README, RAW / "parente/Jezero_readme.pdf")
    with rasterio.open(tif) as d:
        rgb = np.zeros((3, N, N), np.uint8)
        reproject(d.read(), rgb, src_transform=d.transform, src_crs=d.crs,
                  dst_transform=site_transform(N, RES), dst_crs=site_crs(), resampling=Resampling.nearest)
        foot = np.zeros((N, N), np.uint8)
        reproject(np.ones((d.height, d.width), np.uint8), foot, src_transform=d.transform, src_crs=d.crs,
                  dst_transform=site_transform(N, RES), dst_crs=site_crs(), resampling=Resampling.nearest)
    # Decode on the unique colours (fast): colour ~ s * bold_RGB(class); nearest class by residual
    flat = rgb.reshape(3, -1).T
    packed = (flat[:, 0].astype(np.int64) << 16) | (flat[:, 1].astype(np.int64) << 8) | flat[:, 2]
    uniq, inv = np.unique(packed, return_inverse=True)
    uc = np.stack([(uniq >> 16) & 255, (uniq >> 8) & 255, uniq & 255], 1).astype(float)
    U = np.array([p[2] for p in PROTOS], float)
    s = (uc @ U.T) / (U * U).sum(1)
    resid = np.linalg.norm(uc[:, None, :] - s[:, :, None] * U[None], axis=2)
    resid[(s < MIN_S) | (s > 1.15)] = 1e9
    k = resid.argmin(1)
    ok = resid[np.arange(len(uc)), k] <= DECODE_MAX_RESID
    cls_u = np.where(ok, k, -1).astype(np.int8)
    s_u = s[np.arange(len(uc)), k]
    black_u = uc.sum(1) < 30
    cls = cls_u[inv].reshape(N, N)
    sf = s_u[inv].reshape(N, N)
    black = black_u[inv].reshape(N, N)
    white = (flat.sum(1) == 765).reshape(N, N)
    return cls, sf, black, white, foot > 0


def build_evidence(cls, sf, black, white):
    """Per mineral layer: tier raster int8. 3 strong_orbital (bold, pure prototype), 2 weak_orbital (subdued/intermediate
    pure prototype, or ANY mixed prototype containing the mineral), 0 not_detected (confidently decoded as a different
    prototype), -1 no_coverage (black, white background, undecodable edge blends)."""
    names = [p[0] for p in PROTOS]
    decoded = cls >= 0
    layers = {}
    for lid, (pure, mixed) in MINERAL_LAYERS.items():
        t = np.full((N, N), -1, np.int8)
        t[decoded] = TIERS["not_detected"]
        for pid in pure:
            m = cls == names.index(pid)
            t[m & (sf >= STRONG_S)] = TIERS["strong_orbital"]
            t[m & (sf < STRONG_S)] = TIERS["weak_orbital"]
        for pid in mixed:
            t[cls == names.index(pid)] = np.maximum(t[cls == names.index(pid)], TIERS["weak_orbital"])
        layers[lid] = t
    return layers


def tier_shares(t):
    v, c = np.unique(t, return_counts=True)
    return {int(a): round(float(b) / t.size, 4) for a, b in zip(v, c)}


# ------------------------------------------------------------------ rasters / yaml
def write_tif(path, arr, dtype, nodata):
    with rasterio.open(path, "w", driver="GTiff", width=N, height=N, count=1, dtype=dtype,
                       crs=site_crs(), transform=site_transform(N, RES), nodata=nodata,
                       compress="deflate", tiled=True, blockxsize=256, blockysize=256) as d:
        d.write(arr.astype(dtype), 1)


def disc_mask(shape_xy, cx, cy, r):
    xs = -HALF + (np.arange(N) + 0.5) * RES
    ys = HALF - (np.arange(N) + 0.5) * RES
    c0, c1 = np.searchsorted(xs, cx - r - RES), np.searchsorted(xs, cx + r + RES)
    r1, r0 = np.searchsorted(-ys, -(cy + r) - RES), np.searchsorted(-ys, -(cy - r) + RES)
    X, Y = np.meshgrid(xs[c0:c1], ys[r1:r0])
    m = np.zeros((N, N), bool)
    m[r1:r0, c0:c1] = np.hypot(X - cx, Y - cy) <= r
    return m


def tier_sources(lid, crism_src, samples):
    """Per-tier provenance. Orbital tiers: MRO/CRISM. confirmed_in_situ: the rover samples that justify it (never CRISM)."""
    orb = dict(kind="orbital", platform=crism_src["mission"], instrument=crism_src["instrument"], citation=crism_src["citation"])
    out = {"not_detected": dict(orb, note="CRISM classified the pixel as a different prototype; absence of evidence"),
           "weak_orbital": dict(orb), "strong_orbital": dict(orb)}
    if samples:
        out["confirmed_in_situ"] = dict(
            kind="rover_sample", platform="Mars 2020 Perseverance (rover), NOT an orbital instrument",
            instrument="not stated per sample: the cited statements are unit/section-level, read from abstracts",
            citation="; ".join(sorted({x["citation"] for x in samples})), samples=samples,
            note="placed at a position derived from rover waypoints (10-50 m radius); not an official sample coordinate")
    return out


def geo_offset_info():
    p = RAW / "usgs_sim3464/offset_measurement.json"
    return json.loads(p.read_text()) if p.exists() else None


def run(out_dir: Path = OUT, make_figure: bool = True):
    global OUT
    OUT = Path(out_dir)
    RAW.mkdir(parents=True, exist_ok=True)
    from atlas_core import load_site
    from atlas_core.terrain import bilinear
    cfg = yaml.safe_load((OUT / "site.yaml").read_text())
    base_site = load_site(OUT)
    science_ids = {"geologic_unit", *MINERAL_LAYERS}
    cfg["layers"] = [l for l in cfg["layers"] if l["id"] not in science_ids]
    pois = [p for p in base_site.pois if p.get("mission_layer") != "science" and p.get("type") not in ("sample_site", "named_region")]

    report = {}
    # ---- 1. traverse and waypoints
    wps = load_waypoints()
    report["traverse"] = build_traverse(wps)
    landing = next(p for p in pois if p["id"] == "octavia_e_butler_landing")
    off, w0 = landing_offset(wps, (landing["x"], landing["y"]))
    report["landing_offset_m"] = off
    print(f"  landing POI vs first MMGIS waypoint (sol {w0['sol']}, {w0['rmc']}, note '{w0['note']}'): {off:.1f} m")

    # ---- 2. samples
    samples = load_samples()
    der = derive_sample_positions(samples, wps)
    inside = [d for d in der if in_site(d["wp"]["x"], d["wp"]["y"]) and abs(d["wp"]["x"]) <= HALF - 20 and abs(d["wp"]["y"]) <= HALF - 20]
    elev = base_site.layer("elevation").data
    def z_at(x, y):
        r, c = base_site.xy_to_rc(x, y)
        return round(float(bilinear(elev, float(r), float(c))), 2)
    sample_pois = []
    for d in inside:
        w = d["wp"]
        stmt = INSITU.get(d["no"])
        desc = (f"Sample {d['no']} of the NASA Perseverance sample list: core/sample '{d['name']}' from feature '{d['feature']}', "
                f"sealed sol {d['sol']} ({d['date']}). Rock type per NASA list: {d['rock_type']} ({d['sample_type']}). "
                f"Sampling region per NASA list: {d['region']}. "
                f"APPROXIMATE position derived from the rover waypoint at sol {w['sol']} (RMC {w['rmc']}, MMGIS waypoint, "
                f"the last waypoint at or before sol {d['sol']}); NOT an official sample coordinate. The rover may have moved "
                f"between that waypoint and coring; the chord to the next waypoint (sol {d['next_wp']['sol'] if d['next_wp'] else 'n/a'}) is {d['chord_m']:.0f} m, "
                f"so treat the position as uncertain by at least {d['unc_m']:.0f} m. ")
        if stmt:
            minerals = " and ".join(stmt)
            desc += f"In-situ mineral statement ({minerals}; tier confirmed_in_situ marked within {min(max(d['unc_m'], 10.0), 50.0):.0f} m): " + next(iter(stmt.values()))
            if d["no"] in (14, 15):
                desc += SERPENTINE_NOTE
        else:
            desc += "No sample-specific mineral measurement was read for this sample; only the NASA rock-type label is available (rock class, not mineral)."
        sample_pois.append(dict(id=f"sample_{d['no']:02d}_{slug(d['name'])}", name=f"Sample {d['no']}: {d['name']}", type="sample_site",
                                x=round(w["x"], 2), y=round(w["y"], 2), z=z_at(w["x"], w["y"]),
                                lat=round(w["lat"], 7), lon=round(w["lon"], 7), mission="Mars 2020", sol=d["sol"],
                                position_method="rover waypoint by sol (derived)", position_uncertainty_m=round(d["unc_m"], 1),
                                rock_type=d["rock_type"], region=d["region"], feature=d["feature"],
                                in_situ_minerals=sorted(stmt) if stmt else [],
                                source_url=URL_SAMPLES, description=desc, interim=True))
    # named regions: centroid of derived sample positions per NASA 'Sampling Location' (only where >=2 samples)
    region_pois = []
    byreg = {}
    for d in inside:
        byreg.setdefault(d["region"], []).append(d)
    for reg, ds in byreg.items():
        if len(ds) < 2 or "Landing" in reg:
            continue
        xs = np.mean([d["wp"]["x"] for d in ds]); ys = np.mean([d["wp"]["y"] for d in ds])
        spread = max(math.hypot(d["wp"]["x"] - xs, d["wp"]["y"] - ys) for d in ds)
        if spread > 400:      # samples too far apart: a centroid would not be a defensible position for the region
            print(f"  region '{reg}' skipped: samples spread {spread:.0f} m from their centroid")
            continue
        lat = LAT0 + math.degrees(ys / MARS_RADIUS_M)
        lon = LON0 + math.degrees(xs / (MARS_RADIUS_M * math.cos(math.radians(LAT0))))
        nums = ", ".join(str(d["no"]) for d in ds)
        region_pois.append(dict(
            id="region_" + slug(reg), name=f"{reg} (sampling area)", type="named_region", x=round(float(xs), 2), y=round(float(ys), 2),
            z=z_at(xs, ys), lat=round(lat, 7), lon=round(lon, 7), mission="Mars 2020", source_url=URL_SAMPLES,
            position_method="centroid of derived sample positions", position_uncertainty_m=round(float(spread) + 50, 1),
            description=(f"Marker for the NASA-list sampling region '{reg}', placed at the centroid of the derived positions of samples {nums} "
                         f"(each from the rover waypoint at its sol; see those sample POIs). This marks where Perseverance sampled in the region, "
                         f"NOT the region's extent or an official gazetteer coordinate. Max distance of a sample from the centroid: {spread:.0f} m."),
            interim=True))
    pois = pois + sample_pois + region_pois
    report["n_sample_pois"] = len(sample_pois)
    report["n_region_pois"] = len(region_pois)
    report["samples_excluded"] = [d["no"] for d in der if d not in inside]

    # ---- 3. geologic units
    off_info = geo_offset_info()
    shift = geo_shift(off_info)
    unit, uclasses = build_geologic_unit(shift)
    write_tif(OUT / "geologic_unit.tif", unit, "int16", -1)
    sh = {uclasses[int(k)]: round(float((unit == k).mean()), 4) for k in uclasses}
    report["geologic_shares"] = sh
    report["geologic_unassigned"] = float((unit < 0).mean())
    report["geologic_shift_applied_m"] = shift
    if off_info:
        off_txt = (f" Measured by this build (sitebuild/measure_geo_offset.py): {off_info['summary']}")
    else:
        off_txt = " (This build did not measure the offset.)"
    if shift is not None:
        shift_txt = (f" SHIFT APPLIED: the polygons were moved {shift[0]:+.1f} m east and {shift[1]:+.1f} m north (the inverse of the measured "
                     f"mosaic offset, per-tile median) because the per-tile shift vectors agree (max deviation {off_info['tile_spread_max_m']:.0f} m, "
                     f"rms {off_info['tile_spread_rms_m']:.0f} m, below {GEO_SHIFT_MAX_SPREAD_M:.0f} m). Residual error is not zero: expect "
                     "up to about 25 m of unexplained spread plus contact-drawing error at 1:75,000, so unit boundaries remain approximate.")
        registration = dict(shift_applied=True, mosaic_offset_east_m=off_info["median_dx_east_m"],
                            mosaic_offset_north_m=off_info["median_dy_north_m"],
                            applied_shift_east_m=round(shift[0], 1), applied_shift_north_m=round(shift[1], 1),
                            residual_spread_max_m=off_info["tile_spread_max_m"], residual_spread_rms_m=off_info["tile_spread_rms_m"],
                            n_tiles=off_info["n_tiles_used"], contact_caveat_m=100.0,
                            note="measured by image-to-hillshade correlation; residual spread across tiles, not absolute accuracy")
    else:
        shift_txt = " No shift has been applied."
        registration = dict(shift_applied=False, contact_caveat_m=100.0,
                            mosaic_offset_east_m=(off_info or {}).get("median_dx_east_m"),
                            mosaic_offset_north_m=(off_info or {}).get("median_dy_north_m"),
                            note="offset not applied (measurement missing or inconsistent)")
    geo_entry = dict(
        id="geologic_unit", kind="categorical", file="geologic_unit.tif",
        title="Geologic units (USGS SIM 3464, interim)", units=None, resolution_m=RES,
        measures=("Mapped geologic unit at each cell from the USGS geologic map of Jezero crater and the Nili Planum region (1:75,000, "
                  "polygon layer GeoUnits_v1), rasterised to the 2 m grid by polygon containment at the cell centre. Class names are the "
                  "map's unit names with the map symbol in brackets."),
        does_not_measure=("Mineralogy or composition: a unit is a mapped body of rock, not a detection. The map's pamphlet says unit-mineral "
                          "associations may be used as proxies where no spectral data exist; this layer makes no such inference. "
                          "Contacts are drawn at 1:75,000, so 2 m cell edges are not accurate contact positions. Units inside the site that the map "
                          "does not subdivide (for example the eolian bedform unit) hide the geology beneath them."),
        quality=("REGISTRATION: the USGS readme states the map 'will have random offsets of about 100-200 meters from the surface operations CTX mosaic' "
                 "(the controlled mosaic our HiRISE/CTX elevation is tied to), i.e. 50-100 cells at 2 m; unit boundaries will NOT line up with the "
                 "HiRISE hillshade at cell level and a point within about 200 m of a contact may be on either unit." + off_txt +
                 shift_txt +
                 " One database polygon is labelled 'Upper etched unit' with symbol Nnp and code 5 (an attribute "
                 "inconsistency in the source); this and a typo ('riim') are kept/fixed by name only. -1 = no polygon "
                 f"({report['geologic_unassigned']:.2%} of the site). " + INTERIM),
        coverage="site", registration=registration,
        source=dict(mission="USGS Astrogeology (map based on MRO CTX)", instrument="geologic mapping on a semi-controlled CTX mosaic",
                    product="SIM 3464 database, Nili.gdb / GeoUnits_v1 (MTM central meridian 77.5 E, Mars 2000 sphere R=3396190 m)",
                    citation="Sun, V.Z. and Stack, K.M., 2020, Geologic map of Jezero crater and the Nili Planum region, Mars: USGS SIM 3464, doi:10.3133/sim3464",
                    url=URL_SIM, licence="USGS publication; no licence text was read in the database metadata (it has a no-warranty statement and 'Fees: None'). Public-domain status NOT verified.",
                    retrieved=RETRIEVED),
        classes=uclasses)
    cfg["layers"].append(geo_entry)

    # ---- 4. CRISM evidence
    cls, sf, black, white, foot = parente_classes()
    ev = build_evidence(cls, sf, black, white)
    names = [p[0] for p in PROTOS]
    report["crism_black_share"] = float(black.mean())
    report["crism_undecodable_share"] = float(((cls < 0) & ~black).mean())
    report["crism_decoded_share"] = float((cls >= 0).mean())
    report["crism_class_shares"] = {n: round(float((cls == i).mean()), 4) for i, n in enumerate(names)}

    # ---- 5. confirmed_in_situ at sample sites (only where INSITU says a source states the mineral)
    marked = []
    insitu_samples: dict[str, list] = {}
    for p in sample_pois:
        no = int(p["id"].split("_")[1])
        for lid in INSITU.get(no, {}):
            r = float(min(max(p["position_uncertainty_m"], 10.0), 50.0))
            m = disc_mask(None, p["x"], p["y"], r)
            ev[lid][m] = TIERS["confirmed_in_situ"]
            marked.append((no, lid, r, int(m.sum())))
            insitu_samples.setdefault(lid, []).append(dict(
                poi=p["id"], name=p["name"], sample_no=no, x=p["x"], y=p["y"], radius_m=r, citation=INSITU_CITE[no]))
    report["in_situ_marked"] = marked

    crism_src = dict(
        mission="MRO", instrument="CRISM (hyperspectral, L detector), UMass GAN-based classification",
        product="Map of the dominant mineralogy over and around the Mars2020 landing ellipse (jezeroFinal.tif, 36 m/px, Zenodo 5575824), from CRISM HRL000040FF, FRT000047A3, FRT00005C5E, FRS00031442",
        citation="Parente, Saranathan, Itoh, Tarnas, Seelos, Mustard (2021), Zenodo, doi:10.5281/zenodo.5575824; methods: Itoh and Parente 2021 (Icarus 354, 114024), Saranathan and Parente 2021 (Icarus 355, 114107)",
        url=URL_PARENTE, licence="CC BY 4.0 (Zenodo record metadata)", retrieved=RETRIEVED)
    for lid, (pure, mixed) in MINERAL_LAYERS.items():
        pn = {p[0]: p[1] for p in PROTOS}
        t = ev[lid]
        mix_txt = ("; mixed prototypes containing it (" + ", ".join(pn[m] for m in mixed) + ") are capped at weak_orbital" if mixed else "")
        insitu_txt = ""
        if lid in ("olivine", "carbonate"):
            nums = sorted(n for n, d in INSITU.items() if lid in d)
            insitu_txt = (f" confirmed_in_situ (4) is set ONLY within the position uncertainty (10-50 m radius, per sample) of sample POIs "
                          f"{', '.join(map(str, nums))}, where a source states the mineral was measured in situ (see each POI description); "
                          "those marks are unit/section-level statements applied at a derived position, and they override the orbital tier there.")
        entry = dict(
            id=lid, kind="evidence", file=f"{lid}.tif", title=MINERAL_TITLE[lid] + ", interim", units=None, resolution_m=36.0,
            aliases=list(MINERAL_ALIASES.get(lid, [])),
            measures=(f"Orbital spectral detection of the '{', '.join(pn[p] for p in pure)}' prototype class in the CRISM mineral map, as an ordinal evidence tier: "
                      f"strong_orbital (3) = bold tone = 'very high confidence' match to the prototype (decoded brightness >= {STRONG_S} of the bold colour); "
                      f"weak_orbital (2) = subdued tone = 'high / intermediate confidence'{mix_txt}; not_detected (0) = the pixel was confidently classified "
                      "as a DIFFERENT prototype." + insitu_txt),
            does_not_measure=("Abundance, grain size or how much of a pixel is this mineral. A prototype is the closest match to a published spectrum; the map's own readme "
                              "says exact attributions can only be verified by further analysis. not_detected means a different prototype was the best match, NOT absence "
                              "of the mineral: this is a dominant-mineralogy map, so minor components are not excluded. Tier boundaries are 36 m pixels resampled to 2 m "
                              "(nearest), so edges are blocky and not accurate at cell level. Dust and the unmodelled classes can hide or mimic detections. "
                              "The CRISM-to-CTX/HiRISE registration residual was not measured here (the authors state they coregistered to the USGS controlled CTX/HiRISE "
                              "products); expect offsets of the order of tens of metres or more relative to rover points."),
            quality=("-1 (no_coverage) marks pixels with NO usable classification, NOT 'nothing detected': (a) black (0,0,0), which the source defines as 'not identified as any "
                     "of the known prototypes' and the Zenodo description also calls the null value; the file has no nodata mask, so unclassified pixels and areas "
                     f"without CRISM data cannot be told apart ({report['crism_black_share']:.1%} of the site), and (b) edge-blend colours that could not be decoded to one prototype "
                     f"(residual > {DECODE_MAX_RESID} RGB units from a scaled prototype colour; {report['crism_undecodable_share']:.1%}). Decoding: the map stores confidence as colour "
                     "brightness, so each colour is fitted as s x (bold prototype RGB); s >= 0.8 is read as bold (the documented bold and subdued tones are 1.0 and 0.6 of the bold "
                     "RGB), 0.4 <= s < 0.8 as subdued, s < 0.4 as undecodable. The 0.8 split and the 14-unit tolerance are our choices. Four CRISM scenes of different dates and "
                     "observing conditions were mosaicked, so detection sensitivity varies spatially. " + INTERIM),
            coverage="partial", source=dict(crism_src),
            tier_sources=tier_sources(lid, crism_src, insitu_samples.get(lid)),
            registration=dict(pixel_m=36.0, residual_measured=False, contact_caveat_m=36.0,
                              note="CRISM-to-terrain registration residual was not measured"), )
        cfg["layers"].append(entry)
        write_tif(OUT / f"{lid}.tif", t, "int8", -1)
        report.setdefault("tier_shares", {})[lid] = tier_shares(t)

    # ---- write
    (OUT / "site.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]}, "properties": p} for p in pois]}
    (OUT / "pois.geojson").write_text(json.dumps(fc, indent=1, ensure_ascii=False))
    write_sources(report, off_info)
    if make_figure:
        figure(OUT, unit, uclasses, ev, wps, sample_pois, region_pois, cls)
    (RAW / "build_report.json").write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps({k: v for k, v in report.items() if k not in ("geologic_shares",)}, indent=1, default=str))
    return report


def figure(out_dir, unit, uclasses, ev, wps, sample_pois, region_pois, cls):
    """docs/science_layers_check.png: hillshade + traverse + sample POIs, geologic units, every evidence layer's tiers."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap
    from matplotlib.patches import Patch
    import rasterio as rio
    with rio.open(out_dir / "elevation.tif") as d:
        z = d.read(1)
    zz = np.where(np.isfinite(z), z, np.nanmean(z))
    gy, gx = np.gradient(zz, RES)
    sl = np.arctan(np.hypot(gx, gy)); asp = np.arctan2(-gx, gy)
    hs = np.clip(np.sin(np.radians(35)) * np.cos(sl) + np.cos(np.radians(35)) * np.sin(sl) * np.cos(np.radians(315) - asp), 0, 1)
    ext = (-HALF, HALF, -HALF, HALF)
    st = 4                                           # display every 4th cell (8 m)
    tl = json.loads((out_dir / "traverse.geojson").read_text())
    ln = next(f for f in tl["features"] if f["geometry"]["type"] == "LineString")["properties"]
    tcol = {-1: (0.1, 0.1, 0.1, 0.55), 0: (0, 0, 0, 0), 2: (1.0, 0.75, 0.0, 0.85), 3: (0.85, 0.05, 0.05, 0.95), 4: (0.9, 0.0, 0.9, 1.0)}
    cm = ListedColormap([tcol[k] for k in (-1, 0, 2, 3, 4)])
    fig, axs = plt.subplots(3, 4, figsize=(26, 19))
    ax = axs.ravel()
    gcol = plt.cm.tab10(np.arange(10))
    def base(a, title):
        a.imshow(hs[::st, ::st], cmap="gray", extent=ext, vmin=0, vmax=1)
        a.set_title(title, fontsize=11); a.set_xlabel("x east (m)"); a.set_ylabel("y north (m)")
    def pts(a, ms=5):
        a.plot([p["x"] for p in sample_pois], [p["y"] for p in sample_pois], "o", mfc="none", mec="cyan", ms=ms + 2, mew=1.2)
    base(ax[0], "hillshade + traverse (blue) + sample POIs (cyan) + regions (yellow)")
    ax[0].plot(ln["x"], ln["y"], "-", color="dodgerblue", lw=1)
    pts(ax[0], 6)
    for p in sample_pois:
        ax[0].annotate(str(p["id"].split("_")[1]), (p["x"], p["y"]), (p["x"] + 40, p["y"] + 40), color="cyan", fontsize=8)
    for p in region_pois:
        ax[0].plot(p["x"], p["y"], "*", color="yellow", ms=14, mec="k")
        ax[0].annotate(p["name"].replace(" (sampling area)", ""), (p["x"], p["y"]), (p["x"] - 450, p["y"] + 120), color="yellow", fontsize=8)
    ax[0].plot(*lonlat_to_xy(77.4508, 18.4447), "r+", ms=16, mew=2)
    ax[0].set_xlim(-HALF, HALF); ax[0].set_ylim(-HALF, HALF)
    base(ax[1], "geologic units (USGS SIM 3464), outlines on hillshade (registration shift per catalogue)")
    xs = np.linspace(-HALF, HALF, unit[::st, ::st].shape[1]); ys = np.linspace(HALF, -HALF, unit[::st, ::st].shape[0])
    handles = []
    for k, nm in uclasses.items():
        m = (unit[::st, ::st] == k).astype(float)
        ax[1].imshow(np.ma.masked_where(m == 0, m), extent=ext, cmap=ListedColormap([gcol[k % 10]]), alpha=0.35, vmin=0, vmax=1)
        ax[1].contour(xs, ys, m, [0.5], colors=[gcol[k % 10]], linewidths=1.2)
        handles.append(Patch(color=gcol[k % 10], label=nm))
    ax[1].legend(handles=handles, fontsize=7, loc="lower left")
    order = ["carbonate", "olivine", "fe_mg_smectite", "high_ca_pyroxene", "low_ca_pyroxene", "kaolinite", "mg_smectite_2"]
    for i, lid in enumerate(order):
        a = ax[2 + i]
        t = ev[lid]
        sh = {k: (t == k).mean() for k in (-1, 0, 2, 3, 4)}
        base(a, f"{lid}: strong {sh[3]:.1%}, weak {sh[2]:.1%}, not_det {sh[0]:.0%}, no_cov {sh[-1]:.0%}")
        a.imshow(cm(np.searchsorted([-1, 0, 2, 3, 4], t[::st, ::st]) / 4.0), extent=ext, interpolation="nearest")
        a.contour(xs, ys, (unit[::st, ::st] == [k for k, n in uclasses.items() if n.startswith("Jezero fan")][0]).astype(float), [0.5], colors="w", linewidths=0.6, linestyles=":") if any(n.startswith("Jezero fan") for n in uclasses.values()) else None
        pts(a, 3)
        a.set_xlim(-HALF, HALF); a.set_ylim(-HALF, HALF)
        rr, cc = np.nonzero(t == 4)
        if rr.size:
            X, Y = -HALF + (cc + 0.5) * RES, HALF - (rr + 0.5) * RES
            a.plot([X.mean()], [Y.mean()], "m*", ms=1)       # placeholder to keep legend order stable
            for p in sample_pois:
                if lid in p.get("in_situ_minerals", []):
                    a.plot(p["x"], p["y"], "o", mfc="m", mec="k", ms=6)
    # zooms on Seitah samples
    sx = np.mean([p["x"] for p in sample_pois if p["id"].split("_")[1] in ("04", "05", "06", "07")])
    sy = np.mean([p["y"] for p in sample_pois if p["id"].split("_")[1] in ("04", "05", "06", "07")])
    w = 600
    for j, lid in enumerate(["olivine", "carbonate"]):
        a = ax[9 + j]
        c0, c1 = int((sx - w + HALF) / RES), int((sx + w + HALF) / RES)
        r0, r1 = int((HALF - sy - w) / RES), int((HALF - sy + w) / RES)
        e2 = (sx - w, sx + w, sy - w, sy + w)
        a.imshow(hs[r0:r1, c0:c1], cmap="gray", extent=e2, vmin=0, vmax=1)
        a.imshow(cm(np.searchsorted([-1, 0, 2, 3, 4], ev[lid][r0:r1, c0:c1]) / 4.0), extent=e2, interpolation="nearest")
        a.set_title(f"zoom 1.2 km around samples 4-7 (Seitah): {lid} tiers (magenta = confirmed_in_situ)", fontsize=10)
        for p in sample_pois:
            if abs(p["x"] - sx) < w and abs(p["y"] - sy) < w:
                a.plot(p["x"], p["y"], "o", mfc="none", mec="cyan", ms=9, mew=1.5)
                a.annotate(p["id"].split("_")[1], (p["x"], p["y"]), (p["x"] + 15, p["y"] + 15), color="cyan", fontsize=9)
    ax[11].axis("off")
    ax[11].legend(handles=[Patch(color=tcol[-1][:3], alpha=0.55, label="-1 no_coverage (black / undecodable)"),
                           Patch(fc="white", ec="k", label="0 not_detected (transparent)"),
                           Patch(color=tcol[2][:3], label="2 weak_orbital"), Patch(color=tcol[3][:3], label="3 strong_orbital"),
                           Patch(color=tcol[4][:3], label="4 confirmed_in_situ (marked disc at sample POI)")], loc="center", fontsize=12,
                  title="evidence tiers (INTERIM layers)")
    plt.tight_layout(); plt.savefig(ROOT / "docs/science_layers_check.png", dpi=55); plt.close()


def write_sources(report, off_info):
    p = OUT / "SOURCES_site.md"
    t = p.read_text() if p.exists() else ""
    t = t.split("\n## Science layers (INTERIM")[0].rstrip() + "\n"
    t += f"""
## Science layers (INTERIM, built by sitebuild/science.py; retrieved {RETRIEVED})

All entries below are interim layers for the query engine, to be replaced by the data pipeline's official layers.

| Item | Source | Licence / credit | Notes |
|---|---|---|---|
| traverse.geojson, waypoints | NASA/JPL-Caltech Mars 2020 MMGIS: {URL_TRAVERSE} ; {URL_WAYPOINTS} (ETags fafb42a0..., 6e29cc7b...; last modified 2026-10-28) | No licence text on the endpoints; NASA content generally not copyrighted, credit NASA/JPL-Caltech | Lat is planetocentric, lon east. Clipped to the site + 100 m. First waypoint (sol 13, RMC 3_0, 'Site increment, no motion') equals the PDS PLACES SITE 1 position. |
| sample_site POIs ({report['n_sample_pois']}) | NASA Mars Rock Samples list {URL_SAMPLES}; positions derived from the MMGIS waypoint at or before the sol sealed | NASA/JPL-Caltech | Positions are APPROXIMATE and derived, not official. Samples {', '.join(map(str, report['samples_excluded']))} fall outside the site or have no derived position inside it. |
| named_region POIs ({report['n_region_pois']}) | Centroids of derived sample positions per NASA 'Sampling Location' | derived | Mark where the rover sampled, not the region's extent. No gazetteer coordinates used. |
| geologic_unit | USGS SIM 3464 database {URL_SIM} (GeoUnits_v1) | USGS; public-domain status not verified | 100-200 m registration offset to CTX per the readme. {('Measured: ' + off_info['summary']) if off_info else ''} Polygon shift applied (east, north m): {report.get('geologic_shift_applied_m')}. |
| carbonate, olivine, fe_mg_smectite, high_ca_pyroxene, low_ca_pyroxene, kaolinite, mg_smectite_2 | Parente et al. 2021, Zenodo {URL_PARENTE} (doi:10.5281/zenodo.5575824), jezeroFinal.tif + Jezero_readme.pdf | CC BY 4.0 | 36 m native, nearest to 2 m. Black = no_coverage. Tier mapping per docs/data-inventory-minerals.md section 1e with our decode rules (see layer 'quality'). |
| confirmed_in_situ marks | Farley et al. 2022 (Seitah olivine, Mg-Fe carbonate; Europe PMC abstract); Williams et al. 2023 LPSC (olivine, pyroxene at Cape Nukshak; Amalik serpentine); Hurowitz et al. 2023 Goldschmidt (olivine-dominated source, Amalik) | papers / abstracts | Only at sample POIs {sorted(INSITU)}; unit/section-level statements applied at derived positions. Delta-front Fe-bearing carbonate (Hurowitz) is NOT marked: it is stated per section, not per sample. |

Cached raw files: data/raw/science/ (mmgis, nasa, parente, usgs_sim3464, papers). Install note: pyogrio and shapely were pip-installed into .venv to read the FileGDB.
"""
    p.write_text(t)


if __name__ == "__main__":
    run()

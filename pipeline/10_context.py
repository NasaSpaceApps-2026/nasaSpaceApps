"""Context block: real NASA CTX data around the HiRISE site (28.7 km square, same site frame).

Sources (USGS/JPL, MRO CTX, verified live 2026-11-14):
  DTM  20 m : .../mosaic/mars2020_trn/CTX/JEZ_ctx_B_soc_008_DTM_MOLAtopography_DeltaGeoid_20m_Eqc_latTs0_lon0.tif
  Ortho 5 m : .../mosaic/mars2020_trn/CTX/ScienceInvestigationMaps_JPL/M20_JezeroCrater_CTXortho_mosaic_5m.tif
Outputs under data/build/ctx/: manifest.json, tiles/ortho/{z}/{x}/{y}.webp (levels 0..CTX_MAX_LEVEL),
web/dem_ctx.u16 (+json), stats.json (CTX vs HiRISE brightness fit and terrain difference, used by 09_validate).
Run: .venv/bin/python pipeline/10_context.py
"""
import json
import math
import os
import sys
import urllib.request
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio import Affine
from rasterio.crs import CRS as RCRS
from rasterio.warp import Resampling, reproject, transform_bounds
from rasterio.windows import from_bounds

os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "8")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "3")
sys.path.insert(0, str(Path(__file__).parent))
from colorize import colorize_banded  # noqa: E402
from site_cfg import CRS, CTX_MAX_LEVEL, CTX_SIZE, LAT0, LON0, R, SIZE  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RAW, OUT = ROOT / "data" / "raw", ROOT / "data" / "build" / "ctx"
BASE = "https://asc-pds-services.s3.us-west-2.amazonaws.com/mosaic/mars2020_trn/CTX/"
DTM_URL = BASE + "JEZ_ctx_B_soc_008_DTM_MOLAtopography_DeltaGeoid_20m_Eqc_latTs0_lon0.tif"
ORTHO_URL = BASE + "ScienceInvestigationMaps_JPL/M20_JezeroCrater_CTXortho_mosaic_5m.tif"
DEM_RES, ORTHO_PX, IMG_PX = 16.0, 512 * 2 ** CTX_MAX_LEVEL, 512
half = CTX_SIZE / 2
site_crs = RCRS.from_string(CRS)


def fetch_dtm():
    p = RAW / "ctx_dtm_20m.tif"
    if not p.exists():
        RAW.mkdir(parents=True, exist_ok=True)
        p.write_bytes(urllib.request.urlopen(DTM_URL, timeout=120).read())
    return p


def fetch_ortho_window():
    """Read the CTX 5 m ortho around the block at 10 m (decimated) into data/raw/ctx_ortho_window.tif."""
    p = RAW / "ctx_ortho_window.tif"
    if p.exists():
        return p
    with rasterio.open("/vsicurl/" + ORTHO_URL) as s:
        l, b, r, t = transform_bounds(site_crs, s.crs, -half - 600, -half - 600, half + 600, half + 600, densify_pts=21)
        win = from_bounds(l, b, r, t, s.transform).round_offsets().round_lengths()
        m = (int(win.height // 2), int(win.width // 2))
        data = s.read(1, window=win, out_shape=m)
        tr = s.window_transform(win) * Affine.scale(win.width / m[1], win.height / m[0])
        prof = s.profile.copy()
        prof.update(width=m[1], height=m[0], transform=tr, tiled=True, blockxsize=256, blockysize=256, compress="deflate", predictor=2)
    with rasterio.open(p, "w", **prof) as d:
        d.write(data, 1)
    return p


def warp(src, res, n, resampling, nodata=None):
    dst = np.full((n, n), np.nan, dtype=np.float32)
    with rasterio.open(src) as s:
        a = s.read(1).astype(np.float32)
        nod = nodata if nodata is not None else s.nodata
        if nod is not None:
            a[(a == nod) | (a < -1e30)] = np.nan
        reproject(a, dst, src_transform=s.transform, src_crs=s.crs, dst_transform=Affine(res, 0, -half, 0, -res, half),
                  dst_crs=site_crs, resampling=resampling, src_nodata=np.nan, dst_nodata=np.nan)
    return dst


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    stats = {}
    # ---- terrain at 16 m
    n_dem = int(CTX_SIZE / DEM_RES)
    dem = warp(fetch_dtm(), DEM_RES, n_dem, Resampling.cubic)
    stats["ctx_dem_nan_share"] = float(np.isnan(dem).mean())
    if np.isnan(dem).any():  # nearest fill so the browser never sees NaN; share reported above
        from scipy import ndimage
        idx = ndimage.distance_transform_edt(np.isnan(dem), return_distances=False, return_indices=True)
        dem = dem[tuple(idx)]
    base = float(np.floor(dem.min()))
    STEP = 0.1  # 20 m DTM: 0.1 m steps cover 6.5 km of relief in 16 bits
    v = np.rint((dem - base) / STEP)
    assert v.max() < 65536
    (OUT / "web").mkdir(exist_ok=True)
    v.astype("<u2").tofile(OUT / "web" / "dem_ctx.u16")
    (OUT / "web" / "dem_ctx.json").write_text(json.dumps({"width": n_dem, "height": n_dem, "res_m": DEM_RES, "base": base, "step": STEP}))

    # ---- CTX DTM vs HiRISE DTM over the site (same datum per USGS; measure, do not assume)
    with rasterio.open(ROOT / "data" / "build" / "analysis" / "dem.tif") as d:
        hi = d.read(1).astype(np.float64)
    c0 = int((half - SIZE / 2) / DEM_RES)
    k = int(SIZE / DEM_RES)
    ctx_site = dem[c0:c0 + k, c0:c0 + k].astype(np.float64)
    hi_16 = hi.reshape(k, int(hi.shape[0] / k), k, int(hi.shape[1] / k)).mean(axis=(1, 3))  # 2 m -> 16 m block mean
    diff = ctx_site - hi_16
    stats["terrain_ctx_minus_hirise_m"] = {"mean": float(diff.mean()), "sd": float(diff.std()), "p95_abs": float(np.percentile(abs(diff), 95)),
                                            "corr": float(np.corrcoef(ctx_site.ravel(), hi_16.ravel())[0, 1])}

    # ---- imagery
    gray = warp(fetch_ortho_window(), CTX_SIZE / ORTHO_PX, ORTHO_PX, Resampling.lanczos, nodata=0)
    stats["ortho_nan_share"] = float(np.isnan(gray).mean())
    # brightness match: HiRISE (real, 1 m) averaged onto the CTX grid vs CTX over the site footprint
    hi_ortho = warp(RAW / "ortho_window.tif", CTX_SIZE / ORTHO_PX, ORTHO_PX, Resampling.average, nodata=0)
    m = ~np.isnan(gray) & ~np.isnan(hi_ortho)
    a, b = np.polyfit(gray[m], hi_ortho[m], 1)
    stats["brightness_fit_hirise_vs_ctx"] = {"gain": float(a), "offset": float(b), "corr": float(np.corrcoef(gray[m], hi_ortho[m])[0, 1]), "n_px": int(m.sum())}
    g8 = np.clip(np.nan_to_num(a * gray + b, nan=0.0), 0, 255).astype(np.uint8)
    del gray, hi_ortho
    rgb = colorize_banded(g8, RAW, LAT0, LON0, CTX_SIZE, ref_size_m=CTX_SIZE)
    full = Image.fromarray(rgb, "RGB")
    cnt = 0
    for z in range(CTX_MAX_LEVEL + 1):
        side = IMG_PX * 2 ** z
        img = full if side == ORTHO_PX else full.resize((side, side), Image.LANCZOS)
        for ty in range(2 ** z):
            for tx in range(2 ** z):
                p = OUT / "tiles" / "ortho" / str(z) / str(tx) / f"{ty}.webp"
                p.parent.mkdir(parents=True, exist_ok=True)
                img.crop((tx * IMG_PX, ty * IMG_PX, (tx + 1) * IMG_PX, (ty + 1) * IMG_PX)).save(p, "WEBP", quality=80)
                cnt += 1
    manifest = {"site": "jezero-context", "lat0": LAT0, "lon0": LON0, "radius_m": int(R), "size_m": int(CTX_SIZE),
                "elev_min": math.floor(float(dem.min())), "elev_max": math.ceil(float(dem.max())),
                "layers": [{"id": "ortho", "title": "CTX orthomosaic, colourised (context, false colour)", "mission": "MRO", "instrument": "CTX",
                            "max_level": CTX_MAX_LEVEL, "kind": "base",
                            "credit": "NASA/JPL/MSSS/USGS (CTX 5 m, JPL Mars 2020 science mosaic). Brightness matched to HiRISE; colour as the site (false colour).",
                            "url": ORTHO_URL}]}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (OUT / "stats.json").write_text(json.dumps(stats, indent=2))
    print(cnt, "context tiles;", json.dumps(stats, indent=1))


if __name__ == "__main__":
    main()

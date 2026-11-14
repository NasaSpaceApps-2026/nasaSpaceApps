"""Windowed, cached read of the HiRISE DTM (and CTX DTM) around a site."""
import os
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import from_bounds

from site_frame import src_window_bounds

HI_URL = ("https://asc-pds-services.s3.us-west-2.amazonaws.com/mosaic/mars2020_trn/HiRISE/"
          "JEZ_hirise_soc_006_DTM_MOLAtopography_DeltaGeoid_1m_Eqc_latTs0_lon0_blend40.tif")
CTX_PATH = "data/raw/ctx/JEZ_ctx_B_soc_008_DTM_MOLAtopography_DeltaGeoid_20m_Eqc_latTs0_lon0.tif"


def _read_chunked(path, w, chunk_dir, rows=200, workers=6):
    """Read window `w` in row chunks with retries; chunks persist on disk so an interrupted
    (or flaky-network) run resumes instead of restarting."""
    import time
    from concurrent.futures import ThreadPoolExecutor
    from rasterio.windows import Window
    chunk_dir.mkdir(parents=True, exist_ok=True)
    out = np.empty((int(w.height), int(w.width)), np.float32)
    starts = list(range(0, int(w.height), rows))

    def job(r0):
        f = chunk_dir / f"{r0:06d}.npy"
        n = min(rows, int(w.height) - r0)
        if f.exists():
            return r0, np.load(f)
        for attempt in range(8):
            try:
                with rasterio.open(path) as d:
                    a = d.read(1, window=Window(w.col_off, w.row_off + r0, w.width, n)).astype(np.float32)
                np.save(f.with_suffix(".tmp.npy"), a)
                os.replace(f.with_suffix(".tmp.npy"), f)
                return r0, a
            except Exception as e:  # network hiccup
                print(f"  chunk {r0} retry {attempt}: {str(e)[:80]}", flush=True)
                time.sleep(3 * (attempt + 1))
        raise RuntimeError(f"chunk {r0} failed")

    done = 0
    with ThreadPoolExecutor(workers) as ex:
        for r0, a in ex.map(job, starts):
            out[r0:r0 + a.shape[0]] = a
            done += 1
            print(f"  chunk {done}/{len(starts)}", flush=True)
    return out


def read_window(path, bounds, out_tif):
    """Read `bounds` (source CRS) from `path` as float32 with NaN nodata; cache as GeoTIFF."""
    out_tif = Path(out_tif)
    if out_tif.exists():
        with rasterio.open(out_tif) as d:
            return d.read(1), d.transform, d.crs
    with rasterio.open(path) as d:
        w = from_bounds(*bounds, d.transform).round_offsets().round_lengths()
        tr, crs, nodata = d.window_transform(w), d.crs, d.nodata
    a = _read_chunked(path, w, out_tif.with_suffix(".chunks"))
    a[a == nodata] = np.nan
    a[~np.isfinite(a)] = np.nan
    out_tif.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_tif.with_suffix(".part")
    with rasterio.open(tmp, "w", driver="GTiff", width=a.shape[1], height=a.shape[0], count=1,
                       dtype="float32", crs=crs, transform=tr, nodata=np.nan,
                       compress="deflate", predictor=3, tiled=True) as o:
        o.write(a, 1)
    os.replace(tmp, out_tif)
    return a, tr, crs


def get_hirise(lat0, lon0, half):
    os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    os.environ.setdefault("GDAL_HTTP_MULTIRANGE", "YES")
    os.environ.setdefault("GDAL_HTTP_MERGE_CONSECUTIVE_RANGES", "YES")
    b = src_window_bounds(lat0, lon0, half, pad=1.006)
    return read_window("/vsicurl/" + HI_URL, b, f"data/raw/hirise/window_{lat0}_{lon0}_{int(half)}.tif")


def get_ctx(lat0, lon0, half):
    b = src_window_bounds(lat0, lon0, half, pad=1.3)
    return read_window(CTX_PATH, b, f"data/raw/ctx/window_{lat0}_{lon0}_{int(half)}.tif")

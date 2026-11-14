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


def _cached_windows(out_tif):
    return [f for f in sorted(out_tif.parent.glob("window_*.tif")) if f != out_tif]


def read_window(path, bounds, out_tif, reuse=False):
    """Read `bounds` (source CRS) from `path` as float32 with NaN nodata; cache as GeoTIFF.

    reuse=True: pixels already present in another cached window_*.tif next to `out_tif`
    (same CRS, same pixel size, integer pixel offset) are copied from disk; only the
    missing L-shaped remainder is read remotely."""
    from rasterio.windows import Window
    out_tif = Path(out_tif)
    if out_tif.exists():
        with rasterio.open(out_tif) as d:
            return d.read(1), d.transform, d.crs
    with rasterio.open(path) as d:
        w = from_bounds(*bounds, d.transform).round_offsets().round_lengths()
        tr, crs, nodata = d.window_transform(w), d.crs, d.nodata
    H, W = int(w.height), int(w.width)
    a = np.full((H, W), np.nan, np.float32)
    have = np.zeros((H, W), bool)
    if reuse:
        for f in _cached_windows(out_tif):
            with rasterio.open(f) as c:
                if c.crs != crs or c.transform.a != tr.a or c.transform.e != tr.e:
                    continue
                dc, dr = (c.transform.c - tr.c) / tr.a, (c.transform.f - tr.f) / tr.e
                if abs(dc - round(dc)) > 1e-6 or abs(dr - round(dr)) > 1e-6:
                    continue
                dc, dr = int(round(dc)), int(round(dr))        # cached origin in new-window pixels
                r0, r1 = max(0, dr), min(H, dr + c.height)
                c0, c1 = max(0, dc), min(W, dc + c.width)
                if r1 <= r0 or c1 <= c0:
                    continue
                sub = c.read(1, window=Window(c0 - dc, r0 - dr, c1 - c0, r1 - r0))
                m = ~have[r0:r1, c0:c1]
                a[r0:r1, c0:c1][m] = sub[m]
                have[r0:r1, c0:c1] = True
                print(f"  reused {int(have.sum())/have.size:.1%} of window from {f.name}", flush=True)
    if not have.all():
        # remote rectangles for what is missing: rows with any gap, split into full-width / east strips
        miss = ~have
        rows = np.where(miss.any(1))[0]
        rects, i = [], 0
        while i < len(rows):                       # contiguous row runs with the same missing column span
            j = i
            cols = np.where(miss[rows[i]])[0]
            span = (int(cols[0]), int(cols[-1]) + 1)
            while j + 1 < len(rows) and rows[j + 1] == rows[j] + 1:
                cc = np.where(miss[rows[j + 1]])[0]
                if (int(cc[0]), int(cc[-1]) + 1) != span:
                    break
                j += 1
            rects.append((int(rows[i]), int(rows[j]) + 1, *span))
            i = j + 1
        print(f"  remote fetch of {len(rects)} rectangle(s), "
              f"{miss.sum()/miss.size:.1%} of window", flush=True)
        for (ra, rb, ca, cb) in rects:
            sub = Window(w.col_off + ca, w.row_off + ra, cb - ca, rb - ra)
            blk = _read_chunked(path, sub, out_tif.with_suffix(f".chunks_{ra}_{rb}_{ca}_{cb}"))
            a[ra:rb, ca:cb][miss[ra:rb, ca:cb]] = blk[miss[ra:rb, ca:cb]]
        a[a == nodata] = np.nan
        a[~np.isfinite(a)] = np.nan
    out_tif.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_tif.with_suffix(".part")
    with rasterio.open(tmp, "w", driver="GTiff", width=W, height=H, count=1,
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
    return read_window("/vsicurl/" + HI_URL, b, f"data/raw/hirise/window_{lat0}_{lon0}_{int(half)}.tif", reuse=True)


def get_ctx(lat0, lon0, half):
    b = src_window_bounds(lat0, lon0, half, pad=1.3)
    return read_window(CTX_PATH, b, f"data/raw/ctx/window_{lat0}_{lon0}_{int(half)}.tif")

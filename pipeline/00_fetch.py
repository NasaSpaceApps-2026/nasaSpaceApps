"""Stream A step 0: windowed remote read of the USGS HiRISE products for the site.

Reads only the site window (plus padding) from the 1.8 GB DTM and 7.4 GB ortho via
GDAL /vsicurl/ range requests, writes them to data/raw/ (gitignored), and appends a
record to data/SOURCES.md. Centre and size come from pipeline/site.py.

Run: .venv/bin/python pipeline/00_fetch.py [dtm|ortho|all]
"""
import datetime
import os

os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "8")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "3")
os.environ.setdefault("GDAL_HTTP_TIMEOUT", "60")
import math
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window

sys.path.insert(0, str(Path(__file__).parent))
from site_cfg import LAT0, LON0, R, SIZE  # noqa: E402

BASE = "https://planetarymaps.usgs.gov/mosaic/mars2020_trn/HiRISE/"
PRODUCTS = {
    "dtm": ("JEZero HiRISE DTM 1 m (DOI 10.5066/P9REJ9JN)",
            "JEZ_hirise_soc_006_DTM_MOLAtopography_DeltaGeoid_1m_Eqc_latTs0_lon0_blend40.tif"),
    "ortho": ("Jezero HiRISE orthomosaic 25 cm (DOI 10.5066/P9QJDP48)",
              "JEZ_hirise_soc_006_orthoMosaic_25cm_Eqc_latTs0_lon0_first.tif"),
}
PAD = 200.0  # metres of margin so the warp has data past the square's edge
ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"


def fetch(key):
    title, fname = PRODUCTS[key]
    url = BASE + fname
    cx, cy = R * math.radians(LON0), R * math.radians(LAT0)  # source is eqc, lat_ts 0
    half = SIZE / 2 + PAD
    out = RAW / f"{key}_window.tif"
    RAW.mkdir(parents=True, exist_ok=True)
    with rasterio.open("/vsicurl/" + url) as src:
        res = src.res[0]
        # Source is eqc with lat_ts=0, so east-west is stretched by 1/cos(lat0) vs ground metres.
        halfx = half / math.cos(math.radians(LAT0))
        c0, r0 = ~src.transform * (cx - halfx, cy + half)
        n = int(round(2 * half / res))          # rows
        nx = int(round(2 * halfx / res))        # cols
        win = Window(int(round(c0)), int(round(r0)), nx, n)
        assert win.col_off >= 0 and win.row_off >= 0 and win.col_off + nx <= src.width \
            and win.row_off + n <= src.height, "site window not inside product footprint"
        # Ortho is 25 cm; read it decimated to 1 m (every 4th row) to keep the transfer ~300 MB.
        f = int(round(1.0 / res)) if key == "ortho" else 1
        m, mx = n // f, nx // f
        data = src.read(1, window=win, out_shape=(m, mx))
        tr = src.window_transform(win) * rasterio.Affine.scale(nx / mx, n / m)
        profile = src.profile.copy()
        profile.update(width=mx, height=m, transform=tr,
                       tiled=True, blockxsize=256, blockysize=256, compress="deflate",
                       predictor=3 if data.dtype.kind == "f" else 2)
        nod = src.nodata
        valid = float(np.mean(~np.isclose(data, nod))) if nod is not None else 1.0
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(data, 1)
    rec = (f"\n## {title}\n- URL: {url}\n- Fetched: {datetime.date.today()} (windowed, "
           f"{mx}x{m} px around lat {LAT0}, lon {LON0})\n- Window file: {out.name}, "
           f"{out.stat().st_size / 1e6:.0f} MB, valid fraction {valid:.3f}\n"
           f"- Citation: USGS Astrogeology Science Center / NASA MRO HiRISE; licence CC0 per USGS data release\n")
    with open(ROOT / "data" / "SOURCES.md", "a") as f:
        f.write(rec)
    print(key, "ok", mx, "x", m, "px, valid", round(valid, 3), "->", out)


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    for k in (PRODUCTS if which == "all" else [which]):
        fetch(k)

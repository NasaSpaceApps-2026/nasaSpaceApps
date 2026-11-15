"""Windowed read of the USGS HiRISE Jezero orthomosaic (25 cm, tiled LZW, orthographic) over our 8 km site,
decimated to 1 m (mean of 4x4), written to <out> as uint8 (nodata 0). Approach follows pipeline/00_fetch.py;
run with the repo .venv:  python stack/fetch_ortho.py <out.tif>   (about 1 GB of range requests, several minutes)."""
import math, sys, os
from concurrent.futures import ThreadPoolExecutor
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "8"); os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "3")
os.environ.setdefault("GDAL_HTTP_TIMEOUT", "120"); os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
import numpy as np, rasterio
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.windows import Window

URL = ("/vsicurl/https://planetarymaps.usgs.gov/mosaic/mars2020_trn/HiRISE/"
       "JEZ_hirise_soc_007_orthoMosaic_25cm_Ortho_blend120.tif")
R, LAT0, LON0, SIZE, PAD = 3396190.0, 18.452, 77.418, 8192.0, 150.0
out = sys.argv[1]
site = f"+proj=eqc +lat_ts={LAT0} +lat_0=0 +lon_0={LON0} +x_0=0 +y_0={-R*math.radians(LAT0):.3f} +R={R} +units=m +no_defs"
with rasterio.open(URL) as src:
    t = Transformer.from_crs(site, src.crs, always_xy=True)
    h = SIZE / 2 + PAD
    pts = [t.transform(x, y) for x in np.linspace(-h, h, 9) for y in (-h, h)] + \
          [t.transform(x, y) for y in np.linspace(-h, h, 9) for x in (-h, h)]
    xs, ys = zip(*pts)
    c0, r0 = ~src.transform * (min(xs), max(ys))
    c1, r1 = ~src.transform * (max(xs), min(ys))
    c0, r0 = int(c0) // 4 * 4, int(r0) // 4 * 4
    nx, ny = (int(c1) - c0) // 4, (int(r1) - r0) // 4
    print("window", c0, r0, nx * 4, ny * 4, flush=True)
    data = np.zeros((ny, nx), np.uint8)
    band = 128  # output rows per request (512 source rows)

    def job(k):
        y = k * band; hh = min(band, ny - y)
        with rasterio.open(URL) as s:
            data[y:y + hh] = s.read(1, window=Window(c0, r0 + y * 4, nx * 4, hh * 4), out_shape=(hh, nx),
                                    resampling=Resampling.average)
        return k
    with ThreadPoolExecutor(6) as ex:
        for i, _ in enumerate(ex.map(job, range((ny + band - 1) // band))):
            print("band", i, flush=True)
    tr = src.transform * rasterio.Affine.translation(c0, r0) * rasterio.Affine.scale(4, 4)
    prof = dict(driver="GTiff", height=ny, width=nx, count=1, dtype="uint8", crs=src.crs, transform=tr,
                nodata=0, tiled=True, blockxsize=256, blockysize=256, compress="deflate")
with rasterio.open(out, "w", **prof) as d:
    d.write(data, 1)
print("ok", out, (data > 0).mean())

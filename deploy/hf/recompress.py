"""Lossless DEFLATE recompression of every GeoTIFF under a directory (in place). Usage: recompress.py DIR"""
import sys, os
from pathlib import Path
import rasterio

for p in sorted(Path(sys.argv[1]).rglob("*.tif")):
    with rasterio.open(p) as src:
        prof = src.profile.copy()
        data = src.read()
        tags = src.tags()
        descs = src.descriptions
    pred = 3 if data.dtype.kind == "f" else 2
    prof.update(driver="GTiff", compress="deflate", predictor=pred, zlevel=9, tiled=True, blockxsize=256, blockysize=256)
    prof.pop("photometric", None)
    tmp = p.with_suffix(".tmp.tif")
    with rasterio.open(tmp, "w", **prof) as dst:
        dst.write(data)
        dst.update_tags(**tags)
        for i, d in enumerate(descs, 1):
            if d: dst.set_band_description(i, d)
    before = p.stat().st_size
    os.replace(tmp, p)
    print(f"{p.name}: {before/1e6:.1f} -> {p.stat().st_size/1e6:.1f} MB", flush=True)

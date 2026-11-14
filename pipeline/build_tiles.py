"""Stream A steps 02-04 (compact): warp the cached HiRISE windows into the site frame,
derive slope/roughness/hazard, and cut contract-2 tiles plus contract-3 rasters.

Inputs : data/raw/dtm_window.tif (and ortho_window.tif if present)
Outputs: data/build/{manifest.json, tiles/, analysis/, data/}  (served as /tiles, /data)

Site frame: eqc with lat_ts=lat0, then shifted so the origin is the region centre.
Run: .venv/bin/python pipeline/build_tiles.py
"""
import json
import math
import sys
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio import Affine
from rasterio.crs import CRS as RCRS
from rasterio.warp import Resampling, reproject

sys.path.insert(0, str(Path(__file__).parent))
from site_cfg import CRS, LAT0, LON0, R, SIZE  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RAW, OUT = ROOT / "data" / "raw", ROOT / "data" / "build"
MAX_LEVEL = 5
HEIGHT_PX, IMG_PX = 257, 512
HAZARD = (15.0, 25.0)  # caution, no-go (astronaut_walk profile; Stream C owns final values)


def warp(src_path, res, resampling):
    """Reproject into the site frame on a grid of `res` metres covering the square."""
    n = int(round(SIZE / res))
    dst = np.full((n, n), np.nan, dtype=np.float32)
    transform = Affine(res, 0, -SIZE / 2, 0, -res, SIZE / 2)
    with rasterio.open(src_path) as s:
        data = s.read(1).astype(np.float32)
        nod = s.nodata
        if nod is not None:
            data[np.isclose(data, nod) | (data < -1e30)] = np.nan
        reproject(data, dst, src_transform=s.transform, src_crs=s.crs, dst_transform=transform,
                  dst_crs=RCRS.from_string(CRS), resampling=resampling,
                  src_nodata=np.nan, dst_nodata=np.nan)
    return dst, transform


def fill_gaps(a):
    """Nearest-neighbour fill of NaNs (rare). Returns (filled, mask_of_filled)."""
    mask = np.isnan(a)
    if not mask.any():
        return a, mask
    from scipy import ndimage
    idx = ndimage.distance_transform_edt(mask, return_distances=False, return_indices=True)
    return a[tuple(idx)], mask


def encode_rgb(e):
    v = np.rint((e + 10000.0) / 0.01).astype(np.int64)
    return np.stack([(v >> 16) & 255, (v >> 8) & 255, v & 255], -1).astype(np.uint8)


def height_tiles(dem1):
    """dem1: 1 m grid, (SIZE x SIZE). Level 5 posts are 1 m; coarser levels subsample the
    same grid at the post spacing so shared edges are bit-identical."""
    n = dem1.shape[0]
    # posts lie on pixel corners: use (n+1) posts by padding one edge row/col
    p = np.pad(dem1, ((0, 1), (0, 1)), mode="edge")
    for z in range(MAX_LEVEL + 1):
        tiles = 2 ** z
        stride = 2 ** (MAX_LEVEL - z)  # source posts between output posts
        for ty in range(tiles):
            for tx in range(tiles):
                r0, c0 = ty * 256 * stride, tx * 256 * stride
                t = p[r0:r0 + 256 * stride + 1:stride, c0:c0 + 256 * stride + 1:stride]
                path = OUT / "tiles" / "height" / str(z) / str(tx) / f"{ty}.png"
                path.parent.mkdir(parents=True, exist_ok=True)
                Image.fromarray(encode_rgb(t), "RGB").save(path, optimize=False)


def slope_deg(z, step):
    gy, gx = np.gradient(z, step)
    return np.degrees(np.arctan(np.hypot(gx, gy)))


def hillshade(z, step):
    gy, gx = np.gradient(z, step)
    az, alt = math.radians(315), math.radians(45)
    sl = np.arctan(np.hypot(gx, gy))
    asp = np.arctan2(-gy, -gx)
    return np.clip(np.sin(alt) * np.cos(sl) + np.cos(alt) * np.sin(sl) * np.cos(az - asp), 0, 1)


def image_tiles(name, rgba, max_px_level=MAX_LEVEL):
    """rgba: (H,W,4) uint8 covering the square. Resampled to 512*2**level per level."""
    full = Image.fromarray(rgba, "RGBA")
    for lvl in range(max_px_level + 1):
        side = IMG_PX * 2 ** lvl
        img = full.resize((side, side), Image.LANCZOS)
        for ty in range(2 ** lvl):
            for tx in range(2 ** lvl):
                tile = img.crop((tx * IMG_PX, ty * IMG_PX, (tx + 1) * IMG_PX, (ty + 1) * IMG_PX))
                path = OUT / "tiles" / name / str(lvl) / str(tx) / f"{ty}.webp"
                path.parent.mkdir(parents=True, exist_ok=True)
                tile.save(path, "WEBP", quality=80)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    dem1, _ = warp(RAW / "dtm_window.tif", 1.0, Resampling.cubic)
    dem1, filled1 = fill_gaps(dem1)
    print("dem 1 m", dem1.shape, "elev", float(dem1.min()), float(dem1.max()), "filled", int(filled1.sum()))
    height_tiles(dem1)

    # analysis grids at 2 m (contract 3), average 2x2 from the 1 m grid
    n2 = dem1.shape[0] // 2
    dem2 = dem1[:n2 * 2, :n2 * 2].reshape(n2, 2, n2, 2).mean(axis=(1, 3)).astype(np.float32)
    f2 = filled1[:n2 * 2, :n2 * 2].reshape(n2, 2, n2, 2).any(axis=(1, 3))
    slope2 = slope_deg(dem2, 2.0).astype(np.float32)
    win = np.lib.stride_tricks.sliding_window_view(np.pad(dem2, 2, mode="edge"), (5, 5))
    rough2 = win.std(axis=(-1, -2)).astype(np.float32)
    meta = dict(driver="GTiff", height=n2, width=n2, count=1, dtype="float32", crs=CRS,
                transform=Affine(2.0, 0, -SIZE / 2, 0, -2.0, SIZE / 2), compress="deflate")
    (OUT / "analysis").mkdir(exist_ok=True)
    for nm, arr in [("dem", dem2), ("slope", slope2), ("rough", rough2), ("filled", f2.astype(np.float32))]:
        with rasterio.open(OUT / "analysis" / f"{nm}.tif", "w", **meta) as d:
            d.write(arr, 1)

    # overlays at 1 m (upsampled to 512 px tiles); hazard treats filled cells as unknown
    slope1 = slope_deg(dem1, 1.0)
    cls = np.zeros(slope1.shape, np.uint8)
    cls[slope1 >= HAZARD[0]] = 1
    cls[slope1 >= HAZARD[1]] = 2
    cls[filled1] = 3
    colors = np.array([[40, 180, 80, 70], [240, 180, 30, 150], [220, 40, 40, 180], [120, 120, 120, 160]], np.uint8)
    image_tiles("hazard", colors[cls])
    s = np.clip(slope1 / 30, 0, 1)
    image_tiles("slope", np.dstack([(255 * s).astype(np.uint8), (255 * (1 - s)).astype(np.uint8),
                                    np.full(s.shape, 60, np.uint8), np.full(s.shape, 140, np.uint8)]))

    ortho_src = RAW / "ortho_window.tif"
    layers = []
    if ortho_src.exists():
        o, _ = warp(ortho_src, 1.0, Resampling.lanczos)
        o = np.nan_to_num(o, nan=0.0)
        o8 = np.clip(o, 0, 255).astype(np.uint8)
        image_tiles("ortho", np.dstack([o8, o8, o8, np.full_like(o8, 255)]))
        layers.append({"id": "ortho", "title": "HiRISE orthomosaic (greyscale, 1 m here)", "mission": "MRO",
                       "instrument": "HiRISE", "max_level": MAX_LEVEL, "kind": "base",
                       "credit": "NASA/JPL/UArizona/USGS", "url": "https://doi.org/10.5066/P9QJDP48"})
    else:  # fall back to a hillshade so the viewer has a base layer
        h = (hillshade(dem1, 1.0) * 255).astype(np.uint8)
        image_tiles("ortho", np.dstack([h, h, h, np.full_like(h, 255)]))
        layers.append({"id": "ortho", "title": "Hillshade of HiRISE DTM", "mission": "MRO", "instrument": "HiRISE DTM",
                       "max_level": MAX_LEVEL, "kind": "base", "credit": "NASA/JPL/UArizona/USGS",
                       "url": "https://doi.org/10.5066/P9REJ9JN"})
    layers += [
        {"id": "slope", "title": "Slope", "mission": "MRO", "instrument": "HiRISE DTM", "max_level": MAX_LEVEL,
         "kind": "overlay", "credit": "NASA/JPL/UArizona/USGS", "url": "https://doi.org/10.5066/P9REJ9JN",
         "legend": [[0, "flat"], [15, "steep"], [30, "very steep"]]},
        {"id": "hazard", "title": "Slope hazard (assumed thresholds)", "mission": "MRO", "instrument": "HiRISE DTM",
         "max_level": MAX_LEVEL, "kind": "overlay", "credit": "NASA/JPL/UArizona/USGS",
         "url": "https://doi.org/10.5066/P9REJ9JN",
         "legend": [[0, "safe"], [HAZARD[0], "caution"], [HAZARD[1], "no-go"], [None, "unknown"]]},
    ]
    manifest = {"site": "jezero", "lat0": LAT0, "lon0": LON0, "radius_m": int(R), "size_m": int(SIZE),
                "elev_min": math.floor(float(dem1.min())), "elev_max": math.ceil(float(dem1.max())),
                "height": {"max_level": MAX_LEVEL, "tile_px": HEIGHT_PX}, "layers": layers,
                "vectors": {"pois": "/data/pois.geojson", "traverse": "/data/traverse.geojson"}}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print("done ->", OUT)


if __name__ == "__main__":
    main()

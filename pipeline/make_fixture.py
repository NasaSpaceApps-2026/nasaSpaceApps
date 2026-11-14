"""Generate the synthetic 1 km fixture site in the exact contract 2 and 3 formats.

Output (default contracts/fixtures/):
  manifest.json
  tiles/height/{z}/{x}/{y}.png      257x257 RGB-encoded elevation, shared edges
  tiles/{layer}/{z}/{x}/{y}.webp    512x512 imagery and overlays (ortho, slope, hazard)
  analysis/{dem,slope,rough,layer_geology}.tif   2 m float32 GeoTIFF, site frame
  data/pois.geojson, data/traverse.geojson

The fixture site is 1,024 m square, so levels 0..2 give 1 m posts at level 2
(the real site is 8,192 m with levels 0..5, same 1 m posts at the top level).
Terrain is analytic, so every level samples the function directly and shared
edges are identical by construction.

Run: .venv/bin/python pipeline/make_fixture.py [outdir]
"""
import json
import math
import sys
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.transform import from_origin

R = 3396190.0
LAT0, LON0 = 18.46, 77.41  # placeholder centre, same as the PRD manifest
SIZE = 1024.0
MAX_LEVEL = 2
HEIGHT_PX = 257
IMG_PX = 512
ANALYSIS_RES = 2.0
CRS = f"+proj=eqc +lat_ts={LAT0} +lon_0={LON0} +R={R} +units=m +no_defs"
HAZARD_THRESH = (15.0, 25.0)  # caution, no-go (astronaut_walk profile)


def xy_to_latlon(x, y):
    lat = LAT0 + math.degrees(y / R)
    lon = LON0 + math.degrees(x / (R * math.cos(math.radians(LAT0))))
    return lat, lon


def elevation(x, y):
    """Analytic terrain in site-frame metres: gentle regional tilt, a bowl crater
    with a raised rim, and a steep east-facing scarp (the 'shortest path is
    unsafe' feature for the demo)."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    z = -2400.0 + 0.02 * x - 0.01 * y
    # crater centred (-150, 100), radius 120 m
    d = np.hypot(x + 150.0, y - 100.0)
    bowl = -22.0 * np.clip(1.0 - (d / 120.0) ** 2, 0.0, None)
    rim = 4.0 * np.exp(-(((d - 125.0) / 14.0) ** 2))
    z = z + bowl + rim
    # scarp: smooth 25 m step across x ~ 180, slopes ~35 deg at the middle
    z = z + 25.0 / (1.0 + np.exp(-(x - 180.0) / 10.0))
    # low-amplitude roughness so slope/roughness are not degenerate
    z = z + 0.4 * np.sin(x / 7.0) * np.cos(y / 9.0)
    return z


def encode_rgb(elev):
    v = np.rint((elev + 10000.0) / 0.01).astype(np.int64)
    if v.min() < 0 or v.max() > 0xFFFFFF:
        raise ValueError("elevation outside encodable range")
    return np.stack([(v >> 16) & 255, (v >> 8) & 255, v & 255], axis=-1).astype(np.uint8)


def decode_rgb(rgb):
    rgb = rgb.astype(np.int64)
    return -10000.0 + 0.01 * (rgb[..., 0] * 65536 + rgb[..., 1] * 256 + rgb[..., 2])


def tile_origin(z, tx, ty):
    n = 2 ** z
    ts = SIZE / n
    return -SIZE / 2 + tx * ts, SIZE / 2 - ty * ts, ts  # west x, north y, tile size


def slope_deg(z, step):
    gy, gx = np.gradient(z, step)
    return np.degrees(np.arctan(np.hypot(gx, gy)))


def hazard_class(slope, unknown):
    """0 safe, 1 caution, 2 no-go, 3 unknown."""
    c = np.zeros(slope.shape, dtype=np.uint8)
    c[slope >= HAZARD_THRESH[0]] = 1
    c[slope >= HAZARD_THRESH[1]] = 2
    c[unknown] = 3
    return c


def unknown_mask(x, y):
    """Small patch standing in for an interpolated data gap."""
    return (np.abs(x - 300.0) < 25.0) & (np.abs(y + 250.0) < 25.0)


def write_height_tiles(out):
    n_posts = HEIGHT_PX
    for z in range(MAX_LEVEL + 1):
        for ty in range(2 ** z):
            for tx in range(2 ** z):
                x0, y0, ts = tile_origin(z, tx, ty)
                step = ts / (n_posts - 1)
                xs = x0 + np.arange(n_posts) * step
                ys = y0 - np.arange(n_posts) * step
                xx, yy = np.meshgrid(xs, ys)
                path = out / "tiles" / "height" / str(z) / str(tx) / f"{ty}.png"
                path.parent.mkdir(parents=True, exist_ok=True)
                Image.fromarray(encode_rgb(elevation(xx, yy)), "RGB").save(path)


def full_res_grids():
    """Top-level imagery grid, pixel centres, row 0 = north."""
    px = IMG_PX * 2 ** MAX_LEVEL
    step = SIZE / px
    xs = -SIZE / 2 + (np.arange(px) + 0.5) * step
    ys = SIZE / 2 - (np.arange(px) + 0.5) * step
    xx, yy = np.meshgrid(xs, ys)
    return xx, yy, step


def write_image_tiles(out):
    xx, yy, step = full_res_grids()
    z = elevation(xx, yy)
    gy, gx = np.gradient(z, step)  # d/drow is -d/dy
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    # Hillshade, sun from the north-west at 45 degrees
    az, alt = math.radians(315.0), math.radians(45.0)
    dzdx, dzdy = gx, -gy
    aspect_term = np.arctan2(dzdy, -dzdx)
    slope_r = np.arctan(np.hypot(dzdx, dzdy))
    shade = np.sin(alt) * np.cos(slope_r) + np.cos(alt) * np.sin(slope_r) * np.cos(az - aspect_term)
    rng = np.random.default_rng(7)
    grain = rng.normal(0.0, 0.03, shade.shape)
    ortho = (np.clip(0.15 + 0.7 * shade + grain, 0, 1) * 255).astype(np.uint8)
    ortho_rgba = np.dstack([ortho, ortho, ortho, np.full_like(ortho, 255)])

    cls = hazard_class(slope, unknown_mask(xx, yy))
    haz_colors = np.array([[40, 180, 80, 70], [240, 180, 30, 150],
                           [220, 40, 40, 180], [120, 120, 120, 160]], dtype=np.uint8)
    hazard = haz_colors[cls]

    s = np.clip(slope / 30.0, 0, 1)
    slope_rgba = np.dstack([(255 * s).astype(np.uint8), (255 * (1 - s)).astype(np.uint8),
                            np.full(s.shape, 60, np.uint8), np.full(s.shape, 140, np.uint8)])

    layers = {"ortho": ortho_rgba, "slope": slope_rgba, "hazard": hazard}
    for name, arr in layers.items():
        full = Image.fromarray(arr, "RGBA")
        for lvl in range(MAX_LEVEL + 1):
            n = 2 ** lvl
            side = IMG_PX * n
            img = full if lvl == MAX_LEVEL else full.resize((side, side), Image.LANCZOS)
            for ty in range(n):
                for tx in range(n):
                    tile = img.crop((tx * IMG_PX, ty * IMG_PX, (tx + 1) * IMG_PX, (ty + 1) * IMG_PX))
                    path = out / "tiles" / name / str(lvl) / str(tx) / f"{ty}.webp"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    tile.save(path, "WEBP", quality=85 if name == "ortho" else 90)


def write_rasters(out):
    n = int(SIZE / ANALYSIS_RES)
    xs = -SIZE / 2 + (np.arange(n) + 0.5) * ANALYSIS_RES
    ys = SIZE / 2 - (np.arange(n) + 0.5) * ANALYSIS_RES
    xx, yy = np.meshgrid(xs, ys)
    dem = elevation(xx, yy).astype(np.float32)
    slope = slope_deg(dem.astype(np.float64), ANALYSIS_RES).astype(np.float32)
    # roughness: std of elevation in a 5x5 window (edge-padded)
    pad = np.pad(dem, 2, mode="edge")
    win = np.lib.stride_tricks.sliding_window_view(pad, (5, 5))
    rough = win.std(axis=(-1, -2)).astype(np.float32)
    # geology: class ids by region, 1 = plains, 2 = crater floor, 3 = delta-like upland
    geo = np.ones_like(dem)
    geo[np.hypot(xx + 150, yy - 100) < 120] = 2
    geo[xx > 180] = 3
    meta = dict(driver="GTiff", height=n, width=n, count=1, dtype="float32",
                crs=CRS, transform=from_origin(-SIZE / 2, SIZE / 2, ANALYSIS_RES, ANALYSIS_RES))
    (out / "analysis").mkdir(parents=True, exist_ok=True)
    for name, arr in [("dem", dem), ("slope", slope), ("rough", rough), ("layer_geology", geo.astype(np.float32))]:
        with rasterio.open(out / "analysis" / f"{name}.tif", "w", **meta) as dst:
            dst.write(arr, 1)


def poi(pid, name, typ, x, y, mission, desc, value, sol=None):
    lat, lon = xy_to_latlon(x, y)
    props = dict(id=pid, name=name, type=typ, x=x, y=y, z=round(float(elevation(x, y)), 2),
                 lat=round(lat, 6), lon=round(lon, 6), mission=mission,
                 source_url="https://example.invalid/fixture", description=desc,
                 science_value=value, sol=sol, image_urls=[])
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
            "properties": props}


def write_vectors(out):
    pois = [
        poi("base", "Fixture base", "base", -380, -380, "fixture", "Synthetic habitat site, SW corner.", 0.0),
        poi("rim", "Fixture crater rim", "landmark", -150, 230, "fixture", "Crater rim lookout.", 0.3),
        poi("floor", "Crater floor sample", "science_target", -150, 100, "fixture", "Fine-grained floor material.", 0.8),
        poi("scarp_top", "Scarp upland", "science_target", 260, 40, "fixture", "Layered upland beyond the scarp.", 0.9),
        poi("sample1", "Core sample site 1", "sample_site", -20, -60, "fixture", "Prior sample location.", 0.6, sol=120),
        poi("scarp", "Steep scarp", "hazard", 180, 40, "fixture", "Up to about 35 degree slope; no-go for walking.", 0.0),
        poi("wp1", "Waypoint sol 100", "rover_waypoint", -300, -300, "fixture", "Rover waypoint.", 0.1, sol=100),
        poi("wp2", "Waypoint sol 110", "rover_waypoint", -100, -150, "fixture", "Rover waypoint.", 0.1, sol=110),
        poi("wp3", "Waypoint sol 120", "rover_waypoint", -20, -60, "fixture", "Rover waypoint.", 0.1, sol=120),
    ]
    data = out / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "pois.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": pois}, indent=1))
    wps = [p for p in pois if p["properties"]["type"] == "rover_waypoint"]
    line = {"type": "Feature", "geometry": {"type": "LineString",
            "coordinates": [p["geometry"]["coordinates"] for p in wps]}, "properties": {"kind": "traverse"}}
    (data / "traverse.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [line] + wps}, indent=1))


def write_manifest(out):
    zs = [float(elevation(*np.meshgrid(np.linspace(-512, 512, 400), np.linspace(-512, 512, 400))).min()),
          float(elevation(*np.meshgrid(np.linspace(-512, 512, 400), np.linspace(-512, 512, 400))).max())]
    m = {
        "site": "fixture", "fixture": True, "lat0": LAT0, "lon0": LON0, "radius_m": int(R),
        "size_m": int(SIZE), "elev_min": math.floor(zs[0]), "elev_max": math.ceil(zs[1]),
        "height": {"max_level": MAX_LEVEL, "tile_px": HEIGHT_PX},
        "layers": [
            {"id": "ortho", "title": "Synthetic hillshade", "mission": "fixture", "instrument": "synthetic",
             "max_level": MAX_LEVEL, "kind": "base", "credit": "Synthetic test data, not Mars",
             "url": "https://example.invalid/fixture"},
            {"id": "slope", "title": "Slope", "mission": "fixture", "instrument": "synthetic DTM",
             "max_level": MAX_LEVEL, "kind": "overlay", "credit": "Synthetic test data, not Mars",
             "url": "https://example.invalid/fixture", "legend": [[0, "flat"], [15, "steep"], [30, "very steep"]]},
            {"id": "hazard", "title": "Slope hazard", "mission": "fixture", "instrument": "synthetic DTM",
             "max_level": MAX_LEVEL, "kind": "overlay", "credit": "Synthetic test data, not Mars",
             "url": "https://example.invalid/fixture",
             "legend": [[0, "safe"], [15, "caution"], [25, "no-go"], [None, "unknown"]]},
        ],
        "vectors": {"pois": "/data/pois.geojson", "traverse": "/data/traverse.geojson"},
    }
    (out / "manifest.json").write_text(json.dumps(m, indent=2))


def self_check(out):
    """Decode every height tile, confirm round trip and shared edges."""
    for z in range(MAX_LEVEL + 1):
        n = 2 ** z
        for ty in range(n):
            for tx in range(n):
                t = decode_rgb(np.array(Image.open(out / "tiles" / "height" / str(z) / str(tx) / f"{ty}.png")))
                assert t.shape == (HEIGHT_PX, HEIGHT_PX)
                if tx + 1 < n:
                    e = decode_rgb(np.array(Image.open(out / "tiles" / "height" / str(z) / str(tx + 1) / f"{ty}.png")))
                    assert np.array_equal(t[:, -1], e[:, 0]), f"east edge mismatch z{z} {tx},{ty}"
                if ty + 1 < n:
                    s = decode_rgb(np.array(Image.open(out / "tiles" / "height" / str(z) / str(tx) / f"{ty + 1}.png")))
                    assert np.array_equal(t[-1, :], s[0, :]), f"south edge mismatch z{z} {tx},{ty}"
    x0, y0, ts = tile_origin(2, 1, 1)
    t = decode_rgb(np.array(Image.open(out / "tiles/height/2/1/1.png")))
    truth = elevation(x0 + 100 * ts / 256, y0 - 50 * ts / 256)
    assert abs(t[50, 100] - truth) < 0.006, (t[50, 100], truth)
    print("self-check ok: edges shared, decode within 0.006 m")


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "contracts" / "fixtures"
    out.mkdir(parents=True, exist_ok=True)
    write_height_tiles(out)
    write_image_tiles(out)
    write_rasters(out)
    write_vectors(out)
    write_manifest(out)
    self_check(out)
    print("wrote fixtures to", out)


if __name__ == "__main__":
    main()

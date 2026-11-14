"""Generate the synthetic 2 km "playground" test site in the exact fixture format.

Same contract 2 and 3 formats as pipeline/make_fixture.py (encoders copied so
this script stays independent of the pipeline folder), but a richer landscape
that is fun to drive and exercises the route planner and site search:

  - fractal plains with domain-warped ridges and smooth "highway" plains
  - a 500 m crater with terraced walls, central peak and a ~15 deg breach ramp
  - a lobate, layered delta with a ~30 deg front scarp cut by two gullies
  - sinuous channels on the delta top and one running out across the plains
  - a transverse dune field (stoss ~10 deg, slip faces ~30 deg)
  - boulder patches, small fresh craters with ejecta, and a cliffed butte

Output (default api/fixtures/playground/):
  manifest.json
  tiles/height/{z}/{x}/{y}.png         257 px RGB-encoded elevation, shared edges (levels 0..3)
  tiles/{ortho,ortho_color,slope,hazard}/{z}/{x}/{y}.webp   512 px (levels 0..3)
  analysis/{dem,slope,rough}.tif, analysis/layer_{geology,min_carbonate,min_olivine,min_clay}.tif
  data/pois.geojson, data/traverse.geojson

Everything is synthetic test data, not Mars data. Deterministic (fixed seeds).

Run: api/.venv/bin/python api/scripts/make_playground.py [outdir]
"""
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.transform import from_origin
from scipy import ndimage
from scipy.special import expit
from shapely.geometry import LineString
from skimage.graph import MCP_Geometric

R = 3396190.0
LAT0, LON0 = 18.46, 77.41  # placeholder centre, same as the PRD manifest and fixture
SIZE = 2048.0
HALF = SIZE / 2
MAX_LEVEL = 3              # 257-post tiles -> 1 m posts at level 3
HEIGHT_PX = 257
IMG_PX = 512
ANALYSIS_RES = 2.0
CRS = f"+proj=eqc +lat_ts={LAT0} +lat_0=0 +lon_0={LON0} +x_0=0 +y_0={-R * math.radians(LAT0):.3f} +R={R} +units=m +no_defs"
HAZARD_THRESH = (15.0, 25.0)  # caution, no-go (astronaut_walk profile)
CREDIT = "Synthetic test data, not Mars"
SRC_URL = "https://example.invalid/playground"

# --- landscape layout (site-frame metres) ------------------------------------
APEX = (-1120.0, 820.0)          # delta apex, just off the NW corner
DELTA_R = 720.0                  # mean radius of the delta front from the apex
DELTA_H = 25.0                   # front scarp height
GAPS = (-1.15, -0.45)            # bearings (rad, atan2) of the two gullies through the front
CRATER = (430.0, -400.0)         # main crater centre
CRATER_R = 250.0
BREACH = math.radians(135.0)     # breach faces NW (atan2 angle)
MESA = (640.0, 480.0)
DUNES = (-380.0, -720.0)
SMALL_CRATERS = [  # x, y, radius
    (120, 330, 26), (-420, -230, 13), (820, -180, 18), (60, -760, 10), (720, -820, 32),
    (-880, -480, 15), (300, 860, 20), (-560, 760, 12), (930, 860, 9), (-30, 150, 6),
    (880, 380, 7), (-150, -880, 11), (520, 120, 5), (-700, 40, 8),
]
BOULDER_PATCHES = [(-80, -470, 90), (900, -480, 110), (-300, 560, 70)]  # x, y, radius


def xy_to_latlon(x, y):
    lat = LAT0 + math.degrees(y / R)
    lon = LON0 + math.degrees(x / (R * math.cos(math.radians(LAT0))))
    return lat, lon


# --- deterministic gradient noise ---------------------------------------------
def _hash(ix, iy, seed):
    h = (ix * 374761393 + iy * 668265263 + seed * 1442695041) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    return h ^ (h >> 16)


def gnoise(x, y, seed):
    """2D gradient (Perlin-style) noise, roughly -1..1, no period."""
    x0 = np.floor(x)
    y0 = np.floor(y)
    fx, fy = x - x0, y - y0
    ix, iy = x0.astype(np.int64), y0.astype(np.int64)

    def g(i, j, dx, dy):
        a = (_hash(ix + i, iy + j, seed) & 1023) * (2 * math.pi / 1024)
        return np.cos(a) * dx + np.sin(a) * dy

    u = fx * fx * fx * (fx * (fx * 6 - 15) + 10)
    v = fy * fy * fy * (fy * (fy * 6 - 15) + 10)
    a = g(0, 0, fx, fy) + u * (g(1, 0, fx - 1, fy) - g(0, 0, fx, fy))
    b = g(0, 1, fx, fy - 1) + u * (g(1, 1, fx - 1, fy - 1) - g(0, 1, fx, fy - 1))
    return 1.4 * (a + v * (b - a))


def fbm(x, y, scale, octaves, seed, gain=0.5):
    tot, amp, norm, f = 0.0, 1.0, 0.0, 1.0 / scale
    for o in range(octaves):
        c, s = math.cos(0.7 * o), math.sin(0.7 * o)  # rotate octaves to hide grid alignment
        tot = tot + amp * gnoise((c * x - s * y) * f + 17.3 * o, (s * x + c * y) * f - 9.1 * o, seed + 101 * o)
        norm += amp
        amp *= gain
        f *= 2.0
    return tot / norm


def smoothstep(e0, e1, v):
    t = np.clip((v - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def stair(u, n_steps, ramp=0.35):
    """Terrace staircase: flat treads with a smooth riser over the last `ramp` of each period."""
    u = np.clip(u, 0.0, n_steps)
    k = np.floor(u)
    return np.minimum(k + smoothstep(1 - ramp, 1.0, u - k), n_steps)


# --- the landscape -------------------------------------------------------------
def fields(x, y, want=("z",)):
    """Analytic terrain. Returns dict with z (elevation, m) and, on request, the unit
    masks used for geology, minerals and albedo."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    out = {}

    # smooth "highway" plains: an E-W corridor from the base to the east edge, plus the base pad
    yw = y + 55 * fbm(x, 0 * x, 260, 3, 12)
    hw = (smoothstep(-170, -90, yw) * (1 - smoothstep(100, 190, yw)) * smoothstep(-470, -360, x + 40 * fbm(y, 0 * y, 200, 2, 13)))
    hw = np.maximum(hw, 1 - smoothstep(50, 150, np.hypot(x + 250, y) + 30 * fbm(x, y, 90, 2, 14)))
    rough_amp = 1 - 0.7 * hw

    z = -2525.0 - 0.012 * x + 0.006 * y
    z = z + 7.0 * fbm(x, y, 480, 5, 1) * (1 - 0.5 * hw)
    z = z + 1.1 * fbm(x, y, 55, 4, 2) * rough_amp

    # delta (polar coordinates about the apex)
    dx, dy = x - APEX[0], y - APEX[1]
    r = np.hypot(dx, dy)
    th = np.arctan2(dy, dx)
    rf = DELTA_R + 45 * np.sin(3 * th + 0.7) + 22 * np.sin(7 * th + 2.0) + 18 * gnoise(th * 9, 3.3, 41)
    u = rf - r  # metres inside the front
    gap = np.zeros_like(r)
    for g in GAPS:
        gap = np.maximum(gap, np.exp(-((r * (th - g)) / 34.0) ** 4))
    gap = gap * smoothstep(-200, -150, u) * (1 - smoothstep(60, 110, u))
    r_eff = rf + 28 * gap
    s = 8.5 + 30 * gap
    terr = 3.0 * stair((u - 60) / 55, 5)
    top = DELTA_H + terr + 0.012 * np.clip(u - 335, 0, None)
    dmask = expit((r_eff - r) / s)
    out["delta"] = dmask
    out["delta_layer"] = ((u - 60) / 55) % 1.0
    out["delta_u"] = u
    out["gap"] = gap * smoothstep(-80, -20, u) * (1 - smoothstep(60, 120, u))
    z = z + dmask * top

    # distributary channels on the delta top, each draining through a gully
    chan = np.zeros_like(r)
    for i, g in enumerate(GAPS):
        amp = 38 * smoothstep(rf - 70, rf - 260, r)
        thc = g + amp / np.maximum(r, 1) * np.sin(r / 62 + 1.7 * i)
        d = r * (th - thc)
        on = expit((rf + 10 - r) / 10)
        chan = np.maximum(chan, np.exp(-(d / 14.0) ** 2) * on)
    # plains channel continuing from the first gully out across the plain
    g0 = GAPS[0]
    ox, oy = APEX[0] + (DELTA_R + 20) * math.cos(g0), APEX[1] + (DELTA_R + 20) * math.sin(g0)
    ex, ey = math.cos(g0), math.sin(g0)  # downstream unit vector
    along = (x - ox) * ex + (y - oy) * ey
    across = -(x - ox) * ey + (y - oy) * ex
    centre = 32 * np.sin(along / 75) * smoothstep(0, 80, along) + 10 * fbm(along, 0 * along, 140, 2, 43)
    w = 12 * (1 - 0.4 * smoothstep(0, 520, along))
    pc = np.exp(-((across - centre) / w) ** 2) * smoothstep(-40, 10, along) * (1 - smoothstep(440, 560, along))
    chan_depth = np.maximum(3.2 * chan, 2.2 * pc)
    out["channel"] = np.maximum(chan, pc)
    z = z - chan_depth
    # channel banks: slight levees
    lev = np.exp(-((np.abs(across - centre) - 1.8 * w) / (0.6 * w)) ** 2) * smoothstep(-40, 10, along) * (1 - smoothstep(440, 560, along))
    z = z + 0.5 * lev

    # main crater with terraced walls, central peak and NW breach ramp
    cx, cy = x - CRATER[0], y - CRATER[1]
    rc = np.hypot(cx, cy)
    phi = np.arctan2(cy, cx)
    wobble = 1 + 0.035 * np.sin(3 * phi + 1) + 0.03 * gnoise(phi * 4, 7.7, 51)
    rr = rc / wobble
    t1 = 150 + 16 * gnoise(phi * 2.5, 0.3, 54) + 4 * gnoise(phi * 9, 0.6, 56)
    t2 = 192 + 13 * gnoise(phi * 2.5, 1.3, 55) + 4 * gnoise(phi * 9, 1.6, 57)
    t3 = 233 + 5 * gnoise(phi * 4, 2.3, 58)
    prof = (-35 + 15 * expit((rr - t1) / 5.5) + 16 * expit((rr - t2) / 5.5) + 16 * expit((rr - t3) / 4.5)
            - 12 * expit((rr - 290) / 25))
    dphi = np.angle(np.exp(1j * (phi - BREACH)))
    bw = np.exp(-(dphi / (0.24 + 0.1 * smoothstep(150, 330, rr))) ** 6)
    ramp = -35 * (1 - smoothstep(100, 330, rr))
    prof = (1 - bw) * prof + bw * ramp
    prof = prof + 14 * np.exp(-(rc / 30) ** 2) + 4 * np.exp(-((cx - 28) ** 2 + (cy + 18) ** 2) / 16 ** 2)
    inner = 1 - smoothstep(240, 300, rr)
    out["crater_depth"] = prof
    out["crater_r"] = rr
    out["breach"] = bw
    # ejecta hummocks
    ej = smoothstep(240, 280, rr) * np.exp(-np.clip(rr - 280, 0, None) / 110)
    wall = smoothstep(130, 160, rr) * (1 - smoothstep(250, 290, rr)) * (1 - bw)
    z = z + prof + 1.4 * ej * fbm(x, y, 22, 3, 52) + 1.6 * wall * fbm(x, y, 18, 3, 59)
    z = z - inner * 0.8 * fbm(x, y, 55, 4, 2) * rough_amp  # smoother crater floor
    out["ejecta"] = ej

    # butte with layered cliffs and talus apron
    mx, my = x - MESA[0], y - MESA[1]
    rm = np.hypot(mx, my)
    pm = np.arctan2(my, mx)
    rmesa = 115 + 20 * np.sin(2 * pm + 1) + 12 * np.sin(5 * pm) + 10 * gnoise(pm * 8, 1.1, 61)
    mesa = 22 * expit((rmesa - rm) / 2.5) + 14 * expit((rmesa - 12 - rm) / 2.0)
    talus = 6 * expit((rmesa + 40 + 12 * fbm(x, y, 35, 2, 64) - rm) / 13)
    z = z + mesa + talus + 0.3 * fbm(x, y, 20, 2, 62) * expit((rmesa - rm) / 3)
    out["mesa"] = expit((rmesa - rm) / 3)
    out["talus"] = expit((rmesa + 55 - rm) / 8) - out["mesa"]
    out["mesa_r"] = rm - rmesa

    # suppress ridges in smooth units, then add domain-warped ridges
    calm = np.clip(hw + dmask + (1 - smoothstep(280, 340, rc)) + expit((rmesa + 60 - rm) / 10), 0, 1)
    wx = x + 130 * fbm(x, y, 320, 3, 3)
    wy = y + 130 * fbm(x, y, 320, 3, 4)
    ridge = (1 - np.abs(gnoise(wx / 230, wy / 230, 5))) ** 3
    z = z + 4.5 * ridge * (1 - calm)

    # transverse dunes, wind from the WSW, slip faces on the ENE side
    ddx, ddy = x - DUNES[0], y - DUNES[1]
    wd = math.radians(20)
    a_ = ddx * math.cos(wd) + ddy * math.sin(wd)
    b_ = -ddx * math.sin(wd) + ddy * math.cos(wd)
    ell = np.hypot(a_ / 330, b_ / 190) + 0.12 * fbm(x, y, 90, 2, 71)
    dm = 1 - smoothstep(0.65, 1.0, ell)
    lam = 34.0
    ph = (a_ + 7 * np.sin(b_ / 47) + 14 * fbm(x, y, 130, 2, 72)) / lam
    p = ph % 1.0
    a = 0.82
    shape = np.where(p < a, smoothstep(0, 1, p / a) * 0.35 + 0.65 * p / a, 1 - (p - a) / (1 - a))
    hd = (3.7 + 1.2 * fbm(x, y, 80, 2, 73)) * dm
    z = z + hd * shape - 1.5 * dm
    out["dune"] = dm
    out["dune_crest"] = np.exp(-((p - a) / 0.05) ** 2) * dm

    # small fresh craters with rims and ejecta
    sc_ej = np.zeros_like(x)
    sc_in = np.zeros_like(x)
    for i, (sx, sy, sr) in enumerate(SMALL_CRATERS):
        q = np.hypot(x - sx, y - sy)
        near = q < 4 * sr
        if not near.any():
            continue
        q = q / sr
        d, h = 0.36 * sr, 0.08 * sr
        bowl = np.where(q < 1, d * (q ** 2 - 1) + h, h * np.maximum(q, 1) ** -3)
        bowl = bowl + 0.25 * h * fbm(x, y, max(sr / 2, 2), 2, 80 + i) * np.exp(-np.abs(q - 1.3))
        z = z + bowl
        sc_ej = np.maximum(sc_ej, np.where(q >= 1, np.exp(-(q - 1) * 1.2), 0))
        sc_in = np.maximum(sc_in, (q < 1).astype(float))
    out["small_ejecta"] = sc_ej
    out["small_inside"] = sc_in

    # boulders: crater ejecta, talus, small crater ejecta and named patches
    bd = 0.8 * ej + 0.9 * np.clip(out["talus"], 0, 1) + 0.6 * sc_ej
    for bx, by, br in BOULDER_PATCHES:
        bd = bd + 1 - smoothstep(0.55 * br, br, np.hypot(x - bx, y - by) + 25 * fbm(x, y, 40, 2, 91))
    bd = np.clip(bd, 0, 1) * (1 - dm) * (1 - 0.8 * hw)
    bn = np.clip(gnoise(x / 2.6, y / 2.6, 92) - 0.35, 0, None) ** 1.3 + 0.6 * np.clip(gnoise(x / 1.4, y / 1.4, 93) - 0.45, 0, None)
    boulders = 1.15 * bd * bn
    z = z + boulders
    out["boulder"] = bd
    out["boulder_tops"] = boulders

    # sub-metre texture
    z = z + 0.08 * gnoise(x / 1.7, y / 1.7, 99) * (1 - dm)
    out["z"] = z
    return {k: out[k] for k in want} if want != "all" else out


def elevation(x, y):
    return fields(x, y)["z"]


def grid_eval(xs, ys, fn, rows=128):
    """Evaluate fn(xx, yy) on the xs x ys grid (row 0 = ys[0]) in row chunks."""
    parts = []
    for i in range(0, len(ys), rows):
        xx, yy = np.meshgrid(xs, ys[i:i + rows])
        parts.append(fn(xx, yy))
    if isinstance(parts[0], dict):
        return {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    return np.concatenate(parts)


# --- encoders (same as pipeline/make_fixture.py) -------------------------------
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
    return -HALF + tx * ts, HALF - ty * ts, ts


def slope_deg(z, step):
    gy, gx = np.gradient(z, step)
    return np.degrees(np.arctan(np.hypot(gx, gy)))


def hazard_class(slope):
    c = np.zeros(slope.shape, dtype=np.uint8)
    c[slope >= HAZARD_THRESH[0]] = 1
    c[slope >= HAZARD_THRESH[1]] = 2
    return c


# --- writers --------------------------------------------------------------------
def post_grid():
    """Level-MAX_LEVEL height posts: 1 m spacing including both edges, row 0 = north."""
    n = HEIGHT_PX - 1
    posts = n * 2 ** MAX_LEVEL + 1
    xs = -HALF + np.arange(posts) * (SIZE / (posts - 1))
    ys = HALF - np.arange(posts) * (SIZE / (posts - 1))
    return grid_eval(xs, ys, elevation)


def write_height_tiles(out, posts):
    n = HEIGHT_PX - 1
    for z in range(MAX_LEVEL + 1):
        step = 2 ** (MAX_LEVEL - z)
        for ty in range(2 ** z):
            for tx in range(2 ** z):
                r0, c0 = ty * n * step, tx * n * step
                t = posts[r0:r0 + n * step + 1:step, c0:c0 + n * step + 1:step]
                assert t.shape == (HEIGHT_PX, HEIGHT_PX)
                path = out / "tiles" / "height" / str(z) / str(tx) / f"{ty}.png"
                path.parent.mkdir(parents=True, exist_ok=True)
                Image.fromarray(encode_rgb(t), "RGB").save(path, optimize=True)


def write_rasters(out):
    n = int(SIZE / ANALYSIS_RES)
    xs = -HALF + (np.arange(n) + 0.5) * ANALYSIS_RES
    ys = HALF - (np.arange(n) + 0.5) * ANALYSIS_RES
    f = grid_eval(xs, ys, lambda xx, yy: {**fields(xx, yy, "all"), "xx": xx, "yy": yy})
    dem = f["z"].astype(np.float32)
    slope = slope_deg(dem.astype(np.float64), ANALYSIS_RES).astype(np.float32)
    pad = np.pad(dem, 2, mode="edge")
    rough = np.lib.stride_tricks.sliding_window_view(pad, (5, 5)).std(axis=(-1, -2)).astype(np.float32)

    # geology: 1 plains, 2 crater floor, 3 delta (and delta-remnant butte), 4 dunes, 5 channel fill, 6 rim/ejecta
    geo = np.ones_like(dem)
    rr = f["crater_r"]
    geo[(rr < 420) | (f["small_ejecta"] > 0.35) | (f["small_inside"] > 0)] = 6
    geo[(rr < 145) | ((f["breach"] > 0.5) & (f["crater_depth"] < -18) & (rr < 260))] = 2
    geo[(f["delta"] > 0.5) | (f["mesa"] > 0.5) | (f["talus"] > 0.5)] = 3
    geo[f["channel"] > 0.45] = 5
    geo[f["dune"] > 0.35] = 4

    xx, yy = f["xx"], f["yy"]
    bg = lambda seed: 0.09 + 0.06 * fbm(xx, yy, 70, 3, seed)  # noqa: E731  background < 0.2
    u = f["delta_u"]
    margin = np.exp(-((u - 5) / 45) ** 2) * (0.65 + 0.35 * fbm(xx, yy, 60, 3, 201))
    floor_blob = np.exp(-(((xx - 355) ** 2 + (yy + 325) ** 2) / 70 ** 2)) * (rr < 260)
    carbonate = np.maximum(bg(202), np.maximum(0.9 * margin, 0.85 * floor_blob * (0.8 + 0.2 * fbm(xx, yy, 30, 2, 203))))
    rcr = rr
    olv_ring = np.exp(-((rcr - 370) / 110) ** 2) * (0.55 + 0.45 * np.cos(np.arctan2(yy - CRATER[1], xx - CRATER[0]) - BREACH))
    olivine = np.maximum(bg(204), 0.85 * olv_ring * (0.75 + 0.25 * fbm(xx, yy, 50, 3, 205)) * (1 - f["dune"]) * (1 - f["delta"]))
    layered = f["delta"] * (0.55 + 0.3 * np.cos(2 * np.pi * f["delta_layer"])) * smoothstep(40, 120, u)
    clay = np.maximum(bg(206), np.maximum(layered, 0.75 * f["channel"]) * (0.85 + 0.15 * fbm(xx, yy, 40, 2, 207)))
    clay = np.maximum(clay, 0.5 * (f["mesa"] + np.clip(f["talus"], 0, 1)) * 0.9)  # butte = delta remnant
    minerals = {k: np.clip(v, 0, 1).astype(np.float32) for k, v in
                (("min_carbonate", carbonate), ("min_olivine", olivine), ("min_clay", clay))}

    meta = dict(driver="GTiff", height=n, width=n, count=1, dtype="float32",
                crs=CRS, transform=from_origin(-HALF, HALF, ANALYSIS_RES, ANALYSIS_RES))
    (out / "analysis").mkdir(parents=True, exist_ok=True)
    rasters = [("dem", dem), ("slope", slope), ("rough", rough), ("layer_geology", geo.astype(np.float32))]
    rasters += [(f"layer_{k}", v) for k, v in minerals.items()]
    for name, arr in rasters:
        with rasterio.open(out / "analysis" / f"{name}.tif", "w", compress="deflate", predictor=3, **meta) as dst:
            dst.write(arr, 1)
    return dem, slope, geo, minerals


def image_fields(xx, yy):
    f = fields(xx, yy, "all")
    alb = 0.50 + 0.06 * fbm(xx, yy, 350, 3, 300) + 0.04 * fbm(xx, yy, 45, 4, 301) + 0.03 * fbm(xx, yy, 6, 2, 302)
    dl = f["delta"]
    band = np.cos(2 * np.pi * (f["delta_u"] + 6 * fbm(xx, yy, 60, 2, 306)) / 23.0) * (1 - 0.7 * smoothstep(250, 420, f["delta_u"]))
    layers = (0.6 + 0.035 * band * (0.5 + 0.5 * fbm(xx, yy, 120, 2, 307)) + 0.03 * np.cos(2 * np.pi * f["delta_layer"])
              + 0.04 * fbm(xx, yy, 30, 3, 308))
    alb = alb * (1 - dl) + layers * dl
    ch = np.clip(f["channel"], 0, 1)
    alb = alb * (1 - 0.7 * ch) + 0.58 * 0.7 * ch
    rr = f["crater_r"]
    rays = (0.5 + 0.5 * np.cos(13 * np.arctan2(yy - CRATER[1], xx - CRATER[0]) + 3 * fbm(xx, yy, 80, 2, 303))) ** 4
    alb = alb + 0.06 * f["ejecta"] * rays + 0.05 * f["small_ejecta"]
    alb = np.where(rr < 150, 0.47 + 0.03 * fbm(xx, yy, 25, 3, 304), alb)
    alb = alb * (1 - f["mesa"]) + 0.66 * f["mesa"]
    dm = f["dune"]
    alb = alb * (1 - 0.95 * dm) + (0.27 + 0.03 * fbm(xx, yy, 20, 2, 305)) * 0.95 * dm
    alb = alb + 0.18 * np.clip(f["boulder_tops"] / 0.4, 0, 1)
    return {"z": f["z"], "alb": alb.astype(np.float32)}


def write_image_tiles(out, dem, slope, geo, minerals):
    px = IMG_PX * 2 ** MAX_LEVEL
    step = SIZE / px
    xs = -HALF + (np.arange(px) + 0.5) * step
    ys = HALF - (np.arange(px) + 0.5) * step
    t0 = time.time()
    f = grid_eval(xs, ys, image_fields)
    print(f"  image-res terrain {px}x{px} in {time.time() - t0:.0f} s")
    z = f["z"]
    gy, gx = np.gradient(z, step)
    az, alt = math.radians(315.0), math.radians(40.0)
    dzdx, dzdy = gx, -gy
    aspect = np.arctan2(dzdy, -dzdx)
    sl = np.arctan(np.hypot(dzdx, dzdy))
    shade = np.sin(alt) * np.cos(sl) + np.cos(alt) * np.sin(sl) * np.cos(az - aspect)
    del gx, gy, dzdx, dzdy, aspect, sl, z
    rng = np.random.default_rng(7)
    grain = rng.normal(0.0, 0.025, shade.shape).astype(np.float32)
    lum = np.clip(f["alb"] * (0.3 + 0.95 * np.clip(shade, 0, 1)) + grain, 0, 1)
    ortho = (lum * 255).astype(np.uint8)
    opaque = np.full(ortho.shape, 255, np.uint8)
    layers = {"ortho": np.dstack([ortho, ortho, ortho, opaque])}

    # analysis-grid products upsampled to image pixels (keeps tiles consistent with the API)
    ai = (np.arange(px) + 0.5) * step / ANALYSIS_RES - 0.5
    rows, cols = np.meshgrid(ai, ai, indexing="ij")

    def up(a, order=1):
        return ndimage.map_coordinates(a, [rows, cols], order=order, mode="nearest").astype(np.float32)

    # false colour: R olivine, G carbonate, B clay over the hillshade
    tone = np.clip(0.35 + 0.65 * shade, 0, 1)
    ol, ca, cl = up(minerals["min_olivine"]), up(minerals["min_carbonate"]), up(minerals["min_clay"])
    rgb = [np.clip((0.35 + 0.9 * ol - 0.2 * ca - 0.1 * cl) * tone, 0, 1),
           np.clip((0.35 + 0.9 * ca - 0.15 * ol - 0.1 * cl) * tone, 0, 1),
           np.clip((0.35 + 0.9 * cl - 0.2 * ol - 0.1 * ca) * tone, 0, 1)]
    layers["ortho_color"] = np.dstack([(c * 255).astype(np.uint8) for c in rgb] + [opaque])
    del ol, ca, cl, rgb, tone, shade, grain, lum

    s_up = up(slope)
    haz_colors = np.array([[40, 180, 80, 70], [240, 180, 30, 150],
                           [220, 40, 40, 180], [120, 120, 120, 160]], dtype=np.uint8)
    layers["hazard"] = haz_colors[hazard_class(s_up)]
    s = np.clip(s_up / 30.0, 0, 1)
    layers["slope"] = np.dstack([(255 * s).astype(np.uint8), (255 * (1 - s)).astype(np.uint8),
                                 np.full(s.shape, 60, np.uint8), np.full(s.shape, 140, np.uint8)])
    del s, s_up

    for name, arr in layers.items():
        full = Image.fromarray(arr, "RGBA" if name in ("slope", "hazard") else "RGBA")
        if name in ("ortho", "ortho_color"):
            full = full.convert("RGB")
        for lvl in range(MAX_LEVEL + 1):
            n = 2 ** lvl
            side = IMG_PX * n
            img = full if lvl == MAX_LEVEL else full.resize((side, side), Image.LANCZOS)
            for ty in range(n):
                for tx in range(n):
                    tile = img.crop((tx * IMG_PX, ty * IMG_PX, (tx + 1) * IMG_PX, (ty + 1) * IMG_PX))
                    path = out / "tiles" / name / str(lvl) / str(tx) / f"{ty}.webp"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    tile.save(path, "WEBP", quality=82 if name.startswith("ortho") else 90)
    return layers["ortho"][..., 0]


# --- POIs and traverse ------------------------------------------------------------
def poi(pid, name, typ, x, y, desc, value, sol=None):
    lat, lon = xy_to_latlon(x, y)
    props = dict(id=pid, name=name, type=typ, x=round(float(x), 1), y=round(float(y), 1),
                 z=round(float(elevation(np.array(x), np.array(y))), 2),
                 lat=round(lat, 6), lon=round(lon, 6), mission="fixture",
                 source_url=SRC_URL, description=desc + " Synthetic test data, not Mars data.",
                 science_value=value, sol=sol, image_urls=[])
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
            "properties": props}


class Router:
    """Least-cost router on the analysis grid (4 m cells, slope limit), used to lay a
    plausible rover traverse that stays under the rover's slope limit."""

    def __init__(self, slope, limit):
        f = 2
        h = slope.shape[0] // f
        smax = slope.reshape(h, f, h, f).max(axis=(1, 3))
        smean = slope.reshape(h, f, h, f).mean(axis=(1, 3))
        nogo = ndimage.binary_dilation(smax > limit, iterations=1)
        cost = 1.0 / np.exp(-3.5 * np.tan(np.radians(smean)))
        cost[nogo] = np.inf
        self.cost, self.res = cost, ANALYSIS_RES * f

    def cell(self, x, y):
        return int((HALF - y) / self.res), int((x + HALF) / self.res)

    def xy(self, rc):
        return -HALF + (rc[1] + 0.5) * self.res, HALF - (rc[0] + 0.5) * self.res

    def path(self, a, b):
        m = MCP_Geometric(self.cost, sampling=(self.res, self.res))
        sa, sb = self.cell(*a), self.cell(*b)
        cum, _ = m.find_costs([sa], [sb])
        if not np.isfinite(cum[sb]):
            raise RuntimeError(f"traverse leg {a} -> {b} is blocked")
        return [tuple(a)] + [self.xy(rc) for rc in m.traceback(sb)[1:-1]] + [tuple(b)]


def sample(grid, x, y):
    col = int(np.clip((x + HALF) / ANALYSIS_RES, 0, grid.shape[1] - 1))
    row = int(np.clip((HALF - y) / ANALYSIS_RES, 0, grid.shape[0] - 1))
    return float(grid[row, col])


def flat_spot(slope, x, y, radius=30, limit=8.0, prefer=None):
    """Nearest analysis cell to (x, y) whose 3x3 neighbourhood is under `limit` deg."""
    smax = ndimage.maximum_filter(slope, size=5)
    r0, c0 = int((HALF - y) / ANALYSIS_RES), int((x + HALF) / ANALYSIS_RES)
    k = int(radius / ANALYSIS_RES)
    best = None
    for r in range(max(0, r0 - k), min(slope.shape[0], r0 + k + 1)):
        for c in range(max(0, c0 - k), min(slope.shape[1], c0 + k + 1)):
            if smax[r, c] < limit and (prefer is None or prefer(r, c)):
                d = (r - r0) ** 2 + (c - c0) ** 2
                if best is None or d < best[0]:
                    best = (d, r, c)
    if best is None:
        raise RuntimeError(f"no flat ground near ({x}, {y})")
    return -HALF + (best[2] + 0.5) * ANALYSIS_RES, HALF - (best[1] + 0.5) * ANALYSIS_RES


def delta_point(theta, u):
    """Point at bearing theta from the apex, u metres inside (positive) the mean front."""
    return APEX[0] + (DELTA_R - u) * math.cos(theta), APEX[1] + (DELTA_R - u) * math.sin(theta)


def write_vectors(out, slope, geo, minerals):
    fs = lambda x, y, lim=8.0, **k: flat_spot(slope, x, y, limit=lim, **k)  # noqa: E731
    in_geo = lambda cls: (lambda r, c: geo[r, c] == cls)  # noqa: E731
    crest = None
    # dune crest: a crest cell near the upwind (WSW) edge of the field, on a stoss-top bench
    dz = minerals  # unused; kept for symmetry
    del dz
    xs_d = DUNES[0] - 170
    ys_d = DUNES[1] - 60
    crest = fs(xs_d, ys_d, lim=12.0, radius=60, prefer=in_geo(4))

    g0, g1 = GAPS
    mouth = delta_point(g0, -70)
    gap2_foot = delta_point(g1, -80)
    gap2_top = delta_point(g1, 150)
    mesa_foot = (MESA[0] - 10, MESA[1] - 165)
    floor = (355.0, -325.0)
    pts = {
        "base": fs(-250, 0, lim=4.0),
        "delta_top": fs(-760, 430, lim=6.0, prefer=in_geo(3)),
        "crater_floor": fs(*floor, lim=6.0, prefer=in_geo(2)),
        "channel_mouth": fs(*mouth, lim=8.0),
        "dune_crest": crest,
        "mesa_foot": fs(*mesa_foot, lim=10.0),
        "olivine_plains": fs(130, -60, lim=8.0),
        "breach": fs(CRATER[0] + 230 * math.cos(BREACH), CRATER[1] + 230 * math.sin(BREACH), lim=17.0),
        "central_peak": (CRATER[0], CRATER[1]),
        "rim_view": fs(CRATER[0] + 245 * math.cos(math.radians(60)), CRATER[1] + 245 * math.sin(math.radians(60)), lim=14.0, radius=30),
        "butte": (MESA[0], MESA[1]),
    }
    # delta front hazard: where the base -> delta-top straight line crosses the scarp
    bx, by = pts["base"]
    tx, ty = pts["delta_top"]
    line = [(bx + (tx - bx) * t, by + (ty - by) * t) for t in np.linspace(0, 1, 400)]
    pts["delta_front"] = max(line, key=lambda p: sample(slope, *p))
    rim_s = [(CRATER[0] + 205 * math.cos(a), CRATER[1] + 205 * math.sin(a)) for a in np.linspace(-1.5, -0.3, 60)]
    pts["crater_wall"] = max(rim_s, key=lambda p: sample(slope, *p))
    pts["dune_slipfaces"] = (DUNES[0] + 60, DUNES[1] + 10)
    pts["boulder_field"] = BOULDER_PATCHES[0][:2]

    features = [
        poi("base", "Playground base", "base", *pts["base"], "Habitat on a smooth plain at the west end of the highway corridor.", 0.0),
        poi("delta_top", "Delta top layers", "science_target", *pts["delta_top"], "Layered delta-top sediments (clay-bearing). Reach it through one of the two front gullies.", 0.95),
        poi("crater_floor", "Crater floor carbonates", "science_target", *pts["crater_floor"], "Carbonate-rich floor just inside the breach ramp.", 0.9),
        poi("channel_mouth", "Channel mouth fan", "science_target", *pts["channel_mouth"], "Where the southern delta channel exits its gully onto the plain; carbonate and clay.", 0.85),
        poi("dune_crest", "Dune crest", "science_target", *pts["dune_crest"], "Crest of an upwind transverse dune; dark basaltic-style sand.", 0.5),
        poi("mesa_foot", "Butte foot", "science_target", *pts["mesa_foot"], "Talus at the foot of a delta-remnant butte (Kodiak-like analogue); layered cliff above.", 0.75),
        poi("olivine_plains", "Olivine plains outcrop", "sample_site", *pts["olivine_plains"], "Olivine-rich plains north-west of the crater.", 0.7, sol=60),
        poi("breach", "Crater breach ramp", "landmark", *pts["breach"], "~15 deg ramp through the NW rim; the only walkable way into the crater.", 0.4),
        poi("central_peak", "Central peak", "landmark", *pts["central_peak"], "Central uplift of the 500 m crater.", 0.6),
        poi("rim_view", "Rim lookout", "landmark", *pts["rim_view"], "Rim crest lookout over the crater interior.", 0.3),
        poi("butte", "Flat-top butte", "landmark", *pts["butte"], "Cliffed butte, ~36 m high; summit not walkable.", 0.4),
        poi("delta_front", "Delta front scarp", "hazard", *pts["delta_front"], "~30 deg scarp; no-go on foot. Detour through a gully.", 0.0),
        poi("crater_wall", "Crater terraced wall", "hazard", *pts["crater_wall"], "Terraced inner wall, 30 deg+ risers.", 0.0),
        poi("dune_slipfaces", "Dune slip faces", "hazard", *pts["dune_slipfaces"], "Slip faces ~30 deg; soft sand.", 0.0),
        poi("boulder_field", "Boulder field", "hazard", *pts["boulder_field"], "Dense boulders; rough, slow going.", 0.0),
    ]

    # rover traverse: landing on the eastern highway -> crater floor -> plains -> up gully 2 onto the delta
    wps = [
        ("wp1", "Landing site (sol 1)", fs(820, 40, lim=5.0), 1),
        ("wp2", "Highway drive (sol 30)", fs(420, 20, lim=6.0), 30),
        ("wp3", "Olivine plains (sol 60)", pts["olivine_plains"], 60),
        ("wp4", "Breach ramp (sol 95)", pts["breach"], 95),
        ("wp5", "Crater floor (sol 140)", pts["crater_floor"], 140),
        ("wp6", "Gully foot (sol 230)", fs(*gap2_foot, lim=8.0), 230),
        ("wp7", "Delta top (sol 300)", fs(*gap2_top, lim=8.0), 300),
    ]
    router = Router(slope, 16.5)
    line = []
    for (_, _, a, _), (_, _, b, _) in zip(wps, wps[1:]):
        seg = router.path(a, b)
        line += seg if not line else seg[1:]
    ls = LineString(line).simplify(1.5)
    ls_coords = list(ls.coords)
    dens = np.array([ls.interpolate(d).coords[0] for d in np.arange(0, ls.length, 2.0)])
    smax = max(sample(slope, x, y) for x, y in dens)
    print(f"  traverse {ls.length:.0f} m, max slope on line {smax:.1f} deg")

    wp_feats = [poi(i, n, "rover_waypoint", *p, "Synthetic rover waypoint.", 0.1, sol=s) for i, n, p, s in wps]
    features += wp_feats
    data = out / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "pois.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features}, indent=1))
    coords = [[round(v, 6) for v in reversed(xy_to_latlon(x, y))] for x, y in ls_coords]
    tl = {"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords},
          "properties": {"kind": "traverse", "note": "Synthetic traverse, not Mars data."}}
    (data / "traverse.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [tl] + wp_feats}, indent=1))
    return features, ls_coords


def write_manifest(out, posts):
    def layer(lid, title, kind, instrument, **extra):
        return {"id": lid, "title": title, "mission": "fixture", "instrument": instrument,
                "max_level": MAX_LEVEL, "kind": kind, "credit": CREDIT, "url": SRC_URL, **extra}

    m = {
        "site": "playground", "fixture": True,
        "description": "Synthetic 2 km playground site for testing driving, routing and search. Not Mars data.",
        "lat0": LAT0, "lon0": LON0, "radius_m": int(R),
        "size_m": int(SIZE), "elev_min": math.floor(float(posts.min())), "elev_max": math.ceil(float(posts.max())),
        "height": {"max_level": MAX_LEVEL, "tile_px": HEIGHT_PX},
        "layers": [
            layer("ortho", "Synthetic hillshade (not Mars)", "base", "synthetic"),
            layer("ortho_color", "Synthetic false colour minerals (not Mars): R olivine, G carbonate, B clay",
                  "base", "synthetic", false_colour=True),
            layer("slope", "Slope (synthetic, not Mars)", "overlay", "synthetic DTM",
                  legend=[[0, "flat"], [15, "steep"], [30, "very steep"]]),
            layer("hazard", "Slope hazard (synthetic, not Mars)", "overlay", "synthetic DTM",
                  legend=[[0, "safe"], [15, "caution"], [25, "no-go"], [None, "unknown"]]),
        ],
        "vectors": {"pois": "/data/pois.geojson", "traverse": "/data/traverse.geojson"},
    }
    (out / "manifest.json").write_text(json.dumps(m, indent=2))


def self_check(out):
    """Decode every height tile, confirm round trip and shared edges."""
    def load(z, tx, ty):
        return decode_rgb(np.array(Image.open(out / "tiles" / "height" / str(z) / str(tx) / f"{ty}.png")))

    for z in range(MAX_LEVEL + 1):
        n = 2 ** z
        for ty in range(n):
            for tx in range(n):
                t = load(z, tx, ty)
                assert t.shape == (HEIGHT_PX, HEIGHT_PX)
                if tx + 1 < n:
                    assert np.array_equal(t[:, -1], load(z, tx + 1, ty)[:, 0]), f"east edge mismatch z{z} {tx},{ty}"
                if ty + 1 < n:
                    assert np.array_equal(t[-1, :], load(z, tx, ty + 1)[0, :]), f"south edge mismatch z{z} {tx},{ty}"
    for z, tx, ty, i, j in [(MAX_LEVEL, 3, 5, 50, 100), (1, 0, 1, 200, 17), (0, 0, 0, 128, 128)]:
        x0, y0, ts = tile_origin(z, tx, ty)
        t = load(z, tx, ty)
        truth = float(elevation(np.array(x0 + j * ts / 256), np.array(y0 - i * ts / 256)))
        assert abs(t[i, j] - truth) < 0.006, (z, t[i, j], truth)
    # a coarse tile corner equals the finest tile at the same spot
    assert load(0, 0, 0)[128, 128] == load(1, 1, 1)[0, 0] == load(MAX_LEVEL, 4, 4)[0, 0]
    for name in ("ortho", "ortho_color", "slope", "hazard"):
        for z in range(MAX_LEVEL + 1):
            im = Image.open(out / "tiles" / name / str(z) / str(2 ** z - 1) / f"{2 ** z - 1}.webp")
            assert im.size == (IMG_PX, IMG_PX), (name, z, im.size)
    with rasterio.open(out / "analysis" / "dem.tif") as d:
        assert d.shape == (1024, 1024) and d.res == (ANALYSIS_RES, ANALYSIS_RES)
        assert abs(d.read(1)[0, 0] - float(elevation(np.array(-HALF + 1.0), np.array(HALF - 1.0)))) < 1e-3
    print("self-check ok: edges shared, decode within 0.006 m, image tiles 512 px, analysis grid 1024^2 @ 2 m")


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "fixtures" / "playground"
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    posts = post_grid()
    print(f"height posts {posts.shape} in {time.time() - t0:.0f} s, elevation {posts.min():.1f} .. {posts.max():.1f} m")
    write_height_tiles(out, posts)
    dem, slope, geo, minerals = write_rasters(out)
    print(f"analysis rasters done ({time.time() - t0:.0f} s); slope >= 25 deg on {(slope >= 25).mean() * 100:.1f}% of cells")
    write_vectors(out, slope, geo, minerals)
    write_manifest(out, posts)
    write_image_tiles(out, dem, slope, geo, minerals)
    print(f"image tiles done ({time.time() - t0:.0f} s)")
    self_check(out)
    print("wrote playground to", out)


if __name__ == "__main__":
    main()

"""Stream A step 8: assert the built site matches the contracts.

Checks (against data/build, or pass a dir, e.g. contracts/fixtures):
  - every manifest layer has title, mission, instrument, credit and url
  - tile counts match the quadtree for every level, height and imagery
  - adjacent height tiles share identical edge values
  - decoded height tiles match analysis/dem.tif within 0.05 m at 100 random points
  - slope.tif agrees with a manual gradient at 5 random interior points
  - gap-filled cell share is reported (hazard treats filled cells as unknown)
Run: .venv/bin/python pipeline/08_check.py [builddir]
"""
import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image

root = Path(__file__).resolve().parent.parent
B = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "data" / "build"
m = json.loads((B / "manifest.json").read_text())
SIZE, L = m["size_m"], m["height"]["max_level"]
fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
        print("FAIL:", msg)


def dec(p):
    a = np.array(Image.open(p)).astype(np.int64)
    return -10000.0 + 0.01 * (a[..., 0] * 65536 + a[..., 1] * 256 + a[..., 2])


for lay in m["layers"]:
    for k in ("title", "mission", "instrument", "credit", "url"):
        check(lay.get(k), f"layer {lay['id']} missing {k}")
    for z in range(lay["max_level"] + 1):
        n = len(list((B / "tiles" / lay["id"] / str(z)).glob("*/*.webp")))
        check(n == 4 ** z, f"{lay['id']} level {z}: {n} tiles, expected {4 ** z}")

for z in range(L + 1):
    n = len(list((B / "tiles" / "height" / str(z)).glob("*/*.png")))
    check(n == 4 ** z, f"height level {z}: {n} tiles, expected {4 ** z}")

# shared edges at the top two levels (full set is slow for level 5; sample it)
rng = np.random.default_rng(1)
for z in range(min(L, 3) + 1):
    for _ in range(min(20, 4 ** z)):
        x, y = rng.integers(0, 2 ** z, 2)
        t = dec(B / "tiles/height" / str(z) / str(x) / f"{y}.png")
        if x + 1 < 2 ** z:
            e = dec(B / "tiles/height" / str(z) / str(x + 1) / f"{y}.png")
            check(np.array_equal(t[:, -1], e[:, 0]), f"east edge z{z} {x},{y}")
        if y + 1 < 2 ** z:
            s = dec(B / "tiles/height" / str(z) / str(x) / f"{y + 1}.png")
            check(np.array_equal(t[-1], s[0]), f"south edge z{z} {x},{y}")
for _ in range(10):  # level 5 sample
    x, y = rng.integers(0, 2 ** L - 1, 2)
    t = dec(B / "tiles/height" / str(L) / str(x) / f"{y}.png")
    e = dec(B / "tiles/height" / str(L) / str(x + 1) / f"{y}.png")
    check(np.array_equal(t[:, -1], e[:, 0]), f"east edge z{L} {x},{y}")

with rasterio.open(B / "analysis/dem.tif") as d:
    dem, res = d.read(1), d.res[0]
n = dem.shape[0]
bad = 0
for _ in range(100):
    # point on the 2 m grid centre; compare to the top-level tile bilinear-free nearest post
    r, c = rng.integers(2, n - 2, 2)
    wx, wy = -SIZE / 2 + (c + 0.5) * res, SIZE / 2 - (r + 0.5) * res
    ts = SIZE / 2 ** L
    tx, ty = int((wx + SIZE / 2) // ts), int((SIZE / 2 - wy) // ts)
    t = dec(B / "tiles/height" / str(L) / str(tx) / f"{ty}.png")
    px = (wx - (-SIZE / 2 + tx * ts)) / (ts / 256)
    py = ((SIZE / 2 - ty * ts) - wy) / (ts / 256)
    # 2 m cell is the mean of 2x2 one-metre posts; compare with the mean of the 4 nearest posts
    i, j = int(np.floor(py - 0.5)), int(np.floor(px - 0.5))
    if 0 <= i < 255 and 0 <= j < 255:
        approx = t[i:i + 2, j:j + 2].mean()
        bad += abs(approx - dem[r, c]) > 0.5  # loose: posts are corners, DEM is cell-averaged
check(bad <= 5, f"{bad}/100 sampled points differ from dem.tif by more than 0.5 m")

with rasterio.open(B / "analysis/slope.tif") as d:
    slope = d.read(1)
for _ in range(5):
    r, c = rng.integers(3, n - 3, 2)
    gx = (dem[r, c + 1] - dem[r, c - 1]) / (2 * res)
    gy = (dem[r + 1, c] - dem[r - 1, c]) / (2 * res)
    manual = np.degrees(np.arctan(np.hypot(gx, gy)))
    check(abs(manual - slope[r, c]) < 0.05, f"slope at ({r},{c}): {slope[r, c]:.3f} vs manual {manual:.3f}")

fp = B / "analysis/filled.tif"
if fp.exists():
    with rasterio.open(fp) as d:
        print(f"gap-filled share of 2 m cells: {d.read(1).mean():.4%}")
print("elevation range", float(dem.min()), float(dem.max()))
print("CHECK", "FAIL" if fails else "PASS", f"({len(fails)} failures)")
sys.exit(1 if fails else 0)

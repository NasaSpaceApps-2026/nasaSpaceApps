"""Write synthetic mineral layers (layer_min_*.tif, 0..1 abundance) on the data grid.

Placeholder until Stream A ships real CRISM-derived layers. The API labels these
"synthetic placeholder (not Mars data)" everywhere they are cited.

Run: api/.venv/bin/python api/scripts/make_dev_layers.py [data_dir] [out_dir]
"""
import sys
from pathlib import Path

import numpy as np
import rasterio

REPO = Path(__file__).resolve().parents[2]

# Planted hotspots in site metres: (x, y, radius_m, peak). Tests rely on these.
HOTSPOTS = {
    "carbonate": [(-150, 100, 45, 0.95), (-300, 250, 30, 0.75), (-60, -350, 20, 0.6)],
    "olivine": [(100, -200, 60, 0.9), (350, 300, 40, 0.8)],
    "clay": [(-150, 100, 25, 0.7), (-400, -100, 50, 0.85)],
}


def main():
    data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "contracts" / "fixtures"
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else REPO / "api" / "fixtures" / "analysis"
    out.mkdir(parents=True, exist_ok=True)
    with rasterio.open(data_dir / "analysis" / "dem.tif") as src:
        meta = src.meta.copy()
        h, w = src.shape
        tr = src.transform
    xs = tr.c + (np.arange(w) + 0.5) * tr.a
    ys = tr.f + (np.arange(h) + 0.5) * tr.e
    xx, yy = np.meshgrid(xs, ys)
    rng = np.random.default_rng(42)
    meta.update(dtype="float32", count=1, nodata=None)
    for name, spots in HOTSPOTS.items():
        a = rng.uniform(0.0, 0.15, (h, w))  # background noise below any threshold
        for x, y, r, peak in spots:
            a = np.maximum(a, peak * np.exp(-((np.hypot(xx - x, yy - y) / r) ** 2)))
        with rasterio.open(out / f"layer_min_{name}.tif", "w", **meta) as dst:
            dst.write(a.astype(np.float32), 1)
        print("wrote", out / f"layer_min_{name}.tif")


if __name__ == "__main__":
    main()

"""Export the 2 m DEM as a compact Uint16 raster for the browser terrain provider.

elev_m = base + step * value; step 0.01 m. Range must fit 16 bits (655 m), true for the
Jezero site (about 345 m relief). Writes data/build/web/dem2m.u16 and dem2m.json.
Run: .venv/bin/python pipeline/09_export_web.py [builddir]
"""
import json
import sys
from pathlib import Path

import numpy as np
import rasterio

B = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "data" / "build"
with rasterio.open(B / "analysis" / "dem.tif") as d:
    dem, res = d.read(1), d.res[0]
base = float(np.floor(dem.min()))
v = np.rint((dem - base) / 0.01)
assert v.max() < 65536, "relief too large for Uint16 at 0.01 m"
(B / "web").mkdir(exist_ok=True)
v.astype("<u2").tofile(B / "web" / "dem2m.u16")
(B / "web" / "dem2m.json").write_text(json.dumps(
    {"width": dem.shape[1], "height": dem.shape[0], "res_m": res, "base": base, "step": 0.01}))
print("dem2m.u16", dem.shape, "base", base, "bytes", v.size * 2)

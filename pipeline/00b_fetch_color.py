"""Fetch the Viking MDIM 2.1 colourised mosaic tiles around the site from NASA Mars Trek (WMTS).

Used only as the low-frequency COLOUR source for colourising the greyscale HiRISE ortho
(pan-sharpening in build_tiles.py). A 3x3 block of level-7 tiles (about 330 m/px) is stitched
into data/raw/viking_color.png with its lon/lat bounds in viking_color.json.
Run: .venv/bin/python pipeline/00b_fetch_color.py
"""
import datetime
import io
import json
import math
import sys
from pathlib import Path

import requests
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from site_cfg import LAT0, LON0  # noqa: E402

LAYER = "Mars_Viking_MDIM21_ClrMosaic_global_232m"
URL = ("https://trek.nasa.gov/tiles/Mars/EQ/" + LAYER + "/1.0.0//default/default028mm/{z}/{row}/{col}.jpg")
Z, CW, CH = 7, 2 ** 8, 2 ** 7  # level 7: 256 x 128 tiles of 256 px over 360 x 180 deg
ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"

col0 = int((LON0 + 180) / 360 * CW)
row0 = int((90 - LAT0) / 180 * CH)
cols, rows = range(col0 - 1, col0 + 2), range(row0 - 1, row0 + 2)
canvas = Image.new("RGB", (256 * 3, 256 * 3))
for ri, r in enumerate(rows):
    for ci, c in enumerate(cols):
        resp = requests.get(URL.format(z=Z, row=r, col=c), timeout=60)
        resp.raise_for_status()
        canvas.paste(Image.open(io.BytesIO(resp.content)).convert("RGB"), (ci * 256, ri * 256))
RAW.mkdir(parents=True, exist_ok=True)
canvas.save(RAW / "viking_color.png")
west = -180 + cols[0] / CW * 360
north = 90 - rows[0] / CH * 180
meta = {"west": west, "east": west + 3 / CW * 360, "north": north, "south": north - 3 / CH * 180,
        "width": 768, "height": 768, "tiles": {"z": Z, "cols": [cols[0], cols[-1]], "rows": [rows[0], rows[-1]]}}
(RAW / "viking_color.json").write_text(json.dumps(meta))
with open(ROOT / "data" / "SOURCES.md", "a") as f:
    f.write(f"\n## Viking MDIM 2.1 colourised mosaic (colour source only)\n- Layer: {LAYER} via NASA Mars Trek WMTS, level {Z}"
            f" (about 330 m/px)\n- Fetched: {datetime.date.today()}, 3x3 tiles around lat {LAT0}, lon {LON0}\n"
            f"- Use: low-frequency colour for the HiRISE ortho. The result is FALSE COLOUR (HiRISE is greyscale).\n"
            f"- Credit: NASA/JPL/USGS\n")
print("ok", meta["west"], meta["east"], meta["south"], meta["north"])

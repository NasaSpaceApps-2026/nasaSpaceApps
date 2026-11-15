"""Build a larger Jezero bundle for the vision refine loop.

The 80 m landing patch is flat. This crop is 1.2 km on the rough ground north
of Octavia E. Butler Landing, where the HiRISE DTM has tens of metres of
relief. Height is that DTM at 2 m. The texture is the HiRISE ortho at the
same posts: real satellite brightness and dirt pattern, tinted Mars orange.
HiRISE is greyscale. The orange is the anchored dust tone in colorize.py,
with the broad redder-or-darker variation from Viking MDIM.

The full rasters stay in data/raw/ and on the USGS URL. This script writes
only the bundle.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image

from bubble.geom import Camera, Grid
from bubble.render import render_mesh
from colorize import colorize_window
from make_jezero_bundle import DTM_URL, sample_raster
from site_cfg import LAT0, LON0, R, SIZE

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "contracts" / "fixtures" / "jezero_crater"
# Rough 800 m block found in the HiRISE window: western fan front, north of the landing site.
CENTER_LAT = 18.4818
CENTER_LON = 77.4358
N = 601
RES = 2.0
NADIR_PX = 192
NADIR_FOV = 50.0


def site_xy(lat: float, lon: float) -> tuple[float, float]:
    x = R * math.cos(math.radians(LAT0)) * math.radians(lon - LON0)
    y = R * math.radians(lat - LAT0)
    return x, y


def main() -> None:
    center_x, center_y = site_xy(CENTER_LAT, CENTER_LON)
    half = (N // 2) * RES
    x0 = center_x - half
    y0 = center_y + half
    cols = np.arange(N)
    rows = np.arange(N)
    xs = x0 + cols[None, :] * RES
    ys = y0 - rows[:, None] * RES
    ortho_path = RAW / "ortho_window.tif"
    if not ortho_path.exists():
        raise SystemExit(f"missing {ortho_path}")
    if not (RAW / "viking_color.png").exists():
        raise SystemExit("missing data/raw/viking_color.png; run pipeline/00b_fetch_color.py")
    with rasterio.open(ortho_path) as ortho_src, rasterio.open("/vsicurl/" + DTM_URL) as dtm_src:
        dem = sample_raster(dtm_src, xs, ys).astype(np.float32)
        gray = sample_raster(ortho_src, xs, ys)
    if not np.isfinite(dem).all():
        raise SystemExit("DTM crop has gaps")
    gray_u8 = np.clip(np.rint(gray), 0, 255).astype(np.uint8)
    ortho = colorize_window(gray_u8, RAW, LAT0, LON0, SIZE, x0, y0, RES)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "refs").mkdir(exist_ok=True)
    np.save(OUT / "dem.npy", dem)
    Image.fromarray(ortho).save(OUT / "ortho.png")
    (OUT / "grid.json").write_text(json.dumps({
        "x0": x0,
        "y0": y0,
        "res_m": RES,
        "center_x": center_x,
        "center_y": center_y,
    }, indent=2) + "\n")
    ground = float(dem[N // 2, N // 2])
    # Size the frame to the patch edges, with a little room for the relief.
    altitude = half / math.tan(math.radians(NADIR_FOV / 2)) * 1.05
    grid = Grid(x0, y0, RES, center_x, center_y)
    cam = Camera(
        "satellite", "fit",
        (center_x, center_y, ground + altitude),
        0.0, -90.0, NADIR_FOV, NADIR_PX, NADIR_PX, "refs/nadir.png",
    )
    ref = render_mesh(dem, ortho, cam, grid)
    Image.fromarray(ref).save(OUT / "refs" / "nadir.png")
    (OUT / "viewpoints.json").write_text(json.dumps({
        "frame": "site_enu",
        "poses_trusted": True,
        "requires_human": True,
        "registration_rms_m": None,
        "bubble_id": "jezero_fan_front",
        "pose_note": (
            "Overhead camera over the colourised HiRISE crop. The reference is that same mesh "
            "from overhead, so edits have to keep the satellite picture. Brightness and dirt "
            "pattern are HiRISE. Orange is the anchored Mars dust tone plus Viking redness variation."
        ),
        "center_lat": CENTER_LAT,
        "center_lon": CENTER_LON,
        "cameras": [{
            "name": "overhead",
            "role": "nadir",
            "xyz": [center_x, center_y, ground + altitude],
            "heading_deg": 0.0,
            "pitch_deg": -90.0,
            "fov_deg": NADIR_FOV,
            "width": NADIR_PX,
            "height": NADIR_PX,
            "image": None,
        }, {
            "name": "satellite",
            "role": "fit",
            "xyz": [center_x, center_y, ground + altitude],
            "heading_deg": 0.0,
            "pitch_deg": -90.0,
            "fov_deg": NADIR_FOV,
            "width": NADIR_PX,
            "height": NADIR_PX,
            "image": "refs/nadir.png",
        }],
        "credits": [{
            "name": "nadir",
            "source": "HiRISE ortho JEZ_hirise_soc_006, colourised with Viking MDIM 2.1 and the Mars dust anchor",
        }],
    }, indent=2) + "\n")
    print(
        "wrote", OUT,
        "relief", round(float(dem.max() - dem.min()), 1),
        "center", round(center_x, 1), round(center_y, 1),
        "rgb", [round(float(v), 1) for v in ortho.reshape(-1, 3).mean(axis=0)],
    )


if __name__ == "__main__":
    main()

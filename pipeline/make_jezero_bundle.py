"""Build a small real Jezero bundle for both refine loops.

Height is a HiRISE DTM crop. Texture is the HiRISE ortho crop. The two photos are
Perseverance left-Navcam frames from sols 9 and 12 at site 3 / drive 0, the landing
waypoint (18.44462715 N, 77.45088572 E). Mast azimuth is added to the waypoint yaw.
That pose is reconstructed, not a PDS site-frame CAHVOR solution.

The full GeoTIFFs stay in data/raw/. This script writes only the small bundle.
"""

from __future__ import annotations

import io
import json
import math
import urllib.request
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.windows import Window

from site_cfg import LAT0, LON0, R

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "contracts" / "fixtures" / "jezero_sol9"
ORTHO_URL = (
    "https://planetarymaps.usgs.gov/mosaic/mars2020_trn/HiRISE/"
    "JEZ_hirise_soc_006_orthoMosaic_25cm_Eqc_latTs0_lon0_first.tif"
)
DTM_URL = (
    "https://planetarymaps.usgs.gov/mosaic/mars2020_trn/HiRISE/"
    "JEZ_hirise_soc_006_DTM_MOLAtopography_DeltaGeoid_1m_Eqc_latTs0_lon0_blend40.tif"
)
LANDING = (18.44462715, 77.45088572)
YAW = 130.881569
# Two ground-looking left Navcams at site 3 drive 0. Azimuth is rover-relative.
# Left/right Navcam stereo pair, sol 9, site 3 drive 0, same clock.
# Both look down at the ground just off the lander. Baseline is 0.42 m to the rover's right.
FRAMES = (
    ("fit_0", "NLE_0009_0667755063", 336.761, -45.1065, 0.0),
    ("holdout_0", "NRE_0009_0667755063", 337.234, -44.9052, 0.42),
)
STEREO_BASELINE_M = 0.42
N = 81
RES = 1.0
MAST_M = 2.2
NAV_FOV = 96.0
IMG = 160


def site_xy(lat: float, lon: float) -> tuple[float, float]:
    x = R * math.cos(math.radians(LAT0)) * math.radians(lon - LON0)
    y = R * math.radians(lat - LAT0)
    return x, y


def eqc_xy(lat: float, lon: float) -> tuple[float, float]:
    return R * math.radians(lon), R * math.radians(lat)


def site_to_eqc(x, y):
    lat = LAT0 + np.degrees(np.asarray(y) / R)
    lon = LON0 + np.degrees(np.asarray(x) / (R * math.cos(math.radians(LAT0))))
    return R * np.radians(lon), R * np.radians(lat)


def sample_raster(src, xs, ys) -> np.ndarray:
    eqx, eqy = site_to_eqc(xs, ys)
    cols, rows = ~src.transform * (eqx, eqy)
    cols = np.asarray(cols, dtype=np.float64)
    rows = np.asarray(rows, dtype=np.float64)
    c0 = np.floor(cols).astype(np.int32)
    r0 = np.floor(rows).astype(np.int32)
    c1 = np.clip(c0 + 1, 0, src.width - 1)
    r1 = np.clip(r0 + 1, 0, src.height - 1)
    c0 = np.clip(c0, 0, src.width - 1)
    r0 = np.clip(r0, 0, src.height - 1)
    fc = cols - np.floor(cols)
    fr = rows - np.floor(rows)
    window = Window(
        int(c0.min()),
        int(r0.min()),
        int(c1.max() - c0.min() + 1),
        int(r1.max() - r0.min() + 1),
    )
    data = src.read(1, window=window).astype(np.float64)
    nodata = src.nodata
    if nodata is not None:
        data[np.isclose(data, nodata) | (data < -1e30)] = np.nan
    lc0, lr0 = c0 - int(c0.min()), r0 - int(r0.min())
    lc1, lr1 = c1 - int(c0.min()), r1 - int(r0.min())
    v00 = data[lr0, lc0]
    v01 = data[lr0, lc1]
    v10 = data[lr1, lc0]
    v11 = data[lr1, lc1]
    return v00 * (1 - fr) * (1 - fc) + v01 * (1 - fr) * fc + v10 * fr * (1 - fc) + v11 * fr * fc


def find_frame(prefix: str) -> dict:
    base = "https://mars.nasa.gov/rss/api/?feed=raw_images&category=mars2020&feedtype=json&num=100&order=sol+asc&page="
    for page in range(1, 40):
        req = urllib.request.Request(base + str(page), headers={"User-Agent": "ares-atlas-refine/1.0"})
        with urllib.request.urlopen(req, timeout=60) as response:
            payload = json.load(response)
        for image in payload["images"]:
            if str(image["imageid"]).startswith(prefix) and "Navigation Camera" in (image.get("title") or ""):
                return image
    raise RuntimeError(f"did not find {prefix}")


def download_gray(url: str) -> Image.Image:
    req = urllib.request.Request(url, headers={"User-Agent": "ares-atlas-refine/1.0"})
    with urllib.request.urlopen(req, timeout=60) as response:
        image = Image.open(io.BytesIO(response.read())).convert("L")
    return image.resize((IMG, IMG), Image.Resampling.LANCZOS)


def main() -> None:
    rover_x, rover_y = site_xy(*LANDING)
    cols = np.arange(N)
    rows = np.arange(N)
    x0 = rover_x - (N // 2) * RES
    y0 = rover_y + (N // 2) * RES
    xs = x0 + cols[None, :] * RES
    ys = y0 - rows[:, None] * RES
    ortho_path = RAW / "ortho_window.tif"
    if not ortho_path.exists():
        raise SystemExit(f"missing {ortho_path}; run pipeline/00_fetch.py ortho first")
    with rasterio.open(ortho_path) as ortho_src, rasterio.open("/vsicurl/" + DTM_URL) as dtm_src:
        dem = sample_raster(dtm_src, xs, ys).astype(np.float32)
        ortho = sample_raster(ortho_src, xs, ys)
    if not np.isfinite(dem).all():
        raise SystemExit("DTM crop has gaps")
    ortho_u8 = np.clip(np.rint(ortho), 0, 255).astype(np.uint8)
    ortho_rgb = np.repeat(ortho_u8[..., None], 3, axis=2)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "refs").mkdir(exist_ok=True)
    np.save(OUT / "dem.npy", dem)
    Image.fromarray(ortho_rgb).save(OUT / "ortho.png")
    (OUT / "grid.json").write_text(json.dumps({
        "x0": x0, "y0": y0, "res_m": RES, "center_x": rover_x, "center_y": rover_y,
    }, indent=2) + "\n")
    cameras = [{
        "name": "nadir",
        "role": "nadir",
        "xyz": [rover_x, rover_y, float(dem[N // 2, N // 2]) + 40.0],
        "heading_deg": 0.0,
        "pitch_deg": -90.0,
        "fov_deg": 50.0,
        "width": 96,
        "height": 96,
        "image": None,
    }]
    credits = []
    ground_z = float(dem[N // 2, N // 2])
    for name, prefix, az, el, side_m in FRAMES:
        record = find_frame(prefix)
        photo = download_gray(record["image_files"]["small"])
        photo_rgb = Image.merge("RGB", (photo, photo, photo))
        photo_rgb.save(OUT / "refs" / f"{name}.png")
        heading = (YAW + az) % 360.0
        right_x = math.cos(math.radians(YAW))
        right_y = -math.sin(math.radians(YAW))
        cameras.append({
            "name": name,
            "role": "fit" if name == "fit_0" else "holdout",
            "xyz": [
                rover_x + right_x * side_m,
                rover_y + right_y * side_m,
                ground_z + MAST_M,
            ],
            "heading_deg": heading,
            "pitch_deg": el,
            "fov_deg": NAV_FOV,
            "width": IMG,
            "height": IMG,
            "image": f"refs/{name}.png",
        })
        credits.append({
            "name": name,
            "imageid": record["imageid"],
            "sol": record["sol"],
            "mast_az_deg": az,
            "mast_el_deg": el,
            "credit": record.get("credit"),
            "page": record.get("link"),
        })
    (OUT / "viewpoints.json").write_text(json.dumps({
        "frame": "site_enu",
        "poses_trusted": True,
        "requires_human": True,
        "registration_rms_m": None,
        "bubble_id": "jezero_sol9_landing",
        "pose_note": "Waypoint yaw plus rover-relative mast azimuth. Not a PDS site-frame bundle adjustment.",
        "landing_lat": LANDING[0],
        "landing_lon": LANDING[1],
        "cameras": cameras,
        "credits": credits,
    }, indent=2) + "\n")
    print("wrote", OUT, "dem", float(dem.min()), float(dem.max()), "rover", round(rover_x, 1), round(rover_y, 1))


if __name__ == "__main__":
    main()

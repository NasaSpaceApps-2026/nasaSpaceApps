"""Build a larger Jezero bundle for the vision refine loop.

The 80 m landing patch is flat. This crop is 1.8 km on the rough ground north
of Octavia E. Butler Landing, where the HiRISE DTM has the transverse sand
ridges. Height stays at 2 m so those ridges keep their shape. The texture is
finer: HiRISE brightness at 0.5 m, Mars orange from colorize.py, then the
grain of Mastcam-Z close-ups laid on top.

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
from vision_loop.enhance import enhance_color

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "contracts" / "fixtures" / "jezero_crater"
# Rough 800 m block found in the HiRISE window: western fan front, north of the landing site.
CENTER_LAT = 18.4818
CENTER_LON = 77.4358
N = 901
RES = 2.0
TEX_SCALE = 4  # 0.5 m texture on the 2 m height posts
NADIR_PX = 192
NADIR_FOV = 50.0
CLOSE_PX = 220


def site_xy(lat: float, lon: float) -> tuple[float, float]:
    x = R * math.cos(math.radians(LAT0)) * math.radians(lon - LON0)
    y = R * math.radians(lat - LAT0)
    return x, y


def _box_std(values: np.ndarray, radius: int) -> np.ndarray:
    """Local standard deviation. radius is in posts."""
    k = radius * 2 + 1
    padded = np.pad(values, radius, mode="edge")
    integral = np.pad(padded, ((1, 0), (1, 0)), mode="constant")
    integral = np.cumsum(np.cumsum(integral, axis=0), axis=1)
    def window(src):
        return src[k:, k:] - src[:-k, k:] - src[k:, :-k] + src[:-k, :-k]
    count = float(k * k)
    mean = window(integral) / count
    integral2 = np.pad(padded ** 2, ((1, 0), (1, 0)), mode="constant")
    integral2 = np.cumsum(np.cumsum(integral2, axis=0), axis=1)
    return np.sqrt(np.maximum(window(integral2) / count - mean ** 2, 0))


def _feature_points(dem: np.ndarray, x0: float, y0: float) -> dict[str, tuple[float, float, float]]:
    """Whoops, scarp, and a small crater, in site metres, plus the ground height."""
    gy, gx = np.gradient(dem, RES)
    slope = np.hypot(gx, gy)
    # Sand whoops are a tight ripple, not the one big cliff.
    band = _box_mean_radius(dem.astype(np.float64), 2) - _box_mean_radius(dem.astype(np.float64), 14)
    whoop_score = _box_std(band, 8) * (slope < np.quantile(slope, 0.82))
    wr, wc = np.unravel_index(int(np.argmax(whoop_score)), dem.shape)
    sr, sc = np.unravel_index(int(np.argmax(slope)), dem.shape)
    # A crater is a local low surrounded by higher ground.
    blur = _box_mean(dem.astype(np.float64))
    pit = blur - dem
    pr, pc = np.unravel_index(int(np.argmax(pit)), dem.shape)

    def xy(r, c):
        return float(x0 + c * RES), float(y0 - r * RES), float(dem[r, c])

    return {"whoops": xy(wr, wc), "scarp": xy(sr, sc), "crater": xy(pr, pc)}


def _box_mean_radius(values: np.ndarray, radius: int) -> np.ndarray:
    k = radius * 2 + 1
    padded = np.pad(values, radius, mode="edge")
    integral = np.pad(padded, ((1, 0), (1, 0)), mode="constant")
    integral = np.cumsum(np.cumsum(integral, axis=0), axis=1)
    window = integral[k:, k:] - integral[:-k, k:] - integral[k:, :-k] + integral[:-k, :-k]
    return window / float(k * k)


def _box_mean(values: np.ndarray) -> np.ndarray:
    return _box_mean_radius(values, 12)


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
    fine_n = (N - 1) * TEX_SCALE + 1
    fine_res = RES / TEX_SCALE
    fxs = x0 + np.arange(fine_n)[None, :] * fine_res
    fys = y0 - np.arange(fine_n)[:, None] * fine_res
    with rasterio.open(ortho_path) as ortho_src, rasterio.open("/vsicurl/" + DTM_URL) as dtm_src:
        dem = sample_raster(dtm_src, xs, ys).astype(np.float32)
        gray = sample_raster(ortho_src, fxs, fys)
    if not np.isfinite(dem).all():
        raise SystemExit("DTM crop has gaps")
    gray_u8 = np.clip(np.rint(gray), 0, 255).astype(np.uint8)
    fine = colorize_window(gray_u8, RAW, LAT0, LON0, SIZE, x0, y0, fine_res)
    photos = sorted((OUT / "refs" / "detail").glob("*.jpg"))
    texture = enhance_color(fine, photos) if photos else fine
    # The bundle loader still wants an ortho the same shape as the height grid.
    ortho = np.asarray(Image.fromarray(texture).resize((N, N), Image.Resampling.BOX))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "refs").mkdir(exist_ok=True)
    np.save(OUT / "dem.npy", dem)
    Image.fromarray(ortho).save(OUT / "ortho.png")
    Image.fromarray(texture).save(OUT / "texture.png")
    (OUT / "grid.json").write_text(json.dumps({
        "x0": x0,
        "y0": y0,
        "res_m": RES,
        "center_x": center_x,
        "center_y": center_y,
    }, indent=2) + "\n")
    ground = float(dem[N // 2, N // 2])
    altitude = half / math.tan(math.radians(NADIR_FOV / 2)) * 1.05
    grid = Grid(x0, y0, RES, center_x, center_y)
    features = _feature_points(dem, x0, y0)
    detail_names = [f"refs/detail/{path.name}" for path in photos]

    def close_camera(name: str, xyz, heading: float) -> dict:
        # Stand south of the spot and look toward it, so the camera is not buried in the slope.
        x, y, z = xyz[0], xyz[1] - 45.0, xyz[2] + 16.0
        cam = Camera(name, "fit", (x, y, z), 0.0, -32.0, 36.0, CLOSE_PX, CLOSE_PX, f"refs/{name}.png")
        from vision_loop.__main__ import _crop
        sub, sub_grid, row0, col0 = _crop(dem, grid, xyz[0], xyz[1], 220)
        image = render_mesh(sub, texture, cam, sub_grid, row0, col0, dem.shape)
        Image.fromarray(image).save(OUT / "refs" / f"{name}.png")
        return {
            "name": name,
            "role": "fit",
            "xyz": [x, y, z],
            "heading_deg": 0.0,
            "pitch_deg": -32.0,
            "fov_deg": 36.0,
            "width": CLOSE_PX,
            "height": CLOSE_PX,
            "image": f"refs/{name}.png",
            "crop_m": 220,
        }

    overhead = Camera(
        "satellite", "fit",
        (center_x, center_y, ground + altitude),
        0.0, -90.0, NADIR_FOV, NADIR_PX, NADIR_PX, "refs/nadir.png",
    )
    Image.fromarray(render_mesh(dem, texture, overhead, grid)).save(OUT / "refs" / "04_overview.png")
    cameras = [
        {
            "name": "overhead",
            "role": "nadir",
            "xyz": [center_x, center_y, ground + altitude],
            "heading_deg": 0.0,
            "pitch_deg": -90.0,
            "fov_deg": NADIR_FOV,
            "width": NADIR_PX,
            "height": NADIR_PX,
            "image": None,
        },
        close_camera("01_whoops", features["whoops"], 0.0),
        close_camera("02_scarp", features["scarp"], 0.0),
        close_camera("03_crater", features["crater"], 0.0),
        {
            "name": "04_overview",
            "role": "fit",
            "xyz": [center_x, center_y, ground + altitude],
            "heading_deg": 0.0,
            "pitch_deg": -90.0,
            "fov_deg": NADIR_FOV,
            "width": NADIR_PX,
            "height": NADIR_PX,
            "image": "refs/04_overview.png",
        },
    ]
    enhance_targets = []
    for name, key in (("01_whoops", "whoops"), ("02_scarp", "scarp")):
        xyz = features[key]
        enhance_targets.append({
            "name": name,
            "x": xyz[0],
            "y": xyz[1],
            "z": xyz[2] + 14.0,
            "heading_deg": 0.0,
            "pitch_deg": -50.0,
            "fov_deg": 28.0,
            "width": 240,
            "height": 240,
            "crop_m": 70,
            "detail": detail_names[0] if detail_names else "refs/nadir.png",
        })
    (OUT / "viewpoints.json").write_text(json.dumps({
        "frame": "site_enu",
        "poses_trusted": True,
        "requires_human": True,
        "registration_rms_m": None,
        "bubble_id": "jezero_fan_front",
        "ticks_per_image": 12,
        "pose_note": (
            "1.8 km of the Jezero fan front. Height is the 2 m HiRISE DTM, so the sand "
            "ridges already follow the picture. Texture is 0.5 m HiRISE colour plus "
            "Mastcam-Z grain. Close cameras are specific places. Do not move a ridge off its crest."
        ),
        "center_lat": CENTER_LAT,
        "center_lon": CENTER_LON,
        "cameras": cameras,
        "enhance_targets": enhance_targets,
        "credits": [{
            "name": "satellite",
            "source": "HiRISE ortho JEZ_hirise_soc_006, Viking MDIM tint, Mastcam-Z close-up grain",
        }],
    }, indent=2) + "\n")
    print(
        "wrote", OUT,
        "span_m", (N - 1) * RES,
        "relief", round(float(dem.max() - dem.min()), 1),
        "texture", texture.shape,
        "whoops", [round(v, 1) for v in features["whoops"][:2]],
        "rgb", [round(float(v), 1) for v in texture.reshape(-1, 3).mean(axis=0)],
    )


if __name__ == "__main__":
    main()

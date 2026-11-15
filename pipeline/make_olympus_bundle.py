"""Build a 5 km² bubble on the layered scarp of Olympus Mons.

HiRISE stereo DTM and red ortho PSP_001432_2015 / PSP_001630_2015
(NASA/JPL-Caltech/UArizona). No rover has landed here, so there is no
street-view catalogue of this cliff. The registered pictures are overhead
and close views of this HiRISE patch. Mastcam-Z frames from Jezero are
kept only as the kind of sand and rock skin the enhance pass may grow.
They are not photographs of Olympus Mons.

Height is 2 m. Texture is the 1 m ortho, tinted Mars orange, plus fine grain.
"""

from __future__ import annotations

import io
import json
import math
import shutil
from pathlib import Path

import numpy as np
import rasterio
import requests
from PIL import Image
from rasterio.windows import Window

from bubble.geom import Camera, Grid
from colorize import colorize, viking_chroma
from vision_loop.enhance import enhance_color

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "olympus"
OUT = ROOT / "contracts" / "fixtures" / "olympus_scarp"
DETAIL = ROOT / "contracts" / "fixtures" / "jezero_crater" / "refs" / "detail"
DTM_URL = (
    "https://www.uahirise.org/PDS/DTM/PSP/ORB_001400_001499/"
    "PSP_001432_2015_PSP_001630_2015/DTEEC_001432_2015_001630_2015_U01.IMG"
)
ORTHO_URL = (
    "https://www.uahirise.org/PDS/DTM/PSP/ORB_001400_001499/"
    "PSP_001432_2015_PSP_001630_2015/PSP_001432_2015_RED_C_01_ORTHO.JP2"
)
# Center of the steep layered face, in the DTM's own easting/northing.
CENTER_EAST = -2238.2265050581063
CENTER_NORTH = 1276768.0603577567
CENTER_LAT = 21.5548278127083
CENTER_LON = -137.68021154557286  # 222.32 E
N = 1119
RES = 2.0
TEX_SCALE = 2  # 1 m texture, the native ortho
SIDE_M = (N - 1) * RES  # 2236 m, 5.00 km²


def _sample(src, east: np.ndarray, north: np.ndarray) -> np.ndarray:
    cols, rows = (~src.transform) * (east, north)
    cols = np.asarray(cols, dtype=np.float64)
    rows = np.asarray(rows, dtype=np.float64)
    c0 = max(0, int(np.floor(cols.min())) - 2)
    r0 = max(0, int(np.floor(rows.min())) - 2)
    c1 = min(src.width - 1, int(np.ceil(cols.max())) + 2)
    r1 = min(src.height - 1, int(np.ceil(rows.max())) + 2)
    data = src.read(1, window=Window(c0, r0, c1 - c0 + 1, r1 - r0 + 1)).astype(np.float64)
    nodata = src.nodata
    if nodata is not None:
        data[np.isclose(data, nodata) | (data < -1e30)] = np.nan
    lc = cols - c0
    lr = rows - r0
    c0i = np.floor(lc).astype(np.int32)
    r0i = np.floor(lr).astype(np.int32)
    c1i = np.clip(c0i + 1, 0, data.shape[1] - 1)
    r1i = np.clip(r0i + 1, 0, data.shape[0] - 1)
    c0i = np.clip(c0i, 0, data.shape[1] - 1)
    r0i = np.clip(r0i, 0, data.shape[0] - 1)
    fc = lc - np.floor(lc)
    fr = lr - np.floor(lr)
    v00 = data[r0i, c0i]
    v01 = data[r0i, c1i]
    v10 = data[r1i, c0i]
    v11 = data[r1i, c1i]
    return v00 * (1 - fr) * (1 - fc) + v01 * (1 - fr) * fc + v10 * fr * (1 - fc) + v11 * fr * fc


def _grid(n: int, res: float) -> tuple[np.ndarray, np.ndarray]:
    half = (n // 2) * res
    xs = CENTER_EAST + (np.arange(n) - n // 2) * res
    ys = CENTER_NORTH + (n // 2 - np.arange(n)) * res
    east, north = np.meshgrid(xs, ys)
    return east, north


def _fetch_viking() -> None:
    if (RAW / "viking_color.png").exists() and (RAW / "viking_color.json").exists():
        return
    layer = "Mars_Viking_MDIM21_ClrMosaic_global_232m"
    url = "https://trek.nasa.gov/tiles/Mars/EQ/" + layer + "/1.0.0//default/default028mm/{z}/{row}/{col}.jpg"
    z, cw, ch = 7, 2 ** 8, 2 ** 7
    col0 = int((CENTER_LON + 180) / 360 * cw)
    row0 = int((90 - CENTER_LAT) / 180 * ch)
    cols, rows = range(col0 - 1, col0 + 2), range(row0 - 1, row0 + 2)
    canvas = Image.new("RGB", (256 * 3, 256 * 3))
    for ri, row in enumerate(rows):
        for ci, col in enumerate(cols):
            resp = requests.get(url.format(z=z, row=row, col=col), timeout=60)
            resp.raise_for_status()
            canvas.paste(Image.open(io.BytesIO(resp.content)).convert("RGB"), (ci * 256, ri * 256))
    RAW.mkdir(parents=True, exist_ok=True)
    canvas.save(RAW / "viking_color.png")
    west = -180 + cols[0] / cw * 360
    north = 90 - rows[0] / ch * 180
    meta = {
        "west": west, "east": west + 3 / cw * 360,
        "north": north, "south": north - 3 / ch * 180,
        "width": 768, "height": 768,
    }
    (RAW / "viking_color.json").write_text(json.dumps(meta))


def _features(dem: np.ndarray) -> dict[str, tuple[float, float, float]]:
    gy, gx = np.gradient(dem, RES)
    slope = np.hypot(gx, gy)
    slope = slope.copy()
    slope[:40, :] = 0
    slope[-40:, :] = 0
    slope[:, :40] = 0
    slope[:, -40:] = 0
    sr, sc = np.unravel_index(int(np.argmax(slope)), dem.shape)
    # A bench: low slope on the high side of the face.
    high = dem > np.quantile(dem, 0.7)
    bench = np.where(high, -slope, -np.inf)
    br, bc = np.unravel_index(int(np.argmax(bench)), dem.shape)
    low = dem < np.quantile(dem, 0.3)
    floor = np.where(low, -slope, -np.inf)
    fr, fc = np.unravel_index(int(np.argmax(floor)), dem.shape)

    def xy(r, c):
        return (float((c - dem.shape[1] // 2) * RES), float((dem.shape[0] // 2 - r) * RES), float(dem[r, c]))

    return {"scarp": xy(sr, sc), "bench": xy(br, bc), "floor": xy(fr, fc)}


def main() -> None:
    _fetch_viking()
    half = (N // 2) * RES
    east, north = _grid(N, RES)
    fine_n = (N - 1) * TEX_SCALE + 1
    fine_res = RES / TEX_SCALE
    feast, fnorth = _grid(fine_n, fine_res)
    with rasterio.open("/vsicurl/" + DTM_URL) as dtm, rasterio.open("/vsicurl/" + ORTHO_URL) as ortho_src:
        dem = _sample(dtm, east, north).astype(np.float32)
        gray = _sample(ortho_src, feast, fnorth)
    holes = ~np.isfinite(dem)
    if holes.mean() > 0.05:
        raise SystemExit(f"DTM crop is {holes.mean():.0%} empty")
    for _ in range(80):
        holes = ~np.isfinite(dem)
        if not holes.any():
            break
        padded = np.pad(dem, 1, mode="edge")
        acc = np.zeros_like(dem)
        count = np.zeros(dem.shape)
        for dr in range(3):
            for dc in range(3):
                if dr == 1 and dc == 1:
                    continue
                window = padded[dr:dr + dem.shape[0], dc:dc + dem.shape[1]]
                good = np.isfinite(window)
                acc[good] += window[good]
                count[good] += 1
        fill = holes & (count > 0)
        dem[fill] = acc[fill] / count[fill]
    if not np.isfinite(dem).all():
        dem[~np.isfinite(dem)] = float(np.nanmedian(dem))
    gray_u8 = np.clip(np.rint(np.nan_to_num(gray, nan=0)), 0, 255).astype(np.uint8)
    chroma = viking_chroma(RAW, CENTER_LAT, CENTER_LON, SIDE_M, fine_n)
    fine = colorize(gray_u8, chroma)
    OUT.mkdir(parents=True, exist_ok=True)
    detail = OUT / "refs" / "detail"
    detail.mkdir(parents=True, exist_ok=True)
    for src in sorted(DETAIL.glob("*.jpg")):
        shutil.copy(src, detail / src.name)
    photos = sorted(detail.glob("*.jpg"))
    texture = enhance_color(fine, photos) if photos else fine
    ortho = np.asarray(Image.fromarray(texture).resize((N, N), Image.Resampling.BOX))
    np.save(OUT / "dem.npy", dem)
    Image.fromarray(ortho).save(OUT / "ortho.png")
    Image.fromarray(texture).save(OUT / "texture.png")
    (OUT / "grid.json").write_text(json.dumps({
        "x0": -half, "y0": half, "res_m": RES, "center_x": 0.0, "center_y": 0.0,
    }, indent=2) + "\n")
    grid = Grid(-half, half, RES, 0.0, 0.0)
    features = _features(dem)
    from vision_loop.__main__ import _crop
    from bubble.render import render_mesh

    def close(name: str, xyz, heading: float) -> dict:
        # Stand off the face and look at it. Heading 90 looks east, up the scarp.
        x, y, z = xyz[0] - 50.0, xyz[1], xyz[2] + 30.0
        cam = Camera(name, "fit", (x, y, z), heading, -28.0, 40.0, 220, 220, f"refs/{name}.png")
        sub, sub_grid, row0, col0 = _crop(dem, grid, xyz[0], xyz[1], 280)
        image = render_mesh(sub, texture, cam, sub_grid, row0, col0, dem.shape)
        Image.fromarray(image).save(OUT / "refs" / f"{name}.png")
        return {
            "name": name, "role": "fit", "xyz": [x, y, z],
            "heading_deg": heading, "pitch_deg": -28.0, "fov_deg": 40.0,
            "width": 220, "height": 220, "image": f"refs/{name}.png", "crop_m": 280,
        }

    ground = float(dem[N // 2, N // 2])
    altitude = half / math.tan(math.radians(25)) * 1.05
    overview = Camera("04_overview", "fit", (0, 0, ground + altitude), 0, -90, 50, 192, 192, "refs/04_overview.png")
    Image.fromarray(render_mesh(dem, texture, overview, grid)).save(OUT / "refs" / "04_overview.png")
    cameras = [
        {
            "name": "overhead", "role": "nadir",
            "xyz": [0, 0, ground + altitude],
            "heading_deg": 0, "pitch_deg": -90, "fov_deg": 50,
            "width": 192, "height": 192, "image": None,
        },
        close("01_scarp", features["scarp"], 90.0),
        close("02_bench", features["bench"], 90.0),
        close("03_floor", features["floor"], 90.0),
        {
            "name": "04_overview", "role": "fit",
            "xyz": [0, 0, ground + altitude],
            "heading_deg": 0, "pitch_deg": -90, "fov_deg": 50,
            "width": 192, "height": 192, "image": "refs/04_overview.png",
        },
    ]
    detail_name = f"refs/detail/{photos[0].name}" if photos else "refs/04_overview.png"
    enhance_targets = []
    for name, key in (("01_scarp", "scarp"), ("02_bench", "bench")):
        xyz = features[key]
        enhance_targets.append({
            "name": name, "x": xyz[0], "y": xyz[1], "z": xyz[2] + 20,
            "heading_deg": 90, "pitch_deg": -40, "fov_deg": 30,
            "width": 240, "height": 240, "crop_m": 80, "detail": detail_name,
        })
    (OUT / "viewpoints.json").write_text(json.dumps({
        "frame": "local_east_north on the HiRISE DTM",
        "poses_trusted": True,
        "requires_human": True,
        "registration_rms_m": None,
        "bubble_id": "olympus_scarp",
        "ticks_per_image": 8,
        "pose_note": (
            "5.00 km² of the layered scarp of Olympus Mons, HiRISE PSP_001432_2015. "
            "No rover images of this cliff exist. Close-up Mastcam frames are texture "
            "references from Jezero, not located photos of Olympus Mons. Keep the orange "
            "satellite picture. Bumps follow layers already in the elevation model."
        ),
        "center_lat": CENTER_LAT,
        "center_lon_east": 222.31978845442714,
        "cameras": cameras,
        "enhance_targets": enhance_targets,
        "credits": [{
            "name": "hirise",
            "source": "NASA/JPL-Caltech/UArizona HiRISE DTM DTEEC_001432_2015_001630_2015 and red ortho PSP_001432_2015. Viking MDIM tint. Mastcam-Z grain is from Jezero, not this site.",
        }],
    }, indent=2) + "\n")
    area = SIDE_M * SIDE_M / 1e6
    print(
        "wrote", OUT, "km2", round(area, 3),
        "relief", round(float(dem.max() - dem.min()), 1),
        "texture", texture.shape,
        "rgb", [round(float(v), 1) for v in texture.reshape(-1, 3).mean(axis=0)],
    )


if __name__ == "__main__":
    main()

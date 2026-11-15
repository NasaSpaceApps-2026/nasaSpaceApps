"""Pack the sol 9 refine results as sandbox worlds the rover can drive.

The sandbox loads one origin-centred site (manifest.json plus a height tile
and an ortho tile). These two worlds are that site for the same 80 m Jezero
patch: photo projection keeps the HiRISE heights and drapes the Navcam, and
the vision loop uses the edited heights and texture. Site (0, 0) is the
bubble centre. Jezero site metres are center_x/center_y in the manifest.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np
from PIL import Image

HEIGHT_PX = 257
IMG_PX = 512
SIZE_M = 80.0


def encode_rgb(elev: np.ndarray) -> np.ndarray:
    v = np.rint((np.asarray(elev, dtype=np.float64) + 10000.0) / 0.01).astype(np.int64)
    if int(v.min()) < 0 or int(v.max()) > 0xFFFFFF:
        raise ValueError("elevation outside encodable range")
    return np.stack([(v >> 16) & 255, (v >> 8) & 255, v & 255], axis=-1).astype(np.uint8)


def decode_rgb(rgb: np.ndarray) -> np.ndarray:
    rgb = np.asarray(rgb, dtype=np.int64)
    return -10000.0 + 0.01 * (rgb[..., 0] * 65536 + rgb[..., 1] * 256 + rgb[..., 2])


def _bilinear(values: np.ndarray, cf: np.ndarray, rf: np.ndarray) -> np.ndarray:
    rows, cols = values.shape
    c0 = np.floor(cf).astype(np.int32)
    r0 = np.floor(rf).astype(np.int32)
    c0 = np.clip(c0, 0, cols - 1)
    r0 = np.clip(r0, 0, rows - 1)
    c1 = np.clip(c0 + 1, 0, cols - 1)
    r1 = np.clip(r0 + 1, 0, rows - 1)
    fc = cf - np.floor(cf)
    fr = rf - np.floor(rf)
    v00 = values[r0, c0]
    v01 = values[r0, c1]
    v10 = values[r1, c0]
    v11 = values[r1, c1]
    return (v00 * (1 - fc) + v01 * fc) * (1 - fr) + (v10 * (1 - fc) + v11 * fc) * fr


def resample_heights(values: np.ndarray, x0: float, y0: float, res_m: float, center_x: float, center_y: float, size_m: float = SIZE_M) -> np.ndarray:
    """Sample a north-up vertex grid onto the sandbox's level-0 posts."""
    step = size_m / (HEIGHT_PX - 1)
    xs = -size_m / 2 + np.arange(HEIGHT_PX) * step
    ys = size_m / 2 - np.arange(HEIGHT_PX) * step
    xx, yy = np.meshgrid(xs, ys)
    cf = ((xx + center_x) - x0) / res_m
    rf = (y0 - (yy + center_y)) / res_m
    return _bilinear(np.asarray(values, dtype=np.float64), cf, rf)


def _png_from_glb(path: Path) -> Image.Image:
    data = path.read_bytes()
    if data[:4] != b"glTF":
        raise ValueError(f"{path} is not a glb")
    length, typ = struct.unpack_from("<I4s", data, 12)
    if typ != b"JSON":
        raise ValueError(f"{path} has no JSON chunk")
    doc = json.loads(data[20 : 20 + length])
    off = 20 + length
    blen, btyp = struct.unpack_from("<I4s", data, off)
    if btyp != b"BIN\x00":
        raise ValueError(f"{path} has no binary chunk")
    blob = data[off + 8 : off + 8 + blen]
    image = doc["images"][0]
    view = doc["bufferViews"][image["bufferView"]]
    start = int(view.get("byteOffset", 0))
    png = blob[start : start + int(view["byteLength"])]
    from io import BytesIO
    return Image.open(BytesIO(png)).convert("RGB")


def _grid(repo: Path) -> dict:
    return json.loads((repo / "contracts" / "fixtures" / "jezero_sol9" / "grid.json").read_text())


def projection_source(repo: Path) -> tuple[np.ndarray, Image.Image, dict]:
    dem = np.load(repo / "contracts" / "fixtures" / "jezero_sol9" / "dem.npy")
    texture = _png_from_glb(repo / "contracts" / "fixtures" / "jezero_sol9_projection" / "refined_mesh.glb")
    return np.asarray(dem, dtype=np.float64), texture, _grid(repo)


def vision_source(repo: Path) -> tuple[np.ndarray, Image.Image, dict]:
    header = json.loads((repo / "contracts" / "fixtures" / "jezero_sol9_vision" / "drive" / "height.json").read_text())
    count = int(header["width"]) * int(header["height"])
    raw = np.fromfile(repo / "contracts" / "fixtures" / "jezero_sol9_vision" / "drive" / "height.u16", dtype="<u2")
    if raw.size != count:
        raise ValueError(f"vision height.u16 has {raw.size} values, expected {count}")
    elev = float(header["base"]) + 0.01 * raw.reshape(int(header["height"]), int(header["width"])).astype(np.float64)
    texture = Image.open(repo / "contracts" / "fixtures" / "jezero_sol9_vision" / "drive" / "texture.png").convert("RGB")
    grid = _grid(repo)
    grid = {**grid, "x0": float(header["x0"]), "y0": float(header["y0"]), "res_m": float(header["res_m"])}
    return elev, texture, grid


def write_site(out: Path, site: str, title: str, credit: str, elev: np.ndarray, texture: Image.Image, grid: dict, size_m: float = SIZE_M) -> None:
    posts = resample_heights(
        elev, float(grid["x0"]), float(grid["y0"]), float(grid["res_m"]),
        float(grid["center_x"]), float(grid["center_y"]), size_m,
    )
    height_path = out / "tiles" / "height" / "0" / "0" / "0.png"
    ortho_path = out / "tiles" / "ortho" / "0" / "0" / "0.webp"
    height_path.parent.mkdir(parents=True, exist_ok=True)
    ortho_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(encode_rgb(posts)).save(height_path)
    texture.resize((IMG_PX, IMG_PX), Image.Resampling.BILINEAR).save(ortho_path, "WEBP", quality=90)
    manifest = {
        "site": site,
        "fixture": True,
        "lat0": 18.44462715,
        "lon0": 77.45088572,
        "radius_m": 3396190,
        "size_m": size_m,
        "elev_min": float(np.min(posts)),
        "elev_max": float(np.max(posts)),
        "center_x": float(grid["center_x"]),
        "center_y": float(grid["center_y"]),
        "height": {"max_level": 0, "tile_px": HEIGHT_PX},
        "layers": [{
            "id": "ortho",
            "title": title,
            "mission": "Mars 2020",
            "instrument": "Navcam",
            "max_level": 0,
            "kind": "base",
            "credit": credit,
        }],
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def export_worlds(repo: Path, out_root: Path) -> None:
    worlds = (
        ("projection", "jezero_sol9_projection", "Photo projection",
         "Jezero sol 9 Navcam draped on the frozen HiRISE heights", projection_source),
        ("vision", "jezero_sol9_vision", "Vision refine",
         "Jezero sol 9 heights and texture edited by the vision loop", vision_source),
    )
    for name, site, title, credit, source in worlds:
        elev, texture, grid = source(repo)
        write_site(out_root / name, site, title, credit, elev, texture, grid)


def _from_drive(drive: Path) -> tuple[np.ndarray, Image.Image, dict]:
    header = json.loads((drive / "height.json").read_text())
    raw = np.fromfile(drive / "height.u16", dtype="<u2")
    elev = float(header["base"]) + 0.01 * raw.reshape(int(header["height"]), int(header["width"])).astype(np.float64)
    texture = Image.open(drive / "texture.png").convert("RGB")
    return elev, texture, header


def export_crater(repo: Path, out_root: Path) -> None:
    """The larger colourised HiRISE patch, when that bundle has been built.

    A finished vision run in jezero_crater_vision replaces the baseline heights.
    """
    bundle = repo / "contracts" / "fixtures" / "jezero_crater"
    vision = repo / "contracts" / "fixtures" / "jezero_crater_vision" / "drive"
    if not (bundle / "dem.npy").exists() and not (vision / "height.u16").exists():
        return
    grid = json.loads((bundle / "grid.json").read_text())
    credit = "HiRISE brightness and dirt pattern, Mars orange from the anchored Viking tint"
    if (vision / "height.u16").exists():
        dem, texture, header = _from_drive(vision)
        grid = {**grid, "x0": float(header["x0"]), "y0": float(header["y0"]), "res_m": float(header["res_m"])}
        credit = credit + ", plus the vision-loop crest bumps"
    else:
        dem = np.load(bundle / "dem.npy")
        texture = Image.open(bundle / "ortho.png").convert("RGB")
    size_m = (dem.shape[1] - 1) * float(grid["res_m"])
    write_site(
        out_root / "crater",
        "jezero_crater",
        "Jezero fan front",
        credit,
        dem,
        texture,
        grid,
        size_m,
    )


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    out = repo / "contracts" / "fixtures" / "rover_worlds"
    export_worlds(repo, out)
    export_crater(repo, out)
    print(out)


if __name__ == "__main__":
    main()

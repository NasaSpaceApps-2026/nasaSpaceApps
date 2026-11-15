"""Rebuild bubble texture.png files with the current colorize() and refresh rover_world tiles."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from colorize import ANCHOR, BRIGHTEN, ROLLOFF, STREET_ANCHOR, _luminance_rolloff, colorize
from export_rover_worlds import export_crater, export_olympus


def _invert_luminance_rolloff(y_out: np.ndarray) -> np.ndarray:
    """Inverse of _luminance_rolloff for scalar y."""
    y = np.asarray(y_out, dtype=np.float32)
    return y / (1.0 + ROLLOFF * (1.0 - np.clip(y, 0.0, None)))


def recover_gray_per_channel_bake(rgb: np.ndarray, anchor: np.ndarray = STREET_ANCHOR) -> np.ndarray:
    """Undo the per-channel rolloff colorize() used for the first STREET_ANCHOR bake."""
    rgb_f = np.asarray(rgb, dtype=np.float32) / 255.0
    pre_roll = rgb_f * (1.0 + ROLLOFF * np.clip(rgb_f, 0.0, None)) / (1.0 + ROLLOFF)
    norm = float((anchor * np.array([0.30, 0.59, 0.11])).sum())
    pre_norm = pre_roll * norm / 0.92
    y = np.mean(pre_norm / anchor, axis=-1)
    gray = np.clip(y / BRIGHTEN * 255.0, 0, 255).astype(np.uint8)
    return gray


def recover_gray(rgb: np.ndarray, anchor: np.ndarray = STREET_ANCHOR) -> np.ndarray:
    """Undo colorize() for uniform chroma (luminance rolloff + hue-preserving scale)."""
    rgb_f = np.asarray(rgb, dtype=np.float32) / 255.0
    mx = np.max(rgb_f, axis=-1)
    pre = rgb_f.copy()
    heavy = mx > 1.0 - 1.0 / 255.0
    if np.any(heavy):
        pre[heavy] = rgb_f[heavy] * mx[heavy, None]
    norm = float((anchor * np.array([0.30, 0.59, 0.11])).sum())
    tinted = anchor / norm * 0.92
    y = np.max(pre / tinted, axis=-1)
    y = _invert_luminance_rolloff(y)
    gray = np.clip(y / BRIGHTEN * 255.0, 0, 255).astype(np.uint8)
    return gray


def recolor_texture(path: Path, *, from_per_channel: bool = False) -> None:
    rgb = np.asarray(Image.open(path).convert("RGB"))
    gray = recover_gray_per_channel_bake(rgb) if from_per_channel else recover_gray(rgb)
    chroma = np.ones((*gray.shape, 3), dtype=np.float32)
    out = colorize(gray, chroma)
    Image.fromarray(out).save(path)


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    textures = [
        repo / "contracts" / "fixtures" / "jezero_crater" / "texture.png",
        repo / "contracts" / "fixtures" / "olympus_scarp" / "texture.png",
        repo / "contracts" / "fixtures" / "jezero_crater_vision" / "drive" / "texture.png",
    ]
    for texture in textures:
        if not texture.exists():
            continue
        recolor_texture(texture, from_per_channel=True)
        bundle = texture.parent
        if bundle.name == "drive":
            bundle = bundle.parent
        if bundle.name in ("jezero_crater", "olympus_scarp"):
            ortho = np.asarray(Image.open(texture))
            grid_n = json_sidecar_size(bundle)
            if grid_n:
                Image.fromarray(ortho).resize((grid_n, grid_n), Image.Resampling.BOX).save(bundle / "ortho.png")
    out = repo / "contracts" / "fixtures" / "rover_worlds"
    export_crater(repo, out)
    export_olympus(repo, out)
    print(f"recolored with STREET_ANCHOR {STREET_ANCHOR.tolist()}, ANCHOR matches {np.allclose(ANCHOR, STREET_ANCHOR)}")


def json_sidecar_size(bundle: Path) -> int | None:
    dem_path = bundle / "dem.npy"
    if not dem_path.exists():
        return None
    dem = np.load(dem_path)
    return int(dem.shape[0])


if __name__ == "__main__":
    main()
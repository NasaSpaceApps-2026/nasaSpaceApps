"""Rebuild bubble texture.png files with the current STREET_ANCHOR and refresh rover_world tiles."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from colorize import ANCHOR, BRIGHTEN, ROLLOFF, STREET_ANCHOR, colorize
from export_rover_worlds import export_crater, export_olympus

# Baked fixtures used this anchor before the globe-matched street tint.
LEGACY_ANCHOR = np.array([1.00, 0.76, 0.58], dtype=np.float32)


def recover_gray(rgb: np.ndarray, anchor: np.ndarray = LEGACY_ANCHOR) -> np.ndarray:
    """Undo colorize() for uniform chroma, as far as the rolloff allows."""
    rgb_f = np.asarray(rgb, dtype=np.float32) / 255.0
    pre_roll = rgb_f * (1.0 + ROLLOFF * np.clip(rgb_f, 0.0, None)) / (1.0 + ROLLOFF)
    norm = float((anchor * np.array([0.30, 0.59, 0.11])).sum())
    pre_norm = pre_roll * norm / 0.92
    y = np.mean(pre_norm / anchor, axis=-1)
    gray = np.clip(y / BRIGHTEN * 255.0, 0, 255).astype(np.uint8)
    return gray


def recolor_texture(path: Path) -> None:
    rgb = np.asarray(Image.open(path).convert("RGB"))
    gray = recover_gray(rgb)
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
        recolor_texture(texture)
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
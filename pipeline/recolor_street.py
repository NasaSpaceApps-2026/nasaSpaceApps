"""Retint baked bubble textures to STREET_ANCHOR hue without inverting colorize()."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

from colorize import STREET_ANCHOR
from export_rover_worlds import export_crater, export_olympus

RESTORE_REF = "89b5527"

TEXTURE_PATHS = (
    "contracts/fixtures/jezero_crater/texture.png",
    "contracts/fixtures/olympus_scarp/texture.png",
    "contracts/fixtures/jezero_crater_vision/drive/texture.png",
)


def retint_street_rgb(rgb: np.ndarray) -> np.ndarray:
    """Keep per-pixel luminance from baked ortho; map hue to STREET_ANCHOR ratios."""
    rgb = np.asarray(rgb, dtype=np.float32)
    luma = 0.30 * rgb[..., 0] + 0.59 * rgb[..., 1] + 0.11 * rgb[..., 2]
    r, g, b = STREET_ANCHOR
    out = np.stack([luma, (g / r) * luma, (b / r) * luma], axis=-1)
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def restore_texture(repo: Path, rel: str, ref: str = RESTORE_REF) -> Path:
    dest = repo / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(subprocess.check_output(["git", "show", f"{ref}:{rel}"]))
    return dest


def retint_texture(path: Path) -> None:
    rgb = np.asarray(Image.open(path).convert("RGB"))
    Image.fromarray(retint_street_rgb(rgb)).save(path)


def json_sidecar_size(bundle: Path) -> int | None:
    dem_path = bundle / "dem.npy"
    if not dem_path.exists():
        return None
    dem = np.load(dem_path)
    return int(dem.shape[0])


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    for rel in TEXTURE_PATHS:
        texture = restore_texture(repo, rel)
        retint_texture(texture)
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
    print(f"retinted textures from {RESTORE_REF} to STREET_ANCHOR {STREET_ANCHOR.tolist()}")


if __name__ == "__main__":
    main()
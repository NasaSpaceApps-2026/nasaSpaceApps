"""Colourise the greyscale HiRISE ortho (pan-sharpening, FALSE COLOUR).

Luminance and all fine detail come from HiRISE. Colour comes from the Viking MDIM 2.1 colourised
mosaic (about 330 m/px): only its RELATIVE variation across the site is used (darker/bluer basalt
versus redder dust). The overall hue is anchored to a typical Mars surface tone, an ASSUMED value,
because the Viking colourisation over this region is a stylised grey-purple, not surface colour.
"""
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

# Assumed mean chromaticity of the Jezero surface relative to green (typical tan-orange Mars dust).
ANCHOR = np.array([1.00, 0.76, 0.58], dtype=np.float32)
VARIATION = 0.6   # strength of the redness variation taken from Viking (z-score of R/B, clipped)
AXIS = np.array([0.10, 0.0, -0.14], dtype=np.float32)  # redder = more R, less B; never green/magenta
BRIGHTEN = 1.3    # HiRISE ortho DN are dark (mean about 0.4); lift for display only
ROLLOFF = 0.5     # soft highlight rolloff so bright outcrops do not clip to yellow-white


def viking_chroma(raw: Path, lat0, lon0, size_m, n, R=3396190.0):
    """Per-pixel tint multiplier (n x n x 3, about 1) over the site square, from the Viking mosaic."""
    meta = json.loads((raw / "viking_color.json").read_text())
    img = Image.open(raw / "viking_color.png").convert("RGB")
    half = size_m / 2
    d_lon = math.degrees(half / (R * math.cos(math.radians(lat0))))
    d_lat = math.degrees(half / R)
    w, e, s_, n_ = lon0 - d_lon, lon0 + d_lon, lat0 - d_lat, lat0 + d_lat
    sx = meta["width"] / (meta["east"] - meta["west"])
    sy = meta["height"] / (meta["north"] - meta["south"])
    box = ((w - meta["west"]) * sx, (meta["north"] - n_) * sy, (e - meta["west"]) * sx, (meta["north"] - s_) * sy)
    # resize with a float source box: bicubic upsample of the ~25 px footprint to the site grid
    up = np.asarray(img.resize((n, n), Image.BICUBIC, box=box), dtype=np.float32)
    lum = up.mean(axis=2, keepdims=True) + 1e-3
    chroma = up / lum                                   # unit-luminance colour ratios
    # One scalar axis only: how red vs blue the Viking colour is, as a z-score over the site. Letting
    # R, G and B vary independently pushed the hue to yellow-green and magenta, which looks unnatural.
    red = chroma[..., 0] / np.maximum(chroma[..., 2], 1e-3)
    z = np.clip((red - red.mean()) / (red.std() + 1e-6), -2.5, 2.5)
    return 1.0 + (VARIATION * z)[..., None] * AXIS


def colorize(gray: np.ndarray, chroma: np.ndarray) -> np.ndarray:
    """gray: (n,n) HiRISE DN 0..255 -> (n,n,3) uint8."""
    y = np.clip(gray.astype(np.float32) / 255.0 * BRIGHTEN, 0, 1.2)[..., None]
    rgb = y * chroma * ANCHOR
    # keep luminance roughly equal to the HiRISE value after tinting
    norm = (ANCHOR * np.array([0.30, 0.59, 0.11])).sum()
    rgb = rgb / norm * 0.92
    rgb = rgb * (1 + ROLLOFF) / (1 + ROLLOFF * np.clip(rgb, 0, None))  # 1 -> 1, compresses highlights
    return (np.clip(rgb, 0, 1) * 255).astype(np.uint8)


def colorize_banded(gray: np.ndarray, raw: Path, lat0, lon0, size_m, coarse=1024, band=512) -> np.ndarray:
    """Memory-safe colourise of the full-size grey ortho: returns (n, n, 3) uint8.
    The chroma field is computed once on a coarse grid and upsampled band by band."""
    n = gray.shape[0]
    f = n / coarse
    ch = viking_chroma(raw, lat0, lon0, size_m, coarse)                 # (coarse, coarse, 3)
    planes = [Image.fromarray(np.ascontiguousarray(ch[..., k]), mode="F") for k in range(3)]
    out = np.empty((n, n, 3), dtype=np.uint8)
    for r0 in range(0, n, band):
        r1 = min(n, r0 + band)
        box = (0.0, r0 / f, float(coarse), r1 / f)
        up = np.stack([np.asarray(p.resize((n, r1 - r0), Image.BICUBIC, box=box), dtype=np.float32) for p in planes], axis=-1)
        out[r0:r1] = colorize(gray[r0:r1], up)
    return out

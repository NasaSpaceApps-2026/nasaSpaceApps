"""Detector for zones where the HiRISE DTM texture is visibly noisier (stereo-matching noise).

Why it matters: slope and roughness are derivatives of elevation. Random elevation noise of
amplitude e adds ~e/(baseline) to the gradient, so noisy DTM texture inflates slope_2m and
rough_5x5 although the ground is not steeper. Elevations are never modified; only flagged.

Method, in the SOURCE grid (1 m, before resampling):
  1. High-frequency energy: |L| where L = 4-neighbour Laplacian z[r-1,c]+z[r+1,c]+z[r,c-1]+z[r,c+1]-4z.
     Uniform slopes give L = 0 and smooth curvature gives tiny L, so L isolates cell-to-cell
     texture, whatever the tilt.
  2. Block statistic: median of |L| over non-overlapping BLOCK x BLOCK (16 m) blocks. A median is
     used so isolated rocks/craters and thin one- or two-row seam lines (seams.py) cannot lift a
     block; the texture must be high over most of the block.
  3. Log-scale robust z-score against the site-wide distribution of the same statistic:
     z = (log S - median(log S)) / (1.4826 * MAD(log S)).
  4. Gaussian smoothing (SMOOTH_BLOCKS blocks) to merge into contiguous zones, then hysteresis
     thresholding (seed z > Z_HIGH, grown through z > Z_LOW) so a zone is a coherent region and not
     speckle, then removal of components smaller than MIN_AREA_M2 and filling of small holes.
Limits: the score is relative to this site (a site that is noisy everywhere would not be
flagged), it cannot tell noise from genuinely rough terrain (boulder fields, crater rims) of
similar high-frequency energy, and thresholds are judged by eye on docs/noise_check.png,
not against ground truth.
"""
import numpy as np
from scipy import ndimage

BLOCK = 16
SMOOTH_BLOCKS = 3.0
Z_HIGH = 3.0
Z_LOW = 1.5
MIN_AREA_M2 = 40_000.0      # 4 hectares
HOLE_AREA_M2 = 20_000.0


def block_energy(z):
    """Median |Laplacian| per BLOCK x BLOCK block (NaN where fewer than half the cells valid)."""
    H, W = z.shape
    lap = np.full(z.shape, np.nan, np.float32)
    lap[1:-1, 1:-1] = np.abs(z[:-2, 1:-1] + z[2:, 1:-1] + z[1:-1, :-2] + z[1:-1, 2:] - 4 * z[1:-1, 1:-1])
    nr, nc = H // BLOCK, W // BLOCK
    b = lap[:nr * BLOCK, :nc * BLOCK].reshape(nr, BLOCK, nc, BLOCK).transpose(0, 2, 1, 3).reshape(nr, nc, -1)
    ok = np.isfinite(b).sum(-1) >= b.shape[-1] // 2
    with np.errstate(all="ignore"):
        S = np.nanmedian(b, axis=-1)
    return np.where(ok, S, np.nan)


def detect_noise(z, res_m=1.0):
    """z: HiRISE window (1 m, NaN nodata). Returns (zone_mask_blocks, zscore_blocks, info).
    Block grid cell (i, j) covers source rows i*BLOCK.., cols j*BLOCK.."""
    S = block_energy(z)
    ok = np.isfinite(S) & (S > 0)
    logS = np.where(ok, np.log10(np.where(ok, S, 1.0)), np.nan)
    med = np.nanmedian(logS)
    mad = 1.4826 * np.nanmedian(np.abs(logS[ok] - med))
    zs = (logS - med) / mad
    zf = np.where(ok, zs, 0.0)
    w = ndimage.gaussian_filter(ok.astype(np.float32), SMOOTH_BLOCKS)
    zsm = ndimage.gaussian_filter(zf.astype(np.float32), SMOOTH_BLOCKS) / np.maximum(w, 1e-3)
    zsm[w < 0.3] = 0.0
    low = zsm > Z_LOW
    lab, n = ndimage.label(low)
    seeded = np.unique(lab[(zsm > Z_HIGH) & low])
    mask = np.isin(lab, seeded[seeded > 0])
    cell_m2 = (BLOCK * res_m) ** 2
    lab, n = ndimage.label(mask)
    if n:
        areas = ndimage.sum(mask, lab, range(1, n + 1)) * cell_m2
        mask = np.isin(lab, 1 + np.where(areas >= MIN_AREA_M2)[0])
    holes = ndimage.label(~mask)[0]
    nh = holes.max()
    if nh:
        ha = ndimage.sum(~mask, holes, range(1, nh + 1)) * cell_m2
        for i in np.where(ha < HOLE_AREA_M2)[0]:
            mask |= holes == i + 1
    info = (f"log10 median|L| median {10**med:.4f} m, robust sigma {mad:.3f} dex; "
            f"{mask.mean():.2%} of blocks in noisy zones, {ndimage.label(mask)[1]} zone(s)")
    return mask, zsm, info

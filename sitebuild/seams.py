"""Detector for HiRISE-mosaic stitching artefacts (thin, near-horizontal lines).

Observation (looked at in docs/seam_check.png and in |d2| maps of the source grid): at seams the
north-south second derivative d2 = z[r-1] - 2 z[r] + z[r+1] becomes systematically noisier along a
line that runs nearly E-W, while natural terrain raises |d2| only locally. The signed median of d2
per row stays ~0, so these are jitter/ripple seams, not clean offsets.

Method, in the SOURCE grid (1 m, before any resampling):
  1. |d2| for every cell (NaN-aware).
  2. For each row and each column block (BLOCK px wide, so slightly slanted lines stay inside a
     block), A[r, b] = median |d2| over the block. A median is used so a seam must lift at
     least half the columns of the block; isolated rocks or craters cannot do that.
  3. Baseline = median of A over rows r-7..r+7 in the same block (running median, so slow terrain
     change is removed but a 1-5 row line is not).
  4. Excess D = A - baseline; robust scale = 1.4826 * MAD(D) of that block over the whole window.
     A (row, block) segment is a SEAM SUSPECT if D/scale > Z_THRESH and D > MIN_EXCESS_M.
  5. Line-likeness filter: suspect segments are grouped (8-connected over blocks and rows, with a
     +-1 row tolerance for slanted lines); a group is kept only if it spans >= MIN_RUN_BLOCKS
     blocks and <= MAX_LINE_ROWS rows. Without this, steep natural scarps (large |d2| over a
     patch) were flagged too; those are 2-D blobs, whereas seams are long thin lines.
  6. The three elevation rows that enter d2 (r-1, r, r+1) of every suspect segment are flagged.
Only flags are produced. Elevations are never modified. Limits: a long E-W natural scarp or a
dune crest aligned with the grid over >BLOCK/2 columns can be flagged falsely, and a seam that
changes only the mean (offset without extra noise) is not detected.
"""
import numpy as np
from scipy.ndimage import find_objects, label, median_filter

BLOCK = 256
HALF_WIN = 7
Z_THRESH = 6.0
MIN_EXCESS_M = 0.005
MIN_RUN_BLOCKS = 3     # a seam must be line-like: >= 3 adjacent blocks (768 px) wide ...
MAX_LINE_ROWS = 8      # ... and at most 8 source rows tall (blobs on steep terrain are rejected)


def detect_seams(z, band=1500):
    """Per-row seam score from the median |d2| (N-S second derivative) in column blocks of the 1 m source grid; processed in row bands of `band` rows. Flags only, never edits z."""
    H, W = z.shape
    nb = W // BLOCK
    A = np.full((H, nb), np.nan, np.float32)
    zz = z.astype(np.float32)
    for r0 in range(1, H - 1, band):
        r1 = min(r0 + band, H - 1)
        c = zz[r0:r1]
        d2 = np.abs(zz[r0 - 1:r1 - 1] - 2 * c + zz[r0 + 1:r1 + 1])
        for b in range(nb):
            blk = d2[:, b * BLOCK:(b + 1) * BLOCK]
            with np.errstate(all="ignore"):
                ok = np.isfinite(blk).sum(1) >= BLOCK // 2
                med = np.nanmedian(np.where(np.isfinite(blk), blk, np.nan), axis=1)
            A[r0:r1, b] = np.where(ok, med, np.nan)
    Af = np.where(np.isfinite(A), A, 0.0)
    base = median_filter(Af, size=(2 * HALF_WIN + 1, 1), mode="nearest")
    D = np.where(np.isfinite(A), Af - base, 0.0)
    valid = np.isfinite(A)
    flags = np.zeros((H, W), bool)
    sus = np.zeros((H, nb), bool)
    for b in range(nb):
        d = D[valid[:, b], b]
        scale = 1.4826 * np.median(np.abs(d - np.median(d))) if d.size else np.inf
        sus[:, b] = valid[:, b] & (D[:, b] > Z_THRESH * scale) & (D[:, b] > MIN_EXCESS_M)
    n_raw = int(sus.sum())
    lab, n = label(sus, structure=np.ones((3, 3)))
    keep = np.zeros_like(sus)
    for i, sl in enumerate(find_objects(lab), 1):
        nr, ncol = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
        if ncol >= MIN_RUN_BLOCKS and nr <= MAX_LINE_ROWS:
            keep[sl] |= lab[sl] == i
    n_seg = int(keep.sum())
    for r, b in zip(*np.where(keep)):
        flags[max(r - 1, 0):r + 2, b * BLOCK:(b + 1) * BLOCK] = True
    flags &= np.isfinite(z)
    rows = np.where(flags.any(1))[0]
    info = (f"{n_raw} raw suspect segments, {n_seg} kept as line-like; {n_seg} segments ({BLOCK}-px blocks) -> {len(rows)} source rows touched, "
            f"{int(flags.sum())} source cells flagged ({flags.sum()/np.isfinite(z).sum():.2%} of valid)")
    return rows, flags, info

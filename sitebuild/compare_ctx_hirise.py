"""Compare the CTX data Cesium Mars uses for Jezero against the HiRISE TRN mosaics for one window around the Perseverance
landing site. Writes a figure and prints stats (one-off analysis script, not part of the build).

Architecture: offline build step at the bottom of the data path (raw NASA/USGS products -> sitebuild -> data/build/site/<name>/ -> atlas_core.load_site -> devapi/MCP/agent).
Reports elevation difference (median, RMS, p99 |dz|) and slope percentiles at 2 m / 4 m / 20 m, using plain np.gradient slopes
(not the Horn operator of atlas_core.terrain), to compare how the CTX and HiRISE terrain differ in slope at each resolution.
"""
import math, sys, numpy as np, rasterio
from rasterio.windows import from_bounds
from rasterio.warp import reproject, Resampling
from rasterio.transform import from_origin
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt

R = 3396190.0
S3 = "/vsicurl/https://asc-pds-services.s3.us-west-2.amazonaws.com/mosaic/mars2020_trn"
CTX_DTM = "data/raw/ctx/JEZ_ctx_B_soc_008_DTM_MOLAtopography_DeltaGeoid_20m_Eqc_latTs0_lon0.tif"
CTX_ORT = "data/raw/ctx/JEZ_ctx_B_soc_008_orthoMosaic_6m_Eqc_latTs0_lon0.tif"
HI_DTM = f"{S3}/HiRISE/JEZ_hirise_soc_006_DTM_MOLAtopography_DeltaGeoid_1m_Eqc_latTs0_lon0_blend40.tif"
HI_ORT = f"{S3}/HiRISE/JEZ_hirise_soc_007_orthoMosaic_25cm_Ortho_blend120.tif"

lat, lon = 18.4447, 77.4508          # Octavia E. Butler landing site (planetocentric, E)
half = float(sys.argv[1]) if len(sys.argv) > 1 else 1000.0
cx, cy = R * math.radians(lon), R * math.radians(lat)   # eqc, lat_ts=0
bounds = (cx - half, cy - half, cx + half, cy + half)
size_m = 2 * half
# Local frame: eqc with lat_ts = site latitude so east-west metres are true metres.
local_crs = f"+proj=eqc +lat_ts={lat} +lon_0={lon} +R={R} +units=m"
y0 = R * math.radians(lat)

def read_local(path, res, resampling, src_bounds_pad=1.3):
    """Reproject a window of `path` into the site frame at `res` metres."""
    n = int(round(size_m / res))
    dst = np.full((n, n), np.nan, np.float32)
    dst_t = from_origin(-half, y0 + half, res, res)
    with rasterio.open(path) as d:
        # read a padded source window in source CRS (eqc lat_ts=0 stretches x by 1/cos(lat))
        if d.crs.to_proj4().find("ortho") >= 0:
            from rasterio.warp import transform_bounds
            sb = transform_bounds(local_crs.replace(f"+lon_0={lon}", f"+lon_0={lon}") , d.crs,
                                  -half*1.2, y0-half*1.2, half*1.2, y0+half*1.2)
        else:
            k = 1 / math.cos(math.radians(lat))
            sb = (cx - half*k*src_bounds_pad, cy - half*src_bounds_pad, cx + half*k*src_bounds_pad, cy + half*src_bounds_pad)
        w = from_bounds(*sb, d.transform).round_offsets().round_lengths()
        src = d.read(1, window=w, masked=True).astype(np.float32).filled(np.nan)
        reproject(src, dst, src_transform=d.window_transform(w), src_crs=d.crs, src_nodata=np.nan,
                  dst_transform=dst_t, dst_crs=local_crs, dst_nodata=np.nan, resampling=resampling)
    return dst

def slope_deg(z, res):
    """Slope in degrees from np.gradient (plain central differences, not the Horn operator)."""
    gy, gx = np.gradient(z, res)
    return np.degrees(np.arctan(np.hypot(gx, gy)))

def hillshade(z, res, az=315, alt=35):
    """Hillshade clipped to 0-1; `az`, `alt` in degrees."""
    gy, gx = np.gradient(z, res)
    sl = np.arctan(np.hypot(gx, gy)); asp = np.arctan2(-gx, gy)
    a, e = np.radians(az), np.radians(alt)
    return np.clip(np.sin(e)*np.cos(sl) + np.cos(e)*np.sin(sl)*np.cos(a - asp), 0, 1)

print(f"window {size_m:.0f} m square centred on {lat}N {lon}E")
ctx = read_local(CTX_DTM, 20, Resampling.bilinear)
ctx_up = read_local(CTX_DTM, 1, Resampling.bilinear)      # what a viewer shows if you upsample CTX
print("CTX done"); sys.stdout.flush()
hi = read_local(HI_DTM, 1, Resampling.bilinear)
print("HiRISE DTM done"); sys.stdout.flush()
ort_ctx = read_local(CTX_ORT, 1, Resampling.bilinear)
ort_hi = read_local(HI_ORT, 0.5, Resampling.average)
print("orthos done")

# Compare slopes at the planning resolutions in the PRD (2 m and 4 m) vs CTX native 20 m
# block(z, f): NaN-aware mean of f x f blocks (remainder rows/cols cropped)
def block(z, f): n = z.shape[0]//f*f; return np.nanmean(z[:n,:n].reshape(n//f,f,n//f,f), axis=(1,3))
s_ctx = slope_deg(ctx, 20)
s_hi2 = slope_deg(block(hi, 2), 2); s_hi4 = slope_deg(block(hi, 4), 4)
dz = hi - ctx_up
print(f"\nElevation HiRISE minus CTX: median {np.nanmedian(dz):+.2f} m, RMS {np.sqrt(np.nanmean(dz**2)):.2f} m, p99 |dz| {np.nanpercentile(abs(dz),99):.2f} m")
print(f"{'':12s}{'p50':>7s}{'p95':>7s}{'p99':>7s}{'max':>7s}{'>15deg':>9s}{'>20deg':>9s}{'>25deg':>9s}")
for name, s in [("CTX 20 m", s_ctx), ("HiRISE 4 m", s_hi4), ("HiRISE 2 m", s_hi2)]:
    v = s[np.isfinite(s)]
    print(f"{name:12s}" + "".join(f"{np.percentile(v,p):7.1f}" for p in (50,95,99)) + f"{v.max():7.1f}"
          + "".join(f"{100*np.mean(v>t):8.2f}%" for t in (15,20,25)))

fig, ax = plt.subplots(2, 3, figsize=(18, 12))
ext = (-half, half, -half, half)
ax[0,0].imshow(hillshade(ctx_up, 1), cmap="gray", extent=ext); ax[0,0].set_title("CTX 20 m DTM (Cesium Mars Jezero terrain), hillshade")
ax[0,1].imshow(hillshade(hi, 1), cmap="gray", extent=ext); ax[0,1].set_title("HiRISE 1 m DTM, hillshade")
im = ax[0,2].imshow(s_hi2, cmap="RdYlGn_r", vmin=0, vmax=30, extent=ext); ax[0,2].set_title("HiRISE slope at 2 m (deg)"); plt.colorbar(im, ax=ax[0,2], fraction=.046)
ax[1,0].imshow(ort_ctx, cmap="gray", extent=ext); ax[1,0].set_title("CTX 6 m ortho (Cesium Mars Jezero imagery)")
ax[1,1].imshow(ort_hi, cmap="gray", extent=ext); ax[1,1].set_title("HiRISE 25 cm ortho (shown at 0.5 m)")
im = ax[1,2].imshow(s_ctx, cmap="RdYlGn_r", vmin=0, vmax=30, extent=ext); ax[1,2].set_title("CTX slope at 20 m (deg)"); plt.colorbar(im, ax=ax[1,2], fraction=.046)
for a in ax.flat: a.plot(0, 0, "c+", ms=14, mew=2)
plt.tight_layout(); out = f"docs/compare_ctx_vs_hirise_{int(size_m)}m.png"
import os; os.makedirs("docs", exist_ok=True); plt.savefig(out, dpi=80); print("wrote", out)

# zoomed 300 m crop to show ground-level detail
c = 150; m = slice(int(half-c), int(half+c)); m2 = slice(int((half-c)*2), int((half+c)*2))
fig, ax = plt.subplots(1, 3, figsize=(18, 6.3))
ax[0].imshow(hillshade(ctx_up, 1)[m, m], cmap="gray"); ax[0].set_title("CTX 20 m terrain, 300 m crop")
ax[1].imshow(hillshade(hi, 1)[m, m], cmap="gray"); ax[1].set_title("HiRISE 1 m terrain, 300 m crop")
ax[2].imshow(ort_hi[m2, m2], cmap="gray"); ax[2].set_title("HiRISE 25 cm imagery, 300 m crop")
for a in ax: a.axis("off")
plt.tight_layout(); out2 = "docs/compare_zoom_300m.png"; plt.savefig(out2, dpi=80); print("wrote", out2)

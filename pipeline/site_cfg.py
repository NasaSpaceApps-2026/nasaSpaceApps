"""Single source of truth for the real site frame (contract 1). Change only here."""
R = 3396190.0
LAT0 = 18.452  # planetocentric degrees; chosen so the 8 km square holds the landing site
LON0 = 77.418  # east-positive degrees
SIZE = 8192.0  # metres
# Site CRS: eqc, standard parallel lat0, northing shifted by Y0 (contract 1).
import math
Y0 = -R * math.radians(LAT0)  # shift northing so y=0 at the region centre
CRS = f"+proj=eqc +lat_ts={LAT0} +lat_0=0 +lon_0={LON0} +x_0=0 +y_0={Y0:.3f} +R={R} +units=m +no_defs"

# 25 cm "core" block (PRD: level 6 only in a core box). Whole level-6 tiles (128 m, 512 px) so tiles
# stay on the quadtree: CORE_N x CORE_N tiles starting at tile (CORE_TX0, CORE_TY0) of level 6.
# Chosen to cover the real Perseverance landing/early traverse (sols 13-200) at about x 1850, y -440.
CORE_LEVEL = 6
CORE_TX0, CORE_TY0, CORE_N = 37, 29, 17
CORE_TILE_M = SIZE / 2 ** CORE_LEVEL                      # 128 m
CORE_X0 = -SIZE / 2 + CORE_TX0 * CORE_TILE_M              # west edge, site m
CORE_Y1 = SIZE / 2 - CORE_TY0 * CORE_TILE_M               # north edge, site m
CORE_SIZE = CORE_N * CORE_TILE_M                          # 2176 m

# Context block: coarser NASA CTX data around the HiRISE site, so the surroundings are real NASA CTX
# (20 m DTM, 5 m orthomosaic) and not Cesium's. Square, centred on the site, quadtree tiles like the site.
CTX_SIZE = 28672.0   # metres; level 4 tiles are 1792 m (512 px -> 3.5 m/px, close to CTX's 5 m)
CTX_MAX_LEVEL = 4

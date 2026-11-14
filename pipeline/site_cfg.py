"""Single source of truth for the real site frame (contract 1). Change only here."""
R = 3396190.0
LAT0 = 18.452  # planetocentric degrees; chosen so the 8 km square holds the landing site
LON0 = 77.418  # east-positive degrees
SIZE = 8192.0  # metres
# Site CRS: eqc, standard parallel lat0, northing shifted by Y0 (contract 1).
import math
Y0 = -R * math.radians(LAT0)  # shift northing so y=0 at the region centre
CRS = f"+proj=eqc +lat_ts={LAT0} +lat_0=0 +lon_0={LON0} +x_0=0 +y_0={Y0:.3f} +R={R} +units=m +no_defs"

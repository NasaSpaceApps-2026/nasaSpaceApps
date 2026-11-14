"""Mars sun position and solar time: the Mars24 algorithm.

Source: Allison, M. and McEwen, M. (2000), "A post-Pathfinder evaluation of aerocentric solar coordinates with
improved timing recipes for Mars seasonal/diurnal climate studies", Planet. Space Sci. 48, 215-235; implemented
as published in the NASA GISS Mars24 technical notes,
https://www.giss.nasa.gov/tools/mars24/help/algorithm.html  (steps A to D; coefficients and constants below are
copied from that page).

Conventions
-----------
* Longitudes: the algorithm uses WEST longitude; this module takes EAST longitude (as the Atlas does) and
  converts (west = 360 - east).
* Latitude: Mars24 evaluates the zenith angle with PLANETOGRAPHIC latitude. The Atlas uses PLANETOCENTRIC
  latitude, so `planetocentric_to_planetographic` is applied first (tan(phi_g) = (a/b)^2 tan(phi_c), with
  a = 3396.19 km, b = 3376.20 km, the IAU/MOLA Mars ellipsoid). The difference is up to about 0.2 deg of latitude.
* Time scales: input is UTC. TT - UTC = (TAI - UTC) + 32.184 s. TAI - UTC comes from the IERS leap-second table
  below (37 s since 2017-01-01; no leap second has been added since, as of 2026). Before 1972 the polynomial
  given on the Mars24 page is used. Dates after the last table entry assume no further leap seconds
  (a future leap second would shift results by 1 s, which is 0.004 deg of solar longitude: negligible).
* Mean (LMST) vs true (LTST) solar time: LMST is a uniform clock (24 h = one mean sol, 88775.244 s). LTST is the
  apparent sun: LTST = LMST + equation of time; the sun is on the local meridian at LTST = 12:00. Local midnight
  is 00:00. Mission clocks (Mars 2020 "LMST") use mean time; sun-angle questions need true time.
* Mission sol: Mars 2020 sol numbering. Sol 0 is the sol of touchdown. A new sol begins at local MEAN midnight
  at the landing-site longitude. Epoch used here: 2021-02-18 20:55:00 UTC (see MARS2020_LANDING_UTC), the
  landing time on JPL's Mars 2020 landing quick facts. NOTE: JPL lists 3:55 pm EST = 20:55 UTC, which is the Earth-
  receive time; actual touchdown was about 11.4 min earlier (light time). Sol numbers are unaffected unless the
  query falls within those minutes of a sol boundary. Mission sol day boundaries use the Jezero landing longitude
  (LANDING_LON_E).

Not modelled: atmospheric refraction, dust scattering and twilight sky brightness (Mars has a long twilight, of
roughly 2 h at Jezero latitude, because of dust), the finite solar disc (elevation is the disc centre), planetary
parallax, and the small difference between the DEM's local horizontal and the ellipsoid normal.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np

from .errors import AtlasError

# ---- constants (Mars24 page) -------------------------------------------------------------------------
JD_UNIX_EPOCH = 2440587.5
JD_J2000 = 2451545.0
SOL_SECONDS = 88775.244147          # 86400 * 1.0274912517
SOL_DAYS = 1.0274912517
MARS_A_KM = 3396.19
MARS_B_KM = 3376.20
OBLIQUITY_BOUND_DEG = 25.2

_PERTURBERS = (  # A_i, tau_i, phi_i  (Mars24 step B-2)
    (0.0071, 2.2353, 49.409),
    (0.0057, 2.7543, 168.173),
    (0.0039, 1.1177, 191.837),
    (0.0037, 15.7866, 21.736),
    (0.0021, 2.1354, 15.704),
    (0.0020, 2.4694, 95.528),
    (0.0018, 32.8493, 49.095),
)

# TAI - UTC in seconds, effective from the given UTC date (IERS Bulletin C).
_LEAP = (
    ((1972, 1, 1), 10), ((1972, 7, 1), 11), ((1973, 1, 1), 12), ((1974, 1, 1), 13), ((1975, 1, 1), 14),
    ((1976, 1, 1), 15), ((1977, 1, 1), 16), ((1978, 1, 1), 17), ((1979, 1, 1), 18), ((1980, 1, 1), 19),
    ((1981, 7, 1), 20), ((1982, 7, 1), 21), ((1983, 7, 1), 22), ((1985, 7, 1), 23), ((1988, 1, 1), 24),
    ((1990, 1, 1), 25), ((1991, 1, 1), 26), ((1992, 7, 1), 27), ((1993, 7, 1), 28), ((1994, 7, 1), 29),
    ((1996, 1, 1), 30), ((1997, 7, 1), 31), ((1999, 1, 1), 32), ((2006, 1, 1), 33), ((2009, 1, 1), 34),
    ((2012, 7, 1), 35), ((2015, 7, 1), 36), ((2017, 1, 1), 37),
)
_LEAP_JD = [(datetime(*d, tzinfo=timezone.utc).timestamp() / 86400.0 + JD_UNIX_EPOCH, s) for d, s in _LEAP]

MARS2020_LANDING_UTC = datetime(2021, 2, 18, 20, 55, 0, tzinfo=timezone.utc)
LANDING_LON_E = 77.45               # Jezero landing site east longitude used for sol boundaries (deg)


# ---- time handling ------------------------------------------------------------------------------------

def parse_utc(t=None) -> datetime:
    """datetime / ISO-8601 string / None (now) -> timezone-aware UTC datetime. Naive input is taken as UTC."""
    if t is None or (isinstance(t, str) and t.strip().lower() == "now"):
        return datetime.now(timezone.utc)
    if isinstance(t, (int, float)) and not isinstance(t, bool):
        return datetime.fromtimestamp(float(t), timezone.utc)
    if isinstance(t, str):
        s = t.strip().replace("Z", "+00:00").replace("z", "+00:00")
        try:
            t = datetime.fromisoformat(s)
        except ValueError:
            raise AtlasError(f'Cannot read the time "{t}".',
                             hint='Use ISO 8601 UTC, e.g. "2021-02-18T20:55:00Z", or "now".') from None
    if not isinstance(t, datetime):
        raise AtlasError(f"Cannot read the time {t!r}.", hint='Use ISO 8601 UTC, e.g. "2021-02-18T20:55:00Z".')
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


def jd_ut(t) -> float:
    return parse_utc(t).timestamp() / 86400.0 + JD_UNIX_EPOCH


def tt_minus_utc(jd_utc: float) -> float:
    """TT - UTC in seconds (Mars24 step A-2)."""
    if jd_utc < _LEAP_JD[0][0]:
        T = (jd_utc - JD_J2000) / 36525.0
        return 64.184 + 59 * T - 51.2 * T**2 - 67.1 * T**3 - 16.4 * T**4
    tai = _LEAP[0][1]
    for jd0, s in _LEAP_JD:
        if jd_utc >= jd0:
            tai = s
    return tai + 32.184


def jd_tt(t) -> float:
    ju = jd_ut(t)
    return ju + tt_minus_utc(ju) / 86400.0


def utc_from_jd_ut(jd: float) -> datetime:
    return datetime.fromtimestamp((jd - JD_UNIX_EPOCH) * 86400.0, timezone.utc)


# ---- Mars24 core, as a function of JD_TT -------------------------------------------------------------

def _mod(x, m):
    return x - m * np.floor(x / m)


def mars_state_tt(jdtt) -> dict:
    """Everything that does not depend on the observer, from JD_TT (scalar or array). Angles in degrees."""
    dt = np.asarray(jdtt, dtype=float) - JD_J2000
    M = _mod(19.3871 + 0.52402073 * dt, 360.0)                      # B-1
    afms = 270.3871 + 0.524038496 * dt                                # B-2
    pbs = 0.0
    for A, tau, phi in _PERTURBERS:
        pbs = pbs + A * np.cos(np.radians(0.985626 * dt / tau + phi))
    r = np.radians
    nu_m = ((10.691 + 3.0e-7 * dt) * np.sin(r(M)) + 0.623 * np.sin(r(2 * M)) + 0.050 * np.sin(r(3 * M))
            + 0.005 * np.sin(r(4 * M)) + 0.0005 * np.sin(r(5 * M)) + pbs)   # B-4 (nu - M)
    ls = _mod(afms + nu_m, 360.0)                                      # B-5
    eot_deg = (2.861 * np.sin(r(2 * ls)) - 0.071 * np.sin(r(4 * ls)) + 0.002 * np.sin(r(6 * ls)) - nu_m)  # C-1
    mst = _mod(24.0 * ((np.asarray(jdtt, dtype=float) - 2451549.5) / SOL_DAYS + 44796.0 - 0.0009626), 24.0)  # C-2
    decl = np.degrees(np.arcsin(0.42565 * np.sin(r(ls)))) + 0.25 * np.sin(r(ls))                      # D-1
    rm = 1.52367934 * (1.00436 - 0.09309 * np.cos(r(M)) - 0.004336 * np.cos(r(2 * M))
                       - 0.00031 * np.cos(r(3 * M)) - 0.00003 * np.cos(r(4 * M)))                      # D-2
    sub_lon_w = _mod(15.0 * mst + eot_deg + 180.0, 360.0)              # C-5 (west longitude of subsolar point)
    return {"dt_days": dt, "M": M, "ls": ls, "eot_deg": eot_deg, "eot_h": eot_deg / 15.0, "mst_h": mst,
            "subsolar_lat_planetographic": decl, "subsolar_lon_west": sub_lon_w,
            "subsolar_lon_east": _mod(360.0 - sub_lon_w, 360.0), "helio_distance_au": rm}


def planetocentric_to_planetographic(lat_c_deg):
    t = (MARS_A_KM / MARS_B_KM) ** 2 * np.tan(np.radians(lat_c_deg))
    return np.degrees(np.arctan(t))


def planetographic_to_planetocentric(lat_g_deg):
    t = (MARS_B_KM / MARS_A_KM) ** 2 * np.tan(np.radians(lat_g_deg))
    return np.degrees(np.arctan(t))


def elevation_azimuth_tt(jdtt, lat_graphic_deg: float, lon_west_deg: float):
    """Solar elevation and azimuth (deg, azimuth clockwise from north) from JD_TT. Mars24 steps D-5 to D-7."""
    st = mars_state_tt(jdtt)
    r = np.radians
    H = r(lon_west_deg - st["subsolar_lon_west"])
    d = r(st["subsolar_lat_planetographic"])
    p = r(lat_graphic_deg)
    cosz = np.sin(d) * np.sin(p) + np.cos(d) * np.cos(p) * np.cos(H)
    z = np.degrees(np.arccos(np.clip(cosz, -1, 1)))
    az = np.degrees(np.arctan2(np.sin(H), np.cos(p) * np.tan(d) - np.sin(p) * np.cos(H)))
    return 90.0 - z, _mod(az, 360.0)


# ---- public API ---------------------------------------------------------------------------------------

def hms(hours: float) -> str:
    h = float(hours) % 24.0
    s = int(round(h * 3600)) % 86400
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def msd(t) -> float:
    """Mars Sol Date (Allison & McEwen): MSD = (JD_TT - 2451549.5)/1.0274912517 + 44796.0 - 0.0009626."""
    return (jd_tt(t) - 2451549.5) / SOL_DAYS + 44796.0 - 0.0009626


def lmst_hours(t, lon_east_deg: float) -> float:
    """Local mean solar time (hours, 0-24) at an EAST longitude."""
    mst = float(mars_state_tt(jd_tt(t))["mst_h"])
    return float(_mod(mst + lon_east_deg / 15.0, 24.0))


def ltst_hours(t, lon_east_deg: float) -> float:
    st = mars_state_tt(jd_tt(t))
    return float(_mod(st["mst_h"] + lon_east_deg / 15.0 + st["eot_h"], 24.0))


def local_msd(t, lon_east_deg: float) -> float:
    return msd(t) + lon_east_deg / 360.0


def mission_sol(t, lon_east_deg: float = LANDING_LON_E) -> int:
    """Mars 2020 mission sol number (sol 0 = touchdown sol); sols start at local mean midnight at the landing
    longitude. Negative before landing."""
    return int(math.floor(local_msd(t, LANDING_LON_E)) - math.floor(local_msd(MARS2020_LANDING_UTC, LANDING_LON_E)))


def utc_of_mission_sol_start(sol: int) -> datetime:
    """UTC of local mean midnight starting a mission sol."""
    target = math.floor(local_msd(MARS2020_LANDING_UTC, LANDING_LON_E)) + int(sol)
    jd = jd_ut(MARS2020_LANDING_UTC)
    for _ in range(4):   # MSD is nearly linear in JD; a few Newton steps are exact to well under a ms
        jd += (target - local_msd(utc_from_jd_ut(jd), LANDING_LON_E)) * SOL_DAYS
    return utc_from_jd_ut(jd)


def utc_for_ltst(sol: int, ltst_h: float, lon_east_deg: float) -> datetime:
    """UTC at which local TRUE solar time at lon_east equals ltst_h (0-24) on mission sol `sol`.
    The site's sol N is the local-mean-time day numbered floor(local MSD of the landing instant at the site's
    own longitude) + N; for sites within the crater this is identical to the mission sol."""
    day = math.floor(local_msd(MARS2020_LANDING_UTC, lon_east_deg)) + int(sol)
    jd = jd_ut(MARS2020_LANDING_UTC)
    for _ in range(4):   # land on LMST = ltst_h of that day
        jd += (day + ltst_h / 24.0 - local_msd(utc_from_jd_ut(jd), lon_east_deg)) * SOL_DAYS
    for _ in range(4):   # then correct for the equation of time (true, not mean, time)
        cur = ltst_hours(utc_from_jd_ut(jd), lon_east_deg)
        jd += (((ltst_h - cur + 12) % 24) - 12) / 24.0 * SOL_DAYS
    return utc_from_jd_ut(jd)


def sun_position(t, lat_c_deg: float, lon_east_deg: float) -> dict:
    """Sun elevation/azimuth and solar times for a planetocentric latitude and east longitude."""
    ju = jd_ut(t)
    tt = ju + tt_minus_utc(ju) / 86400.0
    st = mars_state_tt(tt)
    lat_g = float(planetocentric_to_planetographic(lat_c_deg))
    elev, az = elevation_azimuth_tt(tt, lat_g, 360.0 - (lon_east_deg % 360.0))
    mst = float(st["mst_h"])
    lmst = float(_mod(mst + lon_east_deg / 15.0, 24.0))
    ltst = float(_mod(lmst + st["eot_h"], 24.0))
    sub_lat_c = float(planetographic_to_planetocentric(st["subsolar_lat_planetographic"]))
    return {
        "utc": utc_from_jd_ut(ju).isoformat().replace("+00:00", "Z"),
        "sun_elevation_deg": float(elev), "sun_azimuth_deg": float(az),
        "ltst_hours": ltst, "ltst": hms(ltst), "lmst_hours": lmst, "lmst": hms(lmst),
        "ls_deg": float(st["ls"]), "season": season_name(float(st["ls"])),
        "msd": msd(t), "mission_sol": mission_sol(t), "coordinated_mars_time": hms(float(mst)),
        "equation_of_time_min": float(st["eot_h"] * 60.0),
        "subsolar_lat_planetographic_deg": float(st["subsolar_lat_planetographic"]),
        "subsolar_lat_planetocentric_deg": sub_lat_c,
        "subsolar_lon_east_deg": float(st["subsolar_lon_east"]),
        "helio_distance_au": float(st["helio_distance_au"]),
        "site_lat_planetocentric_deg": float(lat_c_deg), "site_lat_planetographic_deg": lat_g,
        "site_lon_east_deg": float(lon_east_deg),
        "tt_minus_utc_s": tt_minus_utc(ju),
    }


def season_name(ls: float) -> str:
    return ("northern spring" if ls < 90 else "northern summer" if ls < 180
            else "northern autumn" if ls < 270 else "northern winter")


def sunrise_sunset(t, lat_c_deg: float, lon_east_deg: float, threshold_deg: float = 0.0, step_min: float = 5.0) -> dict:
    """Sun-disc-centre crossings of `threshold_deg` elevation during the local true-solar day (LTST 00:00 to
    24:00) that contains `t`. Geometric (flat horizon, no refraction, disc centre)."""
    ju = jd_ut(t)
    ltst0 = ltst_hours(utc_from_jd_ut(ju), lon_east_deg)
    start = ju - ltst0 / 24.0 * SOL_DAYS           # local true midnight (approx; refined below)
    for _ in range(3):
        cur = ltst_hours(utc_from_jd_ut(start), lon_east_deg)
        diff = ((0.0 - cur + 12) % 24) - 12
        start += diff / 24.0 * SOL_DAYS
    n = int(SOL_SECONDS / 60.0 / step_min) + 1
    jds = start + np.arange(n + 1) * (step_min / 1440.0)
    jds = jds[jds <= start + SOL_DAYS]
    lat_g = float(planetocentric_to_planetographic(lat_c_deg))
    lonw = 360.0 - (lon_east_deg % 360.0)

    def el(j):
        tt = j + np.vectorize(tt_minus_utc)(j) / 86400.0
        return elevation_azimuth_tt(tt, lat_g, lonw)[0] - threshold_deg
    e = el(jds)
    out = {"sunrise": None, "sunset": None, "start_utc": utc_from_jd_ut(start).isoformat().replace("+00:00", "Z")}
    for i in range(len(jds) - 1):
        if (e[i] < 0) != (e[i + 1] < 0):
            a, b = jds[i], jds[i + 1]
            ea = e[i]
            for _ in range(40):
                m = 0.5 * (a + b)
                em = float(el(np.array(m)))
                if (em < 0) == (ea < 0):
                    a, ea = m, em
                else:
                    b = m
            jx = 0.5 * (a + b)
            key = "sunrise" if e[i + 1] > e[i] else "sunset"
            if out[key] is None:
                out[key] = {"utc": utc_from_jd_ut(jx).isoformat().replace("+00:00", "Z"),
                            "ltst": hms(ltst_hours(utc_from_jd_ut(jx), lon_east_deg)),
                            "ltst_hours": ltst_hours(utc_from_jd_ut(jx), lon_east_deg)}
    out["day_length_ltst_hours"] = (None if not (out["sunrise"] and out["sunset"])
                                    else (out["sunset"]["ltst_hours"] - out["sunrise"]["ltst_hours"]) % 24.0)
    out["note"] = ("Polar day (sun never below the threshold)" if (e > 0).all() else
                   "Polar night (sun never above the threshold)" if (e < 0).all() else "")
    return out

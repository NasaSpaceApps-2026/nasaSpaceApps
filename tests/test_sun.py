"""Mars24 sun-position algorithm (atlas_core.sun) against published reference values.

References:
  * NASA GISS Mars24 algorithm page, https://www.giss.nasa.gov/tools/mars24/help/algorithm.html (opened), the
    "near-coincident times" example (2000-01-06 00:00 UTC, 0 N 0 W) and the Spirit example (Dt = 1463.07471 d,
    184.702 W; note the page's planetographic latitude for that example is -14.640, i.e. 14.640 S).
  * JPL Mars 2020 landing quick facts, https://www.jpl.nasa.gov/news/press_kits/mars_2020/landing/quick_facts/ :
    landing 2021-02-18 3:55 pm EST (= 20:55 UTC; this is the Earth-receive time), "time of day at the landing site
    3:53 p.m. local mean solar time", landing site about 18 N, 77 E.
"""
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from atlas_core import sun as S

JEZERO = (18.4447, 77.4508)    # planetocentric lat, east lon (Perseverance landing site)


def test_mars24_example_2000_01_06():
    t = datetime(2000, 1, 6, 0, 0, 0, tzinfo=timezone.utc)
    assert S.tt_minus_utc(S.jd_ut(t)) == pytest.approx(64.184)           # 32 s leap seconds + 32.184
    st = S.mars_state_tt(S.jd_tt(t))
    assert float(st["dt_days"]) == pytest.approx(4.50074, abs=1e-5)
    assert float(st["M"]) == pytest.approx(21.74558, abs=2e-5)
    assert float(st["ls"]) == pytest.approx(277.18758, abs=2e-5)
    assert float(st["eot_deg"]) == pytest.approx(-5.18774, abs=2e-5)
    assert S.hms(float(st["mst_h"])) == "23:59:39"                          # Coordinated Mars Time
    assert float(st["subsolar_lat_planetographic"]) == pytest.approx(-25.22825, abs=1e-4)
    assert float(st["helio_distance_au"]) == pytest.approx(1.39358, abs=1e-5)
    lt = S.ltst_hours(t, 0.0)                                              # 0 E == 0 W
    assert S.hms(lt) == "23:38:54"
    e, a = S.elevation_azimuth_tt(S.jd_tt(t), 0.0, 0.0)
    assert 90 - float(e) == pytest.approx(154.26182, abs=1e-3)
    assert float(a) == pytest.approx(191.03905, abs=5e-3)


def test_mars24_example_spirit():
    jd = 2451545.0 + 1463.07471            # the page gives Dt directly
    st = S.mars_state_tt(jd)
    assert float(st["ls"]) == pytest.approx(327.32416, abs=2e-5)
    assert float(st["eot_deg"]) == pytest.approx(-12.77553, abs=2e-5)
    assert S.hms(float(st["mst_h"])) == "13:09:55"
    assert float(st["subsolar_lat_planetographic"]) == pytest.approx(-13.42065, abs=1e-3)
    assert float(st["helio_distance_au"]) == pytest.approx(1.47767, abs=1e-5)
    lonw = 184.702
    ltst = (float(st["mst_h"]) - lonw / 15 + float(st["eot_h"])) % 24
    assert S.hms(ltst) == "00:00:00"
    e, a = S.elevation_azimuth_tt(jd, -14.640, lonw)
    assert 90 - float(e) == pytest.approx(151.93895, abs=2e-3)
    assert float(a) == pytest.approx(179.99383, abs=5e-3)


def test_perseverance_landing_local_time_jpl():
    # JPL: 15:53 LMST at the landing site. 20:55 UTC is the Earth-receive time, so touchdown was one light-time
    # (~11 min at Mars-Earth range of the day) earlier; LMST 15:53 must fall 10 to 13 minutes before 20:55 UTC.
    lat, lon = JEZERO
    t_erts = datetime(2021, 2, 18, 20, 55, tzinfo=timezone.utc)
    lm = S.lmst_hours(t_erts, lon)
    minutes_after = (lm - (15 + 53 / 60)) * 60 * S.SOL_SECONDS / 86400      # LMST hours -> real minutes
    assert 10.0 <= minutes_after <= 13.0, minutes_after
    p = S.sun_position(t_erts - timedelta(minutes=11, seconds=22), lat, lon)
    assert p["lmst"].startswith("15:5")                                      # 15:53 +- 1 min
    assert abs(p["lmst_hours"] - (15 + 53 / 60)) < 2 / 60
    assert p["mission_sol"] == 0
    assert 5.0 < p["ls_deg"] < 6.5                                           # northern spring, Ls ~ 5.6
    assert 30 < p["sun_elevation_deg"] < 45                                  # afternoon sun


def test_subsolar_latitude_bounds_and_overhead_sun():
    t0 = datetime(2022, 1, 1, tzinfo=timezone.utc)
    jd = S.jd_tt(t0) + np.linspace(0, 700, 1401)                            # a bit over one Mars year
    st = S.mars_state_tt(jd)
    d = st["subsolar_lat_planetographic"]
    # Mars24's declination is planetographic (it adds 0.25 deg sin Ls, so its worked example already reads -25.228);
    # in planetocentric terms the subsolar latitude is bounded by the obliquity, 25.19 deg.
    dc = S.planetographic_to_planetocentric(d)
    assert np.abs(dc).max() <= S.OBLIQUITY_BOUND_DEG
    assert np.abs(d).max() <= 25.45
    assert np.abs(dc).max() > 24.9                                        # solstices reach about 25 deg
    # equinox: Ls crosses 0 near declination 0
    i = np.argmin(np.abs(((st["ls"] + 180) % 360) - 180))
    assert abs(d[i]) < 0.3
    # sun elevation at the subsolar point is 90
    for k in (0, 300, 900):
        e, _ = S.elevation_azimuth_tt(jd[k], float(d[k]), float(st["subsolar_lon_west"][k]))
        assert float(e) == pytest.approx(90.0, abs=1e-6)


def test_sun_position_consistency_and_planetocentric_conversion():
    lat_c = 18.4447
    lat_g = float(S.planetocentric_to_planetographic(lat_c))
    assert lat_g > lat_c and lat_g - lat_c < 0.25
    assert float(S.planetographic_to_planetocentric(lat_g)) == pytest.approx(lat_c, abs=1e-9)
    assert float(S.planetocentric_to_planetographic(0.0)) == 0.0 and float(S.planetocentric_to_planetographic(90.0)) == pytest.approx(90.0)
    t = datetime(2026, 5, 1, 7, 0, tzinfo=timezone.utc)
    p = S.sun_position(t, lat_c, 77.4508)
    # LTST = LMST + EOT
    assert (p["ltst_hours"] - p["lmst_hours"] - p["equation_of_time_min"] / 60 + 12) % 24 - 12 == pytest.approx(0, abs=1e-6)
    assert abs(p["equation_of_time_min"]) < 55
    assert 0 <= p["sun_azimuth_deg"] < 360


def test_utc_for_ltst_roundtrip_and_sols():
    lon = 77.4508
    for sol, h in ((0, 12.0), (100, 6.5), (1200, 16.0)):
        t = S.utc_for_ltst(sol, h, lon)
        assert S.ltst_hours(t, lon) == pytest.approx(h, abs=1e-5)
        assert S.mission_sol(t, lon) == sol
    a, b = S.utc_of_mission_sol_start(5), S.utc_of_mission_sol_start(6)
    assert (b - a).total_seconds() == pytest.approx(S.SOL_SECONDS, abs=0.01)
    assert S.mission_sol(a + timedelta(seconds=1)) == 5 and S.mission_sol(a - timedelta(seconds=1)) == 4


def test_noon_is_highest_and_sunrise_sunset():
    lat, lon = JEZERO
    t = S.utc_for_ltst(1200, 12.0, lon)
    noon = S.sun_position(t, lat, lon)
    for dh in (-1.0, 1.0):
        other = S.sun_position(S.utc_for_ltst(1200, 12.0 + dh, lon), lat, lon)
        assert other["sun_elevation_deg"] < noon["sun_elevation_deg"]
    ss = S.sunrise_sunset(t, lat, lon)
    assert ss["sunrise"] and ss["sunset"]
    assert 4.5 < ss["sunrise"]["ltst_hours"] < 7.5 and 16.5 < ss["sunset"]["ltst_hours"] < 19.5
    for key in ("sunrise", "sunset"):
        e = S.sun_position(datetime.fromisoformat(ss[key]["utc"].replace("Z", "+00:00")), lat, lon)["sun_elevation_deg"]
        assert abs(e) < 1e-3
    # Jezero day length at the northern-winter solstice is shorter than at northern-summer solstice
    def dl(sol):
        return S.sunrise_sunset(S.utc_for_ltst(sol, 12, lon), lat, lon)["day_length_ltst_hours"]
    days = [dl(s) for s in range(1200, 1860, 40)]
    assert max(days) - min(days) > 1.5


def test_polar_night_and_bad_time():
    ss = S.sunrise_sunset(S.utc_for_ltst(1200, 12, 77.45), 89.0, 77.45)   # Ls ~ 288 (northern winter) at the pole
    assert ss["note"].startswith("Polar night")
    with pytest.raises(Exception):
        S.parse_utc("not a time")

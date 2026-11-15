"""Terrain illumination (atlas_core.illumination): incidence angle, self and cast shadows, lit fractions and time windows, on analytic surfaces."""
import math

import numpy as np
import pytest

from atlas_core import illumination as I, sun as S
from atlas_core.sets import SetStore
from atlas_core.site import Layer, Site
from atlas_core.testing import make_site

RES = 2.0


def flat_site(n=64, fn=None):
    c = (np.arange(n) + 0.5 - n / 2) * RES
    X, Y = np.meshgrid(c, c[::-1])
    z = np.zeros((n, n))
    if fn is not None:
        z = fn(X, Y, z)
    meta = {"title": "e", "units": "m", "resolution_m": RES, "source": {}}
    return Site(name="t", lat0=18.4, lon0=77.4, res_m=RES, size_px=n,
                layers={"elevation": Layer("elevation", "continuous", z.astype(np.float32), meta)})


def test_tilted_plane_facing_sun_has_smaller_incidence():
    el, az = 40.0, 90.0                                       # sun in the east
    s = I.sun_vector(el, az)
    east_up = flat_site(fn=lambda X, Y, z: np.tan(np.radians(20)) * X)       # rises east: faces west
    west_up = flat_site(fn=lambda X, Y, z: -np.tan(np.radians(20)) * X)      # faces east, toward the sun
    c_away = I.cos_incidence_grid(east_up.layers["elevation"].data, RES, s)[32, 32]
    c_toward = I.cos_incidence_grid(west_up.layers["elevation"].data, RES, s)[32, 32]
    assert math.degrees(math.acos(c_toward)) == pytest.approx(90 - el - 20, abs=1e-4)   # 30 deg
    assert math.degrees(math.acos(c_away)) == pytest.approx(90 - el + 20, abs=1e-4)     # 70 deg
    assert c_toward > c_away
    # perpendicular tilt: incidence on a flat plane is 90 - el
    flat = flat_site()
    assert math.degrees(math.acos(I.cos_incidence_grid(flat.layers["elevation"].data, RES, s)[10, 10])) == pytest.approx(50.0, abs=1e-4)
    # per-cell helper agrees with the grid
    z = west_up.layers["elevation"].data
    ci = I._cell_cos_incidence(z, np.array([32]), np.array([32]), RES, s)[0]
    assert ci == pytest.approx(c_toward, abs=1e-6)


def test_self_shadow_when_facing_away():
    site = flat_site(fn=lambda X, Y, z: np.tan(np.radians(60)) * X)           # faces west, 60 deg
    s = I.sun_vector(20.0, 90.0)                                              # sun low in the east
    assert I.cos_incidence_grid(site.layers["elevation"].data, RES, s)[32, 32] < 0


@pytest.mark.parametrize("el", [10.0, 20.0, 35.0])
def test_wall_shadow_length_axis_aligned(el):
    h = 4.0
    wall_col = 20

    def fn(X, Y, z):
        z = z.copy()
        z[:, wall_col] = h
        return z
    site = flat_site(fn=fn)
    for az, direction in ((270.0, +1), (90.0, -1)):                           # sun west: shadow runs east
        m = I.cast_shadow_grid(I._filled(site), RES, el, az)
        row = m[32]
        cols = np.nonzero(row)[0]
        run = [c for c in cols if (c - wall_col) * direction > 0]
        length = len(run) * RES
        expected = h / math.tan(math.radians(el))
        assert abs(length - expected) <= RES + 1e-9, (el, az, length, expected)
        assert all(0 < (c - wall_col) * direction for c in run) and not row[wall_col]
        # the cells on the sunward side are lit
        assert not any((c - wall_col) * direction < 0 for c in cols)


def test_wall_shadow_oblique_azimuth_points_away_from_sun():
    def fn(X, Y, z):
        z = z.copy()
        z[(np.abs(X) <= 2) & (np.abs(Y) <= 20)] = 5.0          # N-S wall 6 m x 40 m, 5 m high
        return z
    site = flat_site(n=96, fn=fn)
    m = I.cast_shadow_grid(I._filled(site), RES, 25.0, 225.0)  # sun in the south-west: shadow falls north-east
    ys, xs = np.nonzero(m)
    cx, cy = site.rc_to_xy(ys, xs)
    assert cx.mean() > 2 and cy.mean() > -5
    exp_len = 5.0 / math.tan(math.radians(25))
    # shadow centroid sits about half a shadow length from the wall along the anti-sun direction
    assert 0.3 * exp_len < math.hypot(cx.mean(), cy.mean() - 0) < 2.0 * exp_len + 25
    # ray march agrees with the grid sweep
    rng = np.random.default_rng(1)
    r, c = rng.integers(0, 96, 600), rng.integers(0, 96, 600)
    ray = ~I.points_lit(I._filled(site), RES, r, c, 25.0, 225.0)
    assert (ray == m[r, c]).mean() > 0.98


def test_ridge_shadows_side_away_from_sun_synthetic_site():
    site = make_site()
    z = I._filled(site)
    # synthetic ridge: x 100..112 m, peak 10 m at x = 106, south of y = 150; sun low in the east
    east_sun = I.cast_shadow_grid(z, RES, 20.0, 90.0)
    west_sun = I.cast_shadow_grid(z, RES, 20.0, 270.0)

    def frac(m, x0, x1):
        c0, c1 = int((x0 + 256) / RES), int((x1 + 256) / RES)
        r0, r1 = int((256 - 0) / RES), int((256 + 100) / RES)               # y from 0 down to -100
        return m[r0:r1, c0:c1].mean()
    assert frac(east_sun, 80, 98) > 0.3 and frac(east_sun, 118, 150) == 0     # west side dark, east side lit
    assert frac(west_sun, 118, 134) > 0.3 and frac(west_sun, 70, 98) == 0
    # ridge top is never shadowed by itself
    assert not east_sun[int((256 - -50) / RES), int((106 + 256) / RES)]
    # point queries agree
    sp = {"sun_elevation_deg": 20.0, "sun_azimuth_deg": 90.0}
    ev = I._eval_cells(site, z, *[np.array([v]) for v in site.rc_index(88.0, -50.0)], sp)
    assert ev["cls"][0] == I.CAST_SHADOW
    ev = I._eval_cells(site, z, *[np.array([v]) for v in site.rc_index(130.0, -50.0)], sp)
    assert ev["cls"][0] == I.LIT
    # shadow length on the flat-ish plain ~ h / tan(el) (10 m ridge, 20 deg => ~27 m beyond the crest)
    row = east_sun[int((256 + 50) / RES)]
    cols = np.nonzero(row[: int((106 + 256) / RES)])[0]
    xs = -256 + (cols + 0.5) * RES
    assert 14 < (106 - xs.min()) < 40


def test_whole_site_mask_set_and_lit_fraction():
    site = make_site()
    store = SetStore()
    r = I.illumination_at(site, store, sol=100, ltst="16:00")
    assert r["status"] == "day" and 0.9 < r["lit_fraction"] <= 1.0
    assert r["set_id"].startswith("set:")
    s = store.get(r["set_id"])
    assert s.kind == "regions" and s.count > 0
    classes = {m["attributes"]["illumination_class"] for m in s.members}
    assert "lit" in classes
    assert r["cells"]["unknown"] > 0                                        # the synthetic no-data hole is unknown
    # night: nothing lit
    n = I.illumination_at(site, store, sol=100, ltst="01:00")
    assert n["status"] == "sun_below_horizon" and n["lit_fraction"] == 0.0


def test_point_and_set_targets():
    site = make_site()
    store = SetStore()
    r = I.illumination_at(site, store, target="poi:landing", sol=100, ltst="16:00", radius_m=10)
    assert r["verdict"] == "lit" and r["locations"][0]["kind"].startswith("footprint")
    assert r["locations"][0]["incidence_deg_mean"] < 90
    rs = I.illumination_at(site, store, sol=100, ltst="16:00", make_set=True)
    r2 = I.illumination_at(site, store, target=f"{rs['set_id']}", sol=100, ltst="16:00")
    assert r2["lit_fraction"] is not None and r2["sampled_cells"] > 0
    with pytest.raises(Exception):
        I.illumination_at(site, store, target={"x": 99999, "y": 0}, sol=100, ltst="16:00")


def test_find_lit_windows():
    site = make_site()
    store = SetStore()
    w = I.find_lit_windows(site, store, "poi:landing", sol=100, min_elevation_deg=10)
    assert w["count"] == 1
    win = w["windows"][0]
    assert 5.5 < float(win["start_ltst"][:2]) + float(win["start_ltst"][3:5]) / 60 < 7.0     # elevation >= 10 deg shortly after sunrise
    assert 17.0 < float(win["end_ltst"][:2]) + float(win["end_ltst"][3:5]) / 60 < 18.5
    # edge elevation is the threshold (bisected)
    assert win["sun_elevation_deg_start"] == pytest.approx(10.0, abs=0.01)
    # a higher threshold shortens the window; an impossible one gives no window and a hint
    w2 = I.find_lit_windows(site, store, "poi:landing", sol=100, min_elevation_deg=40)
    assert w2["windows"][0]["duration_min"] < win["duration_min"]
    w3 = I.find_lit_windows(site, store, "poi:landing", sol=100, min_elevation_deg=89.9)
    assert w3["count"] == 0 and "Relax" in w3["hint"]
    # incidence limit: the plain tilts 1 deg, so incidence <= 30 needs elevation >= ~59
    w4 = I.find_lit_windows(site, store, "poi:landing", sol=100, min_elevation_deg=0, max_incidence_deg=30)
    assert w4["count"] == 1 and w4["windows"][0]["duration_min"] < win["duration_min"]
    # LTST window request and UTC request describe the same thing
    w5 = I.find_lit_windows(site, store, "poi:landing", sol=100, ltst_from=9, ltst_to=11, min_elevation_deg=10)
    assert w5["count"] == 1 and w5["windows"][0]["clipped_start"] and w5["windows"][0]["clipped_end"]
    assert w5["windows"][0]["duration_min"] == pytest.approx(2 * 60 * S.SOL_SECONDS / 86400, abs=0.5)   # 2 LTST hours
    with pytest.raises(Exception):
        I.find_lit_windows(site, store, "poi:landing", sol=100, step_min=0.001)


def test_ltst_parse():
    assert I.parse_clock("16:30") == 16.5 and I.parse_clock(9) == 9.0
    with pytest.raises(Exception):
        I.parse_clock("25:00")
    with pytest.raises(Exception):
        I.parse_clock("noonish")

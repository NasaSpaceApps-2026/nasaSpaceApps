"""Terrain-science layers: analytic-surface checks of atlas_core.terrain + real-site sanity checks."""
import math
from pathlib import Path

import numpy as np
import pytest

from atlas_core import terrain as T

ROOT = Path(__file__).resolve().parent.parent
SITE_DIR = ROOT / "data/build/site/jezero"
RES = 2.0


def grid(n=201):
    """x east, y north (m) for row 0 = north; origin at the centre."""
    h = n * RES / 2
    x = -h + (np.arange(n) + 0.5) * RES
    y = h - (np.arange(n) + 0.5) * RES
    return np.meshgrid(x, y)


def test_tilted_plane_aspect_slope_curvature_tri():
    X, Y = grid()
    s = math.tan(math.radians(20))
    for facing_deg in (0, 45, 90, 135, 180, 225, 270, 315):
        e, n = math.sin(math.radians(facing_deg)), math.cos(math.radians(facing_deg))
        z = -s * (e * X + n * Y)                                # descends toward the facing direction
        a = T.aspect_deg(z, RES)
        assert np.allclose(a[1:-1, 1:-1], facing_deg % 360, atol=1e-3), facing_deg
        assert np.isnan(a[0, 0]) and np.isnan(a[-1, 5])         # border: no invented data
        assert np.allclose(T.slope_deg(z, RES)[2:-2, 2:-2], 20, atol=1e-3)
        prof, plan = T.curvature_zt(z, RES)
        assert np.nanmax(np.abs(prof)) < 1e-6 and np.nanmax(np.abs(plan)) < 1e-6
        # TRI of a plane: neighbours differ by +-s*d*RES, d in {1, sqrt2}: sqrt(sum) = s*RES*sqrt(2*(4*1 + 4*... ))
        proj = [abs(e * dx + n * dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (dx, dy) != (0, 0)]
        expect = s * RES * math.sqrt(sum(p * p for p in proj))
        assert np.allclose(T.tri_riley(z)[1:-1, 1:-1], expect, rtol=1e-4)


def test_aspect_classes_and_flat():
    X, Y = grid(41)
    cases = {0: "N", 90: "E", 180: "S", 270: "W", 45: "NE", 315: "NW"}
    inv = {v: k for k, v in T.ASPECT_CLASSES.items()}
    for facing, name in cases.items():
        z = -math.tan(math.radians(10)) * (math.sin(math.radians(facing)) * X + math.cos(math.radians(facing)) * Y)
        assert (T.aspect_class(z, RES)[2:-2, 2:-2] == inv[name]).all(), name
    flat = 0.01 * X                                                # 0.57 deg: below the 1 deg threshold
    assert (T.aspect_class(flat, RES)[2:-2, 2:-2] == inv["flat"]).all()
    assert np.isnan(T.aspect_deg(flat, RES)[2:-2, 2:-2]).all()
    z = flat.copy(); z[20, 20] = np.nan
    assert T.aspect_class(z, RES)[20, 21] == -1                    # a NaN neighbour makes it unknown, not flat


@pytest.mark.parametrize("a", [0.002, -0.004])
def test_paraboloid_curvature_sign_and_value(a):
    X, Y = grid(201)
    z = 100 * 0 + a * (X ** 2 + Y ** 2)       # a>0 bowl (concave, NEGATIVE), a<0 dome (convex, POSITIVE)
    prof, plan = T.curvature_zt(z, RES, smooth_sigma_m=0)
    sl = (slice(30, -30), slice(30, -30))
    # Away from the apex the gradient is non-zero. Exact for a quadratic: profile = plan = -2a (1/m).
    far = (np.hypot(X, Y) > 40)[sl]
    assert np.allclose(prof[sl][far], -2 * a, rtol=1e-3)
    assert np.allclose(plan[sl][far], -2 * a, rtol=1e-3)
    # smoothing a quadratic with a symmetric kernel adds a constant only: curvature unchanged
    prof_s, _ = T.curvature_zt(z, RES, smooth_sigma_m=5.0)
    assert np.allclose(prof_s[sl][far], -2 * a, rtol=2e-3)


def test_saddle_profile_plan_signs_differ():
    X, Y = grid(101)
    z = 0.002 * (X ** 2 - Y ** 2)
    prof, plan = T.curvature_zt(z, RES, smooth_sigma_m=0)
    i, j = 50, 80      # on the +x axis: ground rises away from the centre along x (profile concave, -2a) while the
    #                    contour (hyperbola) bulges downslope toward the origin (plan convex, +2a): opposite signs
    assert prof[i, j] == pytest.approx(-0.004, rel=1e-3) and plan[i, j] == pytest.approx(0.004, rel=1e-3)


def test_tpi_ridge_and_hollow():
    X, Y = grid(201)
    ridge = -0.01 * X ** 2          # crest along x=0 : above surroundings
    t = T.tpi(ridge, 50.0, RES)
    assert t[100, 100] > 5                                         # on the crest
    assert np.nanmax(np.abs(T.tpi(0.3 * X, 50.0, RES)[40:-40, 40:-40])) < 1e-3   # plane: TPI 0
    assert T.tpi(-ridge, 50.0, RES)[100, 100] < -5
    assert np.isnan(t[0, 100]) and np.isnan(t[24, 100])            # disc cut by the edge -> NaN
    assert np.isfinite(t[25, 100])


def test_local_relief_step_and_edges():
    n = 301
    z = np.zeros((n, n)); z[:, 150:] = 12.0                        # 12 m step along column 150
    r = T.local_relief(z, 50.0, RES)
    assert r[150, 150] == 12 and r[150, 149] == 12 and r[150, 174] == 12     # sees the 0-cell at col 149: 25 cells = 50 m
    assert r[150, 175] == 0 and r[150, 124] == 0                   # one cell farther: step just outside the disc
    assert r[150, 125] == 12
    assert r[150, 100] == 0 and r[150, 200] == 0
    assert np.isnan(r[10, 150])                                    # window cut by edge
    z2 = z.copy(); z2[150, 150] = np.nan
    assert np.isnan(T.local_relief(z2, 50.0, RES)[150, 150])       # centre unknown
    # disc, not square: a lone peak at the corner of the square window is excluded
    p = np.zeros((n, n)); p[150 + 20, 150 + 20] = 7.0              # distance 56.6 m
    rp = T.local_relief(p, 50.0, RES)
    assert rp[150, 150] == 0 and rp[150 + 20 - 10, 150 + 20] == 7  # 10 cells away in one axis
    # the disc is exactly the cells within 50 m (radius 25 cells)
    q = np.zeros((n, n)); q[150, 150 + 25] = 3.0
    assert T.local_relief(q, 50.0, RES)[150, 150] == 3 and T.local_relief(q, 48.0, RES)[150, 150] == 0


def test_line_distance_transform():
    from sitebuild.geomorph import line_distance
    n = 201
    d = line_distance([-1000, 1000], [30.0, 30.0], n, RES)        # horizontal line 30 m north of centre, extends off-grid
    X, Y = grid(n)
    assert np.abs(d - np.abs(Y - 30.0)).max() <= RES               # within one cell
    d2 = line_distance([0.0, 0.0], [-500.0, 0.0], n, RES)         # ends at the grid centre, starts outside
    assert abs(d2[100, 100] - 1.0) <= RES and d2[0, 100] > 150


def two_unit_grid(n=201):
    u = np.zeros((n, n), np.int16); u[:, 100:] = 1                 # contact between col 99 and 100
    return u


def test_contact_distance_and_diversity():
    u = two_unit_grid()
    c = T.contact_mask(u)
    assert set(np.where(c.any(0))[0]) == {99, 100}
    d = T.distance_to(c, RES)
    assert d[50, 99] == 0 and d[50, 100] == 0
    assert d[50, 90] == pytest.approx(9 * RES) and d[50, 110] == pytest.approx(10 * RES)
    u2 = u.copy(); u2[:, :10] = -1                                 # unmapped strip is not a contact
    assert not T.contact_mask(u2)[:, 9:11].any()
    div = T.count_within([u == 0, u == 1], 20.0, RES)
    assert div[100, 100] == 2 and div[100, 90] == 2                # 20 m = 10 cells reaches the other unit
    assert div[100, 89] == 1 and div[100, 150] == 1                # 11 cells away: not reached; deep inside: 1
    assert np.isnan(div[0, 50]) and np.isnan(div[100, 5])          # disc cut by edge


def test_mineral_count_ignores_no_coverage():
    n = 101
    a = np.full((n, n), -1, np.int8); a[:, 50:] = 2               # weak detection east half, no coverage west
    b = np.zeros((n, n), np.int8); b[:, :20] = 3                   # strong in the far west, not_detected elsewhere
    cnt = T.count_within([a >= 2, b >= 2], 10.0, RES)
    assert cnt[50, 70] == 1 and cnt[50, 30] == 0                   # no_coverage never counts as a detection


needs_site = pytest.mark.skipif(not (SITE_DIR / "aspect_deg.tif").exists(), reason="geomorph layers not built")


@pytest.fixture(scope="module")
def site():
    from atlas_core import load_site
    return load_site(SITE_DIR)


@needs_site
def test_catalogue_entries(site):
    ids = ["aspect_deg", "aspect_class", "curvature_profile", "curvature_plan", "tpi_50m", "tri", "local_relief_50m",
           "local_relief_200m", "outcrop_exposure", "dist_to_traverse_m", "dist_to_unit_contact_m", "unit_diversity_100m",
           "mineral_diversity_100m"]
    for i in ids:
        m = site.layers[i].meta
        for k in ("title", "units", "resolution_m", "measures", "does_not_measure", "quality", "source", "method"):
            assert k in m, (i, k)
        assert site.layers[i].data.shape == (4096, 4096)
    assert site.layers["aspect_class"].kind == "categorical" and site.layers["aspect_class"].classes[8] == "flat"
    assert "NaN" in site.layers["aspect_deg"].meta["quality"]
    assert "25 m" in site.layers["dist_to_unit_contact_m"].meta["quality"]
    assert "rock" in site.layers["outcrop_exposure"].meta["does_not_measure"]


@needs_site
def test_relief_higher_on_delta_front_than_landing_floor(site):
    """Delta-front scarps (steepest, highest-relief ground of the site) vs the flat crater floor around the landing site."""
    r200 = site.layer("local_relief_200m").data
    slope = site.layer("slope_2m").data
    x, y = site.latlon_to_xy(18.4447, 77.4508)
    ri, ci = site.rc_index(x, y)
    floor = r200[ri - 100:ri + 100, ci - 100:ci + 100]                 # 400 m box around the landing site
    assert np.nanmedian(floor) < 15
    steep = np.isfinite(r200) & (np.nan_to_num(slope) > 30)
    assert np.nanmedian(r200[steep]) > 4 * np.nanmedian(floor)
    assert np.nanpercentile(r200, 99) > 10 * np.nanmedian(floor)
    ox = site.layer("outcrop_exposure").data
    assert np.nanmedian(ox[ri - 100:ri + 100, ci - 100:ci + 100]) == 0
    assert np.nanpercentile(ox, 99) > 10


@needs_site
def test_aspect_on_steep_region_agrees_with_gradient(site):
    z = site.layer("elevation").data
    a = site.layer("aspect_deg").data
    s = site.layer("slope_2m").data
    rr, cc = np.where(np.nan_to_num(s) > 35)
    k = len(rr) // 2
    r, c = int(rr[k]), int(cc[k])
    assert 2 <= r < 4094 and 2 <= c < 4094
    # moving 3 cells downslope must lower the surface: check against a direct finite difference
    ang = math.radians(float(a[r, c]))
    dr, dc = -math.cos(ang) * 3, math.sin(ang) * 3            # row 0 is north
    zz = T.bilinear(z, r + dr, c + dc)
    assert zz < z[r, c]
    cls = site.layer("aspect_class")
    inv = {v: k for k, v in cls.classes.items()}
    assert cls.data[r, c] in range(8)
    assert (cls.data[np.isnan(a) & (cls.data != -1)] == inv["flat"]).all()


@needs_site
def test_distance_layers(site):
    d = site.layer("dist_to_traverse_m").data
    assert np.nanmin(d) < 2.5 and np.nanmax(d) > 500
    x, y = site.latlon_to_xy(18.4447, 77.4508)
    ri, ci = site.rc_index(x, y)
    assert d[ri, ci] < 60                                            # landing is the start of the traverse
    ud = site.layer("unit_diversity_100m").data
    dc = site.layer("dist_to_unit_contact_m").data
    ok = np.isfinite(ud) & np.isfinite(dc)
    assert (ud[ok & (dc > 101)] == 1).mean() > 0.999                  # farther than 100 m from any contact: one unit
    assert np.nanmin(ud) >= 1

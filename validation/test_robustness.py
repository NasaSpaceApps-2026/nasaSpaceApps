"""pytest wrapper for validation/robustness.py.

    .venv/bin/python -m pytest validation -m "not slow" -q      # fast (about 1 min)
    .venv/bin/python -m pytest validation -q                    # everything (about 10 min)

Assertions encode what MUST hold (invariances, bounds, exact analytic answers). Quantities that are legitimately
sensitive (noise Monte Carlo, resolution, thresholds) are only asserted to run and to be within coarse sanity limits;
their numbers are reported in docs/stress-test-robustness.md.
"""
import pytest

import robustness as rb

pytestmark = pytest.mark.skipif(not (rb.SITE_DIR / "site.yaml").exists(), reason="site not built")


@pytest.fixture(scope="module")
def site():
    return rb.load_site(rb.SITE_DIR)


@pytest.fixture(scope="module")
def s1(site):
    return rb.section1(site, quick=True)


def test_analytic_surfaces_exact(s1):
    a = s1["synthetic_surfaces"]
    assert a["plane_slope_err_max"] < 1e-3          # deg; Horn is exact on planes
    assert a["plane_aspect_err_max"] < 1e-3
    assert a["paraboloid_slope_err_max"] < 1e-3     # Horn is exact on quadratics (central differences)
    assert a["paraboloid_aspect_err_max"] < 5.0     # asymptotically exact; 1 deg-scale error near the apex where gradient ~ 0


def test_rotation_invariance(s1):
    for name, r in s1["rotation"].items():
        assert r["slope_max_abs_diff_deg"] < 1e-3, name
        assert r["aspect_max_diff_deg"] < 0.05, name
        assert r["aspect_defined_mismatch_cells"] < 100, name


def test_walk_symmetry_and_triangle(s1):
    w = s1["walk_symmetry"]
    assert w["max_abs_asym_walk_m"] < 1e-3                        # metric walk_m is exactly symmetric
    assert w["max_rel_asym"] < 0.02                               # time: bounded by one cell-entry cost
    assert s1["triangle"]["violations_gt_one_cell"] == 0


def test_monotonicity(s1):
    for name, m in s1["find_locations_monotonicity"].items():
        assert m["candidate_monotone"], name                      # spot COUNT after greedy spreading is not guaranteed


def test_nearest_minimal(s1):
    assert s1["nearest_minimality"]["straight"]["violations"] == 0
    assert s1["nearest_minimality"]["walk_time"]["violations"] == 0


def test_los_reciprocity_high(s1):
    assert s1["los_reciprocity"]["agreement"] > 0.97


def test_walk_direction_bias_bounded(s1):
    ratios = s1["walk_metric_direction_bias"]["ratio_walk_over_straight_by_bearing"].values()
    assert max(ratios) < 1.10 and min(ratios) > 0.99


def test_coordinates(site):
    r = rb.section2(site)
    assert r["round_trip_max_m"] < 1e-6
    assert r["xy_to_latlon_vs_pyproj_max_m"] < 0.01
    assert r["latlon_to_xy_vs_pyproj_max_m"] < 0.01
    assert r["geotiff_vs_rc_to_xy_max_m"] < 1e-6


def test_sanity_bounds(site):
    r = rb.section6(site)
    assert not r["failed_checks"], r["failed_checks"]


@pytest.mark.slow
def test_resolution_consistency(site):
    r = rb.section3(site)
    if "skipped" in r:
        pytest.skip(r["skipped"])
    for name, w in r["windows"].items():
        if "skipped" in w:
            continue
        assert w["reproduction_vs_site_layer"]["elev_rms_vs_site_m"] < 0.5, name
        # coarser baseline smooths: p99 slope must not grow with a longer baseline
        s = w["slope_stats"]
        assert s["4m"]["p99"] <= s["1m"]["p99"] + 1e-6, name


@pytest.mark.slow
def test_noise_monte_carlo_small(site):
    r = rb.section4(site, runs=3)
    for m, s in r["summary"].items():
        assert 0 <= s["rover_scout_class_changed"]["mean"] <= 1
        assert 0 <= s["top10_jaccard_tol25m"]["mean"] <= 1
    # correlated (long-wavelength) noise barely changes slopes; white 0.25 m noise at 2 m posts changes them a lot
    assert r["summary"]["corr_0.25"]["slope_mae_deg"]["mean"] < r["summary"]["white_0.25"]["slope_mae_deg"]["mean"]


@pytest.mark.slow
def test_threshold_sensitivity(site):
    r = rb.section5(site)
    g = r["landing_pad_grid"]
    assert g["slope<=12,R=50"]["candidate_cells"] >= g["slope<=10,R=50"]["candidate_cells"] >= g["slope<=8,R=50"]["candidate_cells"]
    assert g["slope<=10,R=25"]["candidate_cells"] >= g["slope<=10,R=50"]["candidate_cells"] >= g["slope<=10,R=75"]["candidate_cells"]
    a = r["eva_envelope_by_max_slope"]
    assert a["30"]["budget_120min"]["area_km2"] >= a["25"]["budget_120min"]["area_km2"] >= a["20"]["budget_120min"]["area_km2"]

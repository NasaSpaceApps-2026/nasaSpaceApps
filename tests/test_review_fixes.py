"""Regression tests for issues found in the real-data end-to-end review."""
import importlib.util
import sys
from pathlib import Path

import pytest

from atlas_core.errors import AtlasError
from atlas_core.site import Layer
from atlas_core.testing import make_site

ROOT = Path(__file__).resolve().parents[1]


def _format_module():
    spec = importlib.util.spec_from_file_location("mcp_format_under_test", ROOT / "mcp" / "format.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("src,expected", [
    (None, "user-supplied"),
    ("assumed", "assumed"),
    ("assumed (pads must not rest on fill)", "assumed (pads must not rest on fill)"),
    ("TBD: cite", "citation pending"),
    ("user-specified", "user-specified"),
    ("default max(2 * footprint radius, 100)", "default max(2 * footprint radius, 100)"),
    ("Fergason et al. 2020", "cited: Fergason et al. 2020"),
])
def test_source_label_never_calls_assumptions_cited(src, expected):
    assert _format_module().source_label(src) == expected


def test_quality_flag_is_not_a_search_target():
    from devapi.search_sites import search_targets
    site = make_site()
    q = site.layers["elevation"].data.copy()
    import numpy as np
    site.layers["quality_flag"] = Layer("quality_flag", "categorical", np.zeros(q.shape, np.int16),
                                        {"title": "Elevation data quality"}, classes={0: "hirise_dtm"})
    targets = {t["target"] for t in search_targets(site)}
    assert "hirise_dtm" not in targets and "carbonate" in targets


def test_no_science_layers_gives_honest_error():
    from devapi.search_sites import search_targets
    site = make_site()
    for k in ("carbonate", "geologic_unit"):
        del site.layers[k]
    assert search_targets(site) == []


def test_source_label_user_or_default_is_not_cited():
    assert _format_module().source_label("user-specified or default") == "user-specified or default"


def test_describe_groups_not_detected_layers_on_one_line():
    f = _format_module()
    d = {"radius_m": 0, "locations": [{
        "id": "p", "x": 0, "y": 0, "lat": 0, "lon": 0, "quality": "hirise_dtm",
        "values": {"carbonate": {"tier": 0, "tier_name": "not_detected"},
                   "kaolinite": {"tier": 0, "tier_name": "not_detected"},
                   "olivine": {"unknown": True}},
        "evidence": {"carbonate": {}, "kaolinite": {}, "olivine": {}},
        "nearest_ground_truth": None}]}
    text = f.fmt_describe(d)
    assert text.count("absence of evidence") == 1
    assert "carbonate, kaolinite" in text and "no_coverage" in text and "olivine" in text

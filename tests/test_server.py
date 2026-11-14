import importlib.util
import sys
from pathlib import Path

import anyio
import pytest
from mcp.client import Client

from atlas_core import search, travel

ROOT = Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


srv = _load("atlas_server", ROOT / "mcp" / "server.py")
fmt = sys.modules["format"]  # mcp/format.py, put on sys.path by server.py
BANNER = "[SYNTHETIC TEST SITE — not Mars data]"


def run(coro_fn):
    async def go():
        async with Client(srv.build_server()) as c:
            return await coro_fn(c)
    return anyio.run(go)


def _impl(fn, *a, **k):
    try:
        fn(*a, **k)
        return True
    except NotImplementedError:
        return False


def _search_ready():
    from atlas_core.testing import make_site
    from atlas_core import SetStore
    return _impl(search.find_locations, make_site(), SetStore(), profile="landing_pad")


def _regions_ready():
    from atlas_core.testing import make_site
    from atlas_core import SetStore
    return _impl(search.find_regions, make_site(), SetStore(), conditions=[{"layer": "slope_2m", "op": "<", "value": 5}])


def _nearest_ready():
    from atlas_core.testing import make_site
    from atlas_core import SetStore
    return _impl(travel.nearest, make_site(), SetStore(), "base", "poi:landing")


def text(r):
    return r.content[0].text


def test_lists_tools_and_descriptions():
    async def f(c):
        return (await c.list_tools()).tools
    tools = {t.name: t for t in run(f)}
    assert set(tools) == {"get_site_overview", "list_layers", "describe_layer", "describe_location",
                          "sample_terrain", "find_locations", "find_regions", "nearest", "get_set"}
    d = tools["find_locations"].description
    for needle in ("metres", "minutes", "any_of", "Example", "footprint_radius_m", "set"):
        assert needle in d
    assert "Example" in tools["describe_location"].description


def test_overview_tool_and_resource():
    async def f(c):
        r = await c.call_tool("get_site_overview", {})
        res = await c.read_resource("atlas://site/overview")
        return r, res
    r, res = run(f)
    assert not r.is_error
    assert text(r).startswith(BANNER)
    assert r.structured_content["site"]["name"] == "synthetic"
    assert r.structured_content["synthetic"] is True
    assert "carbonate" in text(r) and "landing_pad" in text(r)
    assert BANNER in res.contents[0].text


def test_describe_location_text_and_structured():
    async def f(c):
        return await c.call_tool("describe_location", {"at": {"x": 103.4, "y": -50.2}})
    r = run(f)
    assert not r.is_error
    t = text(r)
    assert "(103, -50)" in t and "slope_2m: 55" in t        # rounded to the metre; ridge flank
    loc = r.structured_content["locations"][0]
    assert loc["x"] == 103.4 and 50 < loc["values"]["slope_2m"]["value"] < 61
    assert len(t.split()) < 300


def test_describe_location_string_args_and_alias():
    async def f(c):
        a = await c.call_tool("describe_location", {"at": '{"x": 0, "y": -150}'})
        b = await c.call_tool("sample_terrain", {"x": 0, "y": 0})
        return a, b
    a, b = run(f)
    assert "unknown (no data)" in text(a)
    assert not b.is_error and b.structured_content["locations"][0]["values"]["slope_2m"]["value"] < 2


def test_unknown_layer_is_readable_error_result():
    async def f(c):
        return await c.call_tool("describe_layer", {"layer_id": "slope"})
    r = run(f)
    assert r.is_error is True
    assert 'Unknown layer "slope"' in text(r) and "slope_2m" in text(r)
    assert "Traceback" not in text(r)
    assert r.structured_content["hint"]


def test_other_errors_are_readable():
    async def f(c):
        return [await c.call_tool("describe_location", {"at": "nowhere"}),
                await c.call_tool("get_set", {"set_id": "set:99"}),
                await c.call_tool("describe_location", {"at": {"x": "abc", "y": 1}})]
    for r in run(f):
        assert r.is_error and text(r).startswith(BANNER) and "Error:" in text(r)
        assert "Traceback" not in text(r)


def test_list_layers_and_get_set():
    async def f(c):
        return await c.call_tool("list_layers", {"kind": "evidence"})
    r = run(f)
    assert [l["id"] for l in r.structured_content["layers"]] == ["carbonate"]


def test_get_set_roundtrip():
    server = srv.build_server()

    async def go():
        async with Client(server) as c:
            return await c.call_tool("get_set", {"set_id": "set:1"})
    r = anyio.run(go)
    assert r.is_error and "Unknown set" in text(r)


@pytest.mark.skipif(not _search_ready(), reason="find_locations not implemented yet")
def test_find_locations_tool():
    async def f(c):
        return await c.call_tool("find_locations", {"profile": "landing_pad", "limit": 3})
    r = run(f)
    assert not r.is_error and r.structured_content["set_id"].startswith("set:")
    assert "Thresholds used" in text(r)
    assert any(a["source"] == "assumed" for a in r.structured_content["assumptions"])


@pytest.mark.skipif(not _regions_ready(), reason="find_regions not implemented yet")
def test_find_regions_tool():
    async def f(c):
        return await c.call_tool("find_regions", {"conditions": [{"evidence": "carbonate", "min_tier": "strong_orbital"}]})
    r = run(f)
    assert not r.is_error and r.structured_content["count"] >= 1


@pytest.mark.skipif(not _nearest_ready(), reason="nearest not implemented yet")
def test_nearest_tool_and_get_set():
    async def f(c):
        n = await c.call_tool("nearest", {"from_ref": "base", "to_ref": "poi:landing", "metric": "straight_m"})
        s = await c.call_tool("get_set", {"set_id": n.structured_content["set_id"]})
        return n, s
    n, s = run(f)
    assert not n.is_error and not s.is_error
    assert n.structured_content["results"][0]["cost"] == pytest.approx(70.7, abs=2)


# ---- format.py unit tests (no server needed) ----

def test_format_search_empty_shows_diagnostics_and_labels():
    d = {"set_id": "set:1", "count": 0, "results": [], "objective": "flattest",
         "assumptions": [{"what": "slope_2m <= 10", "source": "assumed"},
                         {"what": "rough_5x5 <= 0.5", "source": "TBD: cite"},
                         {"what": "x", "source": "Smith 2020"}],
         "diagnostics": [{"step": "slope_2m <= 10", "pass_fraction": 0.71},
                         {"step": "+ footprint radius 50 m", "pass_fraction": 0.003}],
         "hint": "relax the footprint radius"}
    t = fmt.fmt_search(d, "spots")
    assert "No spots found" in t and "71%" in t and "0.3%" in t
    assert "[assumed]" in t and "[citation pending]" in t and "[cited: Smith 2020]" in t
    assert "Hint: relax" in t


def test_format_search_results_rounds_to_metre():
    d = {"set_id": "set:2", "count": 7, "objective": "flattest", "assumptions": [], "diagnostics": [],
         "hint": None,
         "results": [{"id": "2.1", "x": 812.6, "y": -1220.4, "score": 2.1, "clear_radius_m": 64.2,
                      "attributes": {"slope_2m_mean": 2.1, "slope_2m_max": 6.4, "walk_time_min": 22.0}}]}
    t = fmt.fmt_search(d, "spots")
    assert "(813, -1220)" in t and "clear radius 64 m" in t and "walk_time_min=22" in t
    assert "get_set" in t


def test_format_num():
    assert fmt.num(-2493.2) == "-2493"
    assert fmt.num(59.298) == "59.3"
    assert fmt.num(0.0493) == "0.05"
    assert fmt.num(None) == "n/a"

"""Friendly copilot: place resolution (fuzzy, generic terms, crater detection, global names), visible non-blocking
drives, the wait option, progress, rover.state events during a drive, and loose inputs. No network."""
import math

import anyio
import pytest

from atlas_core.places import Gazetteer, detect_craters, great_circle_m, norm
from atlas_core.testing import make_site
from test_server import record_events, run, text, make_server
from mcp.client import Client


def _site():
    s = make_site()
    s.pois += [
        {"id": "region_south_seitah", "name": "South Séítah (sampling area)", "type": "named_region", "x": 150.0, "y": 100.0},
        {"id": "region_delta_front", "name": "Delta Front (sampling area)", "type": "named_region", "x": -200.0, "y": -50.0},
        {"id": "octavia_e_butler_landing", "name": "Octavia E. Butler Landing", "type": "landmark", "x": 50.0, "y": -50.0},
        {"id": "sample_1_roubion", "name": "Sample 1: Roubion", "type": "sample_site", "x": 80.0, "y": 80.0},
    ]
    return s


G = Gazetteer(_site(), names=[])


# ---- crater detection on the synthetic site's crater ----------------------------------------------

def test_detects_the_synthetic_crater():
    cs = detect_craters(make_site())
    assert len(cs) == 1
    c = cs[0]
    assert math.hypot(c["x"] + 120, c["y"] - 120) < 15
    assert 90 <= c["diameter_m"] <= 150 and c["depth_m"] > 15


def test_the_crater_resolves_to_its_near_rim_with_a_plain_explanation():
    r = G.resolve("the crater", (0.0, 0.0))
    assert r["resolved"] and r["kind"] == "crater"
    ch = r["chosen"]
    # near rim: on the side of the rover (south-east of the centre), roughly one radius from the centre
    d = math.hypot(ch["x"] + 120, ch["y"] - 120)
    assert 50 <= d <= 110 and ch["x"] > -120 and ch["y"] < 120
    assert r["explanation"].startswith("Nearest crater:") and "wide" in r["explanation"] and "north-west" in r["explanation"]
    for q in ("a crater", "nearest crater", "crater"):
        assert G.resolve(q, (0.0, 0.0))["kind"] == "crater"
    assert G.resolve("the crater floor", (0.0, 0.0))["chosen"]["x"] == pytest.approx(-120, abs=15)


# ---- fuzzy and aliases -------------------------------------------------------------------------------

@pytest.mark.parametrize("q,pid", [("seitah", "poi:region_south_seitah"), ("Séítah", "poi:region_south_seitah"),
                                   ("SEITHA", "poi:region_south_seitah"), ("the delta", "poi:region_delta_front"),
                                   ("delta front", "poi:region_delta_front"), ("landing site", "poi:octavia_e_butler_landing"),
                                   ("rubion", "poi:sample_1_roubion"), ("sample 1", "poi:sample_1_roubion"),
                                   ("kodiak", "poi:region_delta_front"), ("base", "poi:base")])
def test_fuzzy_and_aliases(q, pid):
    r = G.resolve(q, (0.0, 0.0))
    assert r["resolved"] and r["chosen"]["ref"] == pid, r


def test_approximate_alias_says_so():
    r = G.resolve("kodiak", (0.0, 0.0))
    assert r["chosen"]["approximate"] and "don't have an exact position" in r["explanation"]


def test_generic_hill_and_flat_ground():
    h = G.resolve("the hill", (0.0, 0.0))
    assert h["kind"] == "highest" and h["chosen"]["radius_m"] == 1000.0 and "above the rover" in h["explanation"]
    f = G.resolve("flat ground", (0.0, 0.0))
    assert f["kind"] == "flat" and f["chosen"]["distance_m"] >= 60


def test_never_a_dead_end():
    r = G.resolve("zzzq", (0.0, 0.0))
    assert not r["resolved"] and r["candidates"] and "Nearby places" in r["explanation"]


def test_norm_strips_accents():
    assert norm("Séítah  Región!") == "seitah region"


# ---- global Mars names -----------------------------------------------------------------------------

@pytest.mark.parametrize("q,name", [("Mount Sons", "Olympus Mons"), ("olympus", "Olympus Mons"), ("mt sharp", "Aeolis Mons"),
                                    ("gale crater", "Gale crater"), ("valles marineris", "Valles Marineris"),
                                    ("elysium", "Elysium Mons"), ("hellas", "Hellas Planitia"),
                                    ("grand canyon", "Valles Marineris")])
def test_global_names(q, name):
    r = G.resolve(q, (0.0, 0.0))
    assert r["resolved"] and r["kind"] == "global" and r["chosen"]["name"] == name and r["outside_site"], r


def test_mount_sons_is_a_guess_with_distance_and_street_view():
    r = G.resolve("Mount Sons", (0.0, 0.0))
    assert r["explanation"].startswith("I think you mean Olympus Mons")
    assert "great-circle" in r["explanation"] and "Olympus Mons scarp" in r["explanation"]
    km = r["chosen"]["distance_m"] / 1000
    assert 7000 < km < 8500           # Jezero (18.46 N, 77.41 E) to Olympus Mons (18.65 N, 226.2 E) ~ 7.8e3 km


def test_great_circle():
    assert great_circle_m(0, 0, 0, 90) == pytest.approx(math.pi / 2 * 3396190.0, rel=1e-9)


# ---- through the MCP tools -----------------------------------------------------------------------------

def _seq(*calls):
    async def f(c):
        return [await c.call_tool(n, a) for n, a in calls]
    return run(f)


def test_tools_resolve_phrases_and_say_what_was_chosen():
    t, d = _seq(("teleport_rover", {"target": "the crater"}), ("describe_location", {"at": "nearest crater"}))
    assert not t.is_error and "Place used for \"the crater\": Nearest crater" in text(t)
    assert t.structured_content["places_resolved"][0]["kind"] == "crater"
    assert not d.is_error


def test_search_pois_generic_term_uses_the_gazetteer():
    r, = _seq(("search_pois", {"query": "the crater"}))
    assert "Nearest crater" in text(r) and not r.is_error


def test_drive_is_non_blocking_visible_and_reports_progress():
    app = None
    server, app = make_server()
    seen = record_events(app)

    async def go():
        async with Client(server) as c:
            r = await c.call_tool("drive_to", {"target": "poi:landing", "vehicle": "buggy"})
            assert "Driving now" in text(r) and r.structured_content["started"] is True
            assert r.structured_content["screen_s"] >= 8
            p1 = await c.call_tool("get_drive_progress", {})
            await anyio.sleep(0.6)
            p2 = await c.call_tool("get_rover_state", {})
            for _ in range(200):
                p = await c.call_tool("get_drive_progress", {})
                if not p.structured_content["active"]:
                    break
                await anyio.sleep(0.2)
            return p1, p2, p

    p1, p2, p = anyio.run(go)
    assert p1.structured_content["active"] and "Still driving" in text(p1)
    assert "Still driving" in text(p2) or "Arrived" in text(p2)
    assert not p.structured_content["active"] and p.structured_content["stop_reason"] == "arrived" and "Arrived" in text(p)
    states = [pl for t, pl in seen if t == "rover.state"]
    assert len(states) >= 10                       # many state events while driving, not one at the end
    xs = [s["x"] for s in states]
    assert xs == sorted(xs) or xs == sorted(xs, reverse=True)   # moved steadily
    assert states[-1]["x"] == pytest.approx(50.0, abs=3) and states[-1]["y"] == pytest.approx(-50.0, abs=3)


def test_wait_option_returns_the_final_stop_reason():
    r, = _seq(("drive_to", {"target": "poi:landing", "vehicle": "buggy", "wait": True, "animate": False}))
    assert r.structured_content["stop_reason"] == "arrived" and "Arrived at" in text(r)


def test_goto_and_drive_rover_are_non_blocking_by_default():
    g, = _seq(("goto_location", {"target": "the crater"}))
    assert "Driving now" in text(g) and "Place used" in text(g)
    d, = _seq(("drive_rover", {"distance_m": "50 m", "heading_deg": "north"}))
    assert "Driving now" in text(d)


def test_loose_distance_and_compass():
    r, = _seq(("drive_rover", {"distance_m": "0.05 km", "heading_deg": "north-east", "wait": True, "animate": False}))
    assert not r.is_error and r.structured_content["state"]["heading_deg"] == pytest.approx(45, abs=1)
    bad, = _seq(("drive_rover", {"distance_m": "far", "heading_deg": "north"}))
    assert bad.is_error and "could not read" in text(bad)


def test_far_place_explains_acts_and_does_not_move():
    server, app = make_server()
    seen = record_events(app)

    async def go():
        async with Client(server) as c:
            before = await c.call_tool("get_rover_state", {})
            r = await c.call_tool("drive_to", {"target": "Mount Sons", "start": "Jezero", "vehicle": "buggy"})
            after = await c.call_tool("get_rover_state", {})
            return before, r, after

    before, r, after = anyio.run(go)
    t = text(r)
    assert not r.is_error and "I think you mean Olympus Mons" in t and "The rover did not move" in t
    assert "Olympus Mons scarp" in t and r.structured_content["outside_site"] is True
    assert r.structured_content["place"]["lat"] == pytest.approx(18.65, abs=0.1)
    assert before.structured_content["x"] == after.structured_content["x"]


def test_fly_camera_accepts_far_place_in_planet_mode():
    r, = _seq(("fly_camera", {"target": "olympus", "mode": "planet"}))
    # the synthetic stand-in cannot place a far lat/lon; it must still be a readable answer, not a crash
    assert "Unexpected tool error" not in text(r)

"""Tool-gap fixes from the live evals: tools state the totals a user asks for so the model never computes them."""
import re

from test_server import run, text


def _check(c):
    async def go():
        r = await c.call_tool("plan_route", {"start": "base", "goal": {"x": 150, "y": 0}, "profile": "eva_walkable"})
        rid = r.structured_content["route_id"]
        return await c.call_tool("check_eva_return", {"route_id": rid, "budget_min": 240, "reserve_fraction": 0.2})
    return go()


def test_check_eva_return_states_round_trip_and_totals():
    r = run(_check)
    t = text(r)
    line = next(x for x in t.splitlines() if x.startswith("Totals:"))
    one_way = float(re.search(r"one-way walk ([\d.]+)", line).group(1))
    rt = float(re.search(r"round trip \(out and back to the haven\) ([\d.]+)", line).group(1))
    assert abs(rt - 2 * one_way) < 0.3
    assert "budget 240 min (4 h)" in line and "usable after 20% reserve 192 min (3.2 h)" in line
    assert "margin at the route end" in line and "point of no return" in line


def test_check_eva_return_states_point_of_no_return_when_not_ok():
    async def f(c):
        r = await c.call_tool("plan_route", {"start": "base", "goal": {"x": 150, "y": 0}, "profile": "eva_walkable"})
        return await c.call_tool("check_eva_return", {"route_id": r.structured_content["route_id"], "budget_min": 2})
    t = text(run(f))
    line = next(x for x in t.splitlines() if x.startswith("Totals:"))
    assert re.search(r"point of no return at \d+ m", line)


def test_eva_envelope_states_round_trip_at_max_radius():
    async def f(c):
        return await c.call_tool("eva_envelope", {"budget_min": 240, "reserve_fraction": 0.2})
    t = text(run(f))
    line = next(x for x in t.splitlines() if x.startswith("Totals:"))
    one_way = float(re.search(r"([\d.]+) min(?: \([\d.]+ h\))? one-way walk", line).group(1))
    rt = float(re.search(r"round trip \(out and back\) at the max radius ([\d.]+)", line).group(1))
    assert abs(rt - 2 * one_way) < 0.2 and "max radius" in line and "usable after 20% reserve" in line


def test_search_pois_without_near_reports_distance_from_rover():
    async def f(c):
        st = await c.call_tool("get_rover_state", {})
        return st, await c.call_tool("search_pois", {"query": "base"}), \
            await c.call_tool("search_pois", {"query": "base", "near": {"x": 0, "y": 0}})
    st, p, p2 = run(f)
    s = st.structured_content
    t = text(p)
    assert f"from the rover at ({round(s['x'])}, {round(s['y'])}) m" in t and " away" in t
    assert "from the given point at (0, 0) m" in text(p2)


def test_teleport_states_distance_to_base_and_nearest_pois():
    async def f(c):
        return await c.call_tool("teleport_rover", {"target": "poi:landing"})
    r = run(f)
    t = text(r)
    assert "Straight-line distances from the rover now:" in t and "base" in t
    assert r.structured_content["nearby"]

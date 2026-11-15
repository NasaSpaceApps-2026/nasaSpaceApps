"""devapi: every endpoint on the synthetic site, via the FastAPI TestClient."""
import math
import threading

import numpy as np
import pytest
from fastapi.testclient import TestClient

from atlas_core import route as R
from atlas_core.testing import make_site
from devapi import create_app


@pytest.fixture()
def app():
    return create_app(make_site(), synthetic=True)


@pytest.fixture()
def c(app):
    return TestClient(app)


def post(c, path, body, status=200):
    r = c.post(path, json=body)
    assert r.status_code == status, r.text
    return r.json()


def dense_slopes(site, points, step=1.0):
    out = []
    for a, b in zip(points[:-1], points[1:]):
        L = math.hypot(b["x"] - a["x"], b["y"] - a["y"])
        for t in np.linspace(0, 1, max(2, int(L / step) + 1)):
            out.append(R.ground_slope(site, a["x"] + (b["x"] - a["x"]) * t, a["y"] + (b["y"] - a["y"]) * t))
    return out


# ---- basics and the query engine ---------------------------------------------------------------

def test_health_site_layers_overview(c):
    h = c.get("/health").json()
    assert h["ok"] and h["synthetic"] is True
    s = c.get("/site").json()
    assert s["size_m"] == 512 and "rover_scout" in s["limits"] and s["limits"]["rover_scout"]["max_slope_deg"] == 20
    assert "assumed" in s["limits"]["rover_scout"]
    assert [l["id"] for l in c.get("/layers", params={"kind": "evidence"}).json()["layers"]] == ["carbonate"]
    assert c.get("/layers/slope_2m").json()["id"] == "slope_2m"
    ov = c.get("/overview").json()
    assert ov["markers"] == [] and {t["target"] for t in ov["search_targets"]} >= {"carbonate", "crater_floor"}
    assert ov["rover"]["x"] == 0 and ov["pois"]


def test_atlas_error_is_422_with_hint(c):
    r = c.get("/layers/nope")
    assert r.status_code == 422
    assert r.json()["error"].startswith('Unknown layer "nope"') and "slope_2m" in r.json()["hint"]
    r = c.post("/describe", json={"radius_m": 3})
    assert r.status_code == 422 and "at" in r.json()["error"]
    r = c.post("/describe", json={"at": "base", "bogus": 1})
    assert r.status_code == 422 and "bogus" in r.json()["error"]
    r = c.get("/terrain/sample", params={"x": "abc", "y": 1})
    assert r.status_code == 422 and "error" in r.json()


def test_describe_search_nearest_sets(c):
    d = post(c, "/describe", {"at": {"x": 103.4, "y": -50.2}})
    assert 50 < d["locations"][0]["values"]["slope_2m"]["value"] < 61
    loc = post(c, "/search/locations", {"profile": "landing_pad", "limit": 3})
    assert loc["set_id"].startswith("set:") and loc["count"] >= 1
    reg = post(c, "/search/regions", {"conditions": [{"evidence": "carbonate", "min_tier": "strong_orbital"}]})
    assert reg["count"] >= 1
    n = post(c, "/search/nearest", {"from": "base", "to": "poi:landing", "metric": "straight_m"})
    assert n["results"][0]["cost"] == pytest.approx(70.7, abs=2)
    page = c.get(f"/sets/{n['set_id']}", params={"limit": 5}).json()
    assert page["count"] == 1 and page["query"]["metric"] == "straight_m"
    assert c.get("/sets/set:99").status_code == 422


def test_terrain_sample_profile_pois(c):
    s = c.get("/terrain/sample", params={"x": 0, "y": 0}).json()
    assert s["hazard_class"] == "safe" and s["slope_deg"] < 2 and s["elevation_m"] < -2400
    r = c.get("/terrain/sample", params={"x": 106, "y": -50}).json()
    assert r["hazard_class"] == "no-go"
    assert c.get("/terrain/sample", params={"x": 0, "y": -150}).json()["hazard_class"] == "unknown"
    assert c.get("/terrain/sample", params={"x": 9999, "y": 0}).status_code in (200, 422)
    p = post(c, "/terrain/profile", {"points": [{"x": 0, "y": 0}, {"x": 150, "y": 0}]})
    assert p["distance_m"] == 150 and p["max_slope_deg"] > 50 and p["ascent_m"] > 10 and p["samples"][0]["d"] == 0
    assert c.post("/terrain/profile", json={"points": [{"x": 0, "y": 0}]}).status_code == 422
    pois = c.get("/pois").json()
    assert pois["count"] == 4 and all(p["science_value"] is None for p in pois["pois"])
    near = c.get("/pois", params={"near": "0,0", "radius": 100}).json()
    assert [p["id"] for p in near["pois"]] == ["base", "landing"] and near["pois"][1]["distance_m"] == 70.7
    assert [p["id"] for p in c.get("/pois", params={"type": "science_target"}).json()["pois"]] == ["outcrop_a"]
    assert [p["id"] for p in c.get("/pois", params={"q": "east"}).json()["pois"]] == ["east_point"]


# ---- routes ------------------------------------------------------------------------------------

def test_route_detours_around_ridge(c, app):
    site = app.state.world.site
    r = post(c, "/route", {"start": {"x": 60, "y": -100}, "goal": {"x": 160, "y": -100}, "profile": "eva_walkable"})
    assert r["feasible"] and r["route_id"].startswith("route-")
    assert r["distance_m"] > 2.5 * 100                    # straight is 100 m, ridge forces a detour north
    assert max(p["y"] for p in r["points"]) > 150          # passes the ridge's north end
    assert max(dense_slopes(site, r["points"])) <= 25
    assert r["max_slope_deg"] <= 25
    assert r["points"][0]["z"] is not None and r["stops"][-1]["kind"] == "goal"
    spacing = [math.hypot(a["x"] - b["x"], a["y"] - b["y"]) for a, b in zip(r["points"][:-1], r["points"][1:])]
    assert max(spacing) <= 5.01
    assert r["consumables"]["used"]["minutes"] == pytest.approx(r["duration_min"], abs=0.11)
    assert r["assessment"]["go"] is True
    got = c.get(f"/route/{r['route_id']}").json()
    assert got["distance_m"] == r["distance_m"]
    assert c.get("/route/route-999").status_code == 422


def test_route_override_looser_than_profile_is_flagged(c):
    r = post(c, "/route", {"start": {"x": 60, "y": -100}, "goal": {"x": 102, "y": -100}, "max_slope_deg": 70})
    assert r["feasible"] and r["max_slope_deg"] > 40
    assert r["assessment"]["go"] is False and any("no-go limit" in x for x in r["assessment"]["reasons"])
    assert c.post("/route", json={"goal": "base", "max_slope_deg": 0}).status_code == 422


def test_resolve_ref_marker_unit():
    from atlas_core import resolve_ref
    site = make_site()
    site.markers["a1"] = {"x": 10.0, "y": 20.0, "label": "A"}
    m = resolve_ref(site, "marker:a1")[0]
    assert (m["x"], m["y"], m["id"], m["name"]) == (10.0, 20.0, "marker:a1", "A")
    with pytest.raises(Exception, match="Unknown marker"):
        resolve_ref(site, "marker:zz")


def test_route_duration_matches_speed_curve(c):
    r = post(c, "/route", {"start": {"x": -240, "y": -60}, "goal": {"x": 90, "y": -60}, "profile": "eva_walkable"})
    assert r["max_slope_deg"] == pytest.approx(1.0, abs=0.05)
    expected = r["distance_m"] / (1.0 * math.exp(-3.5 * math.tan(math.radians(1.0)))) / 60
    assert r["duration_min"] == pytest.approx(expected, rel=0.02)


def test_route_blocked_goal_is_infeasible_with_reason(c):
    r = post(c, "/route", {"start": "base", "goal": {"x": 106, "y": -100}})
    assert r["feasible"] is False and r["route_id"] is None
    assert "degree ground" in r["reason"] and "20" in r["reason"]
    nr = r["nearest_reachable"]
    assert nr["distance_to_goal_m"] < 20 and abs(nr["x"]) < 120
    assert r["hint"] and r["points"] == []
    hole = post(c, "/route", {"start": "base", "goal": {"x": 0, "y": -150}})
    assert hole["feasible"] is False and "no terrain data" in hole["reason"]


def test_route_blocked_via_and_unwalkable_start(c):
    r = post(c, "/route", {"start": "base", "goal": "outcrop_a", "via": [{"x": 106, "y": -100}]})
    assert r["feasible"] is False and "via point 1" in r["reason"]
    s = post(c, "/route", {"start": {"x": 106, "y": -100}, "goal": {"x": 60, "y": -100}})
    assert s["feasible"] and any("not on walkable ground" in w for w in s["warnings"])


def test_route_via_and_budget_and_markers(c, app):
    r = post(c, "/route", {"start": "base", "goal": "outcrop_a", "via": ["poi:landing"], "profile": "eva_walkable",
                           "time_budget_min": 5})
    assert r["feasible"] and [s["kind"] for s in r["stops"]] == ["via", "goal"]
    assert r["stops"][0]["x"] == 50 and r["stops"][0]["eta_min"] < r["stops"][1]["eta_min"]
    assert any("budget" in w for w in r["warnings"]) and r["assessment"]["go"] is False
    direct = post(c, "/route", {"start": "base", "goal": "outcrop_a", "profile": "eva_walkable"})
    assert r["distance_m"] > direct["distance_m"]


def test_route_no_nogo_crossing_50_random_pairs(c, app):
    site = app.state.world.site
    rng = np.random.default_rng(7)
    done = 0
    for _ in range(200):
        a, b = rng.uniform(-240, 240, 2), rng.uniform(-240, 240, 2)
        if not (R.ground_slope(site, *a) <= 20 and R.ground_slope(site, *b) <= 20):
            continue
        r = post(c, "/route", {"start": {"x": a[0], "y": a[1]}, "goal": {"x": b[0], "y": b[1]}, "profile": "rover_scout"})
        if not r["feasible"]:
            continue
        done += 1
        assert max(dense_slopes(site, r["points"])) <= 20 + 1e-6
        if done == 50:
            break
    assert done == 50


def test_science_traverse(c):
    r = post(c, "/route/science", {"start": "base", "time_budget_min": 240})
    ids = [s["id"] for s in r["stops"]]
    assert set(ids) == {"poi:outcrop_a", "poi:landing", "poi:east_point"}
    assert all(s["value_assumed"] for s in r["stops"]) and any("0.5" in n for n in r["notes"])
    t = r["time"]
    assert t["total_min"] <= 240 and t["stops_min"] == 45 and r["science_value_total"] is None
    assert "no science weighting" in r["science_weighting_note"]
    assert r["route"]["feasible"] and r["route"]["route_id"]
    assert r["route"]["consumables"]["stops_min"] == 45
    assert r["stops"][0]["arrive_min"] < r["stops"][-1]["arrive_min"]
    small = post(c, "/route/science", {"start": "base", "time_budget_min": 30, "interests": ["carbonate"]})
    assert [s["id"] for s in small["stops"]] == ["poi:outcrop_a"] and "carbonate" in small["stops"][0]["matched_by"][0]
    none = post(c, "/route/science", {"start": "base", "time_budget_min": 5})
    assert none["stops"] == [] and none["hint"]
    assert c.post("/route/science", json={"start": "base"}).status_code == 422


# ---- markers -----------------------------------------------------------------------------------

def test_markers_crud_and_refs(c):
    m1 = post(c, "/markers", {"x": 40, "y": -60, "label": "camp"})
    m2 = post(c, "/markers", {"x": -100, "y": -100, "created_by": "user"})
    assert m1["id"] == "m1" and m1["created_by"] == "agent" and m1["lat"] and m2["created_by"] == "user"
    assert c.get("/markers").json()["count"] == 2
    r = post(c, "/route", {"start": "marker:m1", "goal": "marker:m2", "profile": "eva_walkable"})
    assert r["feasible"] and r["start"]["x"] == 40 and r["goal"]["x"] == -100
    loc = post(c, "/search/locations", {"profile": "landing_pad", "limit": 2,
                                        "within": {"from": "marker:m1", "max": 200, "metric": "straight_m"}})
    assert loc["count"] >= 1
    d = post(c, "/describe", {"at": "marker:m2"})
    assert d["locations"][0]["x"] == -100 and d["locations"][0]["name"] == "m2"
    assert c.post("/route", json={"goal": "marker:zz"}).status_code == 422
    assert c.delete("/markers/m1").json()["removed"]["id"] == "m1"
    assert c.delete("/markers/m1").status_code == 422
    assert post(c, "/markers", {"at": "poi:landing", "label": "L"})["x"] == 50
    assert c.post("/markers", json={"x": 9999, "y": 0}).status_code == 422
    assert c.get("/overview").json()["markers"][0]["id"] == "m2"


# ---- search sites ------------------------------------------------------------------------------

def test_search_sites_and_targets(c):
    t = c.get("/search/targets").json()["targets"]
    carb = next(x for x in t if x["target"] == "carbonate")
    assert carb["kind"] == "evidence" and carb["mission"] == "SYNTHETIC" and carb["tiers"][-1] == "confirmed_in_situ"
    s = post(c, "/search/sites", {"target": "carbonate", "near": "base", "top_k": 3})
    site = s["sites"][0]
    assert site["best_tier_name"] == "confirmed_in_situ" and "score" not in site
    assert site["reachable"] is True and site["distance_m"] == pytest.approx(212, abs=3)
    assert site["evidence"][0]["layer"] == "carbonate" and site["evidence"][0]["mission"]
    assert -190 <= site["x"] <= -110 and site["area_m2"] == 6400
    assert post(c, "/search/sites", {"target": "carbonate", "radius_m": 100})["count"] == 0
    cf = post(c, "/search/sites", {"target": "crater_floor"})
    assert cf["sites"][0]["best_tier"] is None and cf["sites"][0]["evidence"][0]["value"] == "crater_floor"
    strong = post(c, "/search/sites", {"target": "carbonate", "min_tier": "confirmed_in_situ", "min_area_m2": 1})
    assert strong["sites"][0]["area_m2"] == 4.0
    bad = c.post("/search/sites", json={"target": "gold"})
    assert bad.status_code == 422 and "carbonate" in bad.json()["hint"]


# ---- rover -------------------------------------------------------------------------------------

def cmd(c, **body):
    return post(c, "/rover/command", body)


def test_rover_state_and_follow_route_arrives(c):
    st = c.get("/rover/state").json()
    assert (st["x"], st["y"], st["mode"], st["fsd_armed"]) == (0, 0, "idle", False)
    assert st["pitch_deg"] is not None and st["z"] < -2400
    r = post(c, "/route", {"start": "rover", "goal": "outcrop_a", "profile": "rover_scout"})
    res = cmd(c, cmd="follow_route", route_id=r["route_id"])
    assert res["stop_reason"] == "arrived"
    s = res["state"]
    assert math.hypot(s["x"] + 151, s["y"] + 151) < 3 and s["odometer_m"] == pytest.approx(r["distance_m"], rel=0.05)
    assert res["elapsed_sim_min"] == pytest.approx(r["duration_min"], rel=0.1) and s["mode"] == "idle"
    assert c.get("/overview").json()["rover"]["x"] == s["x"]
    # rover ref now resolves to the new pose
    assert post(c, "/describe", {"at": "rover"})["locations"][0]["x"] == s["x"]


def test_rover_drive_into_ridge_blocked_slope(c):
    res = cmd(c, cmd="drive", distance_m=300, heading_deg=90)
    assert res["stop_reason"] == "blocked_slope" and 90 < res["state"]["x"] < 101
    assert "no-go" in res["detail"] and res["hint"]
    assert res["state"]["status"].startswith("stopped") and c.get("/fsd/status").json()["last_hazard"]["type"] == "blocked_slope"
    # plain drive on flat ground works and reports the odometer
    cmd(c, cmd="teleport", x=-50, y=-50)
    ok = cmd(c, cmd="drive", distance_m=40, heading_deg=0)
    assert ok["stop_reason"] == "arrived" and ok["state"]["y"] == pytest.approx(-10, abs=0.01)
    assert ok["state"]["heading_deg"] == 0 and ok["distance_m"] == pytest.approx(40, abs=0.1)


def test_rover_terrain_following_pose_tilts_on_slope(c):
    cmd(c, cmd="teleport", x=-200, y=-100)
    flat = c.get("/rover/state").json()
    site = c.app.state.world.site
    x = next(x for x in np.arange(-190.0, -70.0, 1.0) if 9 < R.ground_slope(site, x, 120) < 19)   # crater wall
    cmd(c, cmd="teleport", x=x, y=120, heading_deg=90)
    tilted = c.get("/rover/state").json()
    assert abs(tilted["pitch_deg"]) > 5 and abs(flat["pitch_deg"]) < 3 and abs(tilted["roll_deg"]) < 2


def test_rover_teleport_turn_goto_stop(c):
    t = cmd(c, cmd="teleport", target="poi:landing", heading_deg=45)
    assert t["stop_reason"] == "arrived" and t["state"]["x"] == 50 and t["state"]["heading_deg"] == 45
    assert cmd(c, cmd="turn", heading_deg=270)["state"]["heading_deg"] == 270
    assert cmd(c, cmd="turn", delta_deg=90)["state"]["heading_deg"] == 0
    g = cmd(c, cmd="goto", x=80, y=-50)
    assert g["stop_reason"] == "arrived" and g["state"]["x"] == pytest.approx(80, abs=0.01)
    bad = cmd(c, cmd="goto", x=106, y=-50)
    assert bad["stop_reason"] == "blocked_slope" and bad["feasible"] is False and bad["nearest_reachable"]
    assert c.post("/rover/command", json={"cmd": "teleport", "x": 106, "y": -50}).status_code == 422
    assert c.post("/rover/command", json={"cmd": "teleport", "x": 0, "y": -150}).status_code == 422
    assert c.post("/rover/command", json={"cmd": "fly"}).status_code == 422
    stop = cmd(c, cmd="stop")
    assert stop["stop_reason"] == "stopped_by_user"


def test_follow_route_requires_rover_at_start(c):
    r = post(c, "/route", {"start": {"x": -100, "y": -100}, "goal": "outcrop_a"})
    e = c.post("/rover/command", json={"cmd": "follow_route", "route_id": r["route_id"]})
    assert e.status_code == 422 and "from the start" in e.json()["error"] and e.json()["hint"]
    e = c.post("/rover/command", json={"cmd": "follow_route", "route_id": "route-99"})
    assert e.status_code == 422


def inject_straight_route(app, a, b):
    """A route the planner would never make (straight over the ridge), as from a looser or older map."""
    w = app.state.world
    rid = w.new_route_id()
    L = math.hypot(b[0] - a[0], b[1] - a[1])
    n = int(L / 5) + 1
    pts = [{"x": a[0] + (b[0] - a[0]) * t, "y": a[1] + (b[1] - a[1]) * t, "z": 0.0} for t in np.linspace(0, 1, n + 1)]
    w.routes[rid] = {"route_id": rid, "feasible": True, "reason": None, "points": pts, "distance_m": L,
                     "goal": {"x": b[0], "y": b[1]}}
    return {"route_id": rid}


def test_fsd_blocked_then_replanned_then_arrives(c, app):
    cmd(c, cmd="teleport", x=60, y=-100)
    r = inject_straight_route(app, (60, -100), (160, -100))
    # without FSD: stops at the ridge
    res = cmd(c, cmd="follow_route", route_id=r["route_id"])
    assert res["stop_reason"] == "blocked_slope" and res["state"]["fsd_armed"] is False
    cmd(c, cmd="teleport", x=60, y=-100)
    en = cmd(c, cmd="fsd_enable", route_id=r["route_id"])
    assert en["state"]["mode"] == "fsd" and en["fsd"]["armed"] and en["fsd"]["replans_allowed"] == 1
    res = cmd(c, cmd="follow_route")                       # uses the route armed with fsd_enable
    assert res["stop_reason"] == "replanned" and res["new_route"]["replan_of"] == r["route_id"]
    st = c.get("/fsd/status").json()
    assert st["last_hazard"]["type"] == "blocked_slope" and st["replans_used"] == 1
    assert st["route_id"] == res["new_route"]["route_id"] and st["remaining_route_m"] > 100
    res2 = cmd(c, cmd="follow_route", route_id=res["new_route"]["route_id"])
    assert res2["stop_reason"] == "arrived"
    assert math.hypot(res2["state"]["x"] - 160, res2["state"]["y"] + 100) < 3
    assert c.get("/fsd/status").json()["replans_used"] == 0
    d = cmd(c, cmd="fsd_disable")
    assert d["state"]["fsd_armed"] is False and d["state"]["mode"] == "idle"
    bad = c.post("/rover/command", json={"cmd": "fsd_enable", "policy": {"nonsense": True}})
    assert bad.status_code == 422 and "nonsense" in bad.json()["error"]


def test_fsd_replan_disabled_stops(c, app):
    cmd(c, cmd="teleport", x=60, y=-100)
    cmd(c, cmd="fsd_enable", policy={"replan_on_block": False})
    r = inject_straight_route(app, (60, -100), (160, -100))
    res = cmd(c, cmd="follow_route", route_id=r["route_id"])
    assert res["stop_reason"] == "blocked_slope"


def test_rover_realtime_and_timeout(c):
    res = cmd(c, cmd="drive", distance_m=30, heading_deg=0, realtime=True, speed_factor=1000)
    assert res["stop_reason"] == "arrived" and res["state"]["y"] == pytest.approx(30, abs=0.01)
    res = cmd(c, cmd="drive", distance_m=30, heading_deg=0, realtime=True, speed_factor=1, timeout_s=0.25)
    assert res["stop_reason"] == "timeout" and res["distance_m"] < 1


# ---- view, snapshot, events ---------------------------------------------------------------------

def test_view_validation_and_result(c):
    v = post(c, "/view", {"mode": "orbit", "target": "poi:landing", "layers": {"carbonate": True, "slope_2m": False},
                          "highlights": ["poi:base", "poi:landing"]})
    assert v["target"]["x"] == 50 and v["viewers"] == 0 and "No viewer" in v["note"]
    assert [h["id"] for h in v["highlights"]] == ["poi:base", "poi:landing"]
    assert c.post("/view", json={"mode": "flying"}).status_code == 422
    assert c.post("/view", json={}).status_code == 422
    assert c.post("/view", json={"layers": {"bogus": True}}).status_code == 422


def test_snapshot_without_viewer_is_504(c):
    r = c.post("/view/snapshot", json={})
    assert r.status_code == 504 and "No viewer" in r.json()["error"]


def test_snapshot_roundtrip_and_timeout():
    app = create_app(make_site(), synthetic=True, snapshot_timeout_s=0.4)
    with TestClient(app) as c, c.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "rover.state"
        out = {}
        t = threading.Thread(target=lambda: out.update(r=c.post("/view/snapshot", json={"direction": "forward"})))
        t.start()
        msg = ws.receive_json()
        assert msg["type"] == "view.snapshot_request" and msg["payload"]["direction"] == "forward"
        ws.send_json({"type": "view.snapshot", "payload": {"request_id": msg["payload"]["request_id"],
                                                          "image_base64": "QUJD"}})
        t.join(5)
        assert out["r"].status_code == 200 and out["r"].json()["image_base64"] == "QUJD"
        # a viewer that never answers -> 504 after the timeout
        t = threading.Thread(target=lambda: out.update(r=c.post("/view/snapshot", json={})))
        t.start()
        assert ws.receive_json()["type"] == "view.snapshot_request"
        t.join(5)
        assert out["r"].status_code == 504 and "did not send" in out["r"].json()["error"]


def test_websocket_events(app):
    with TestClient(app) as c, c.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "rover.state"
        post(c, "/events/agent", {"tool": "plan_route", "args_summary": "base -> outcrop_a", "result_summary": "218 m"})
        ev = ws.receive_json()
        assert ev["type"] == "agent.event" and ev["payload"]["tool"] == "plan_route" and ev["ts"] > 0
        post(c, "/markers", {"x": 1, "y": 2})
        assert ws.receive_json()["type"] == "marker.add"
        c.delete("/markers/m1")
        r = post(c, "/route", {"start": "rover", "goal": "outcrop_a"})
        types = [ws.receive_json()["type"] for _ in range(2)]
        assert types == ["marker.remove", "route.set"]
        res = cmd(c, cmd="follow_route", route_id=r["route_id"])
        assert res["stop_reason"] == "arrived"
        got = []
        for _ in range(500):
            m = ws.receive_json()
            got.append(m["type"])
            if m["type"] == "rover.state" and m["payload"]["status"] == "arrived":
                break
        assert got.count("rover.state") >= 10
        post(c, "/view", {"target": "base", "layers": {"carbonate": True}, "highlights": ["poi:base"]})
        assert [ws.receive_json()["type"] for _ in range(3)] == ["view.camera", "view.layer", "view.highlight"]
        post(c, "/search/sites", {"target": "carbonate"})
        assert ws.receive_json()["type"] == "search.results"
        # viewer -> server
        ws.send_json({"type": "marker.click", "payload": {"x": 10, "y": 20}})
        m = ws.receive_json()
        assert m["type"] == "marker.add" and m["payload"]["created_by"] == "user" and m["payload"]["x"] == 10
        ws.send_json({"type": "rover.pose", "payload": {"x": -40, "y": 30, "heading_deg": 90}})
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"
        st = c.get("/rover/state").json()
        assert (st["x"], st["y"], st["mode"]) == (-40, 30, "manual")
        ws.send_json({"type": "marker.click", "payload": {"x": 99999, "y": 0}})
        assert ws.receive_json()["type"] == "error"


def test_validation_endpoint(app):
    c = TestClient(app)
    v = c.get("/validation").json()
    assert v["available"] is False and "traverse" in v["reason"]
    site = app.state.world.site
    pts = [site.xy_to_latlon(x, -60)[::-1] for x in (-150, -100, -50, 0, 50, 90, 106, 130)]
    wp = lambda sol, ll: {"type": "Feature", "properties": {"sol": sol}, "geometry": {"type": "Point", "coordinates": list(ll)}}
    tr = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": {"type": "LineString", "coordinates": [list(p) for p in pts]}},
        wp(1, pts[0]), wp(2, pts[2]), wp(3, pts[4]), wp(4, pts[5])]}
    app2 = create_app(make_site(), synthetic=True, traverse=tr)
    v = TestClient(app2).get("/validation").json()
    assert v["available"] and 0.5 < v["hazard_agreement"]["share_safe_or_caution"] < 1.0
    assert v["hazard_agreement"]["samples"]["no-go"] > 0 and len(v["route_realism"]) >= 1
    assert v["route_realism"][0]["planned_feasible"] is True


def test_stop_cancels_running_realtime_drive(app):
    import anyio
    import httpx

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as ac:
            out = {}

            async def drive():
                out["d"] = (await ac.post("/rover/command", json={"cmd": "drive", "distance_m": 200, "heading_deg": 0,
                                                                  "realtime": True, "speed_factor": 100})).json()
            async with anyio.create_task_group() as tg:
                tg.start_soon(drive)
                await anyio.sleep(0.5)
                st = (await ac.get("/rover/state")).json()
                assert st["mode"] == "auto" and st["status"] == "driving" and st["y"] > 0
                stop = (await ac.post("/rover/command", json={"cmd": "stop"})).json()
                assert stop["stop_reason"] == "stopped_by_user"
            return out["d"]
    d = anyio.run(go)
    assert d["stop_reason"] == "stopped_by_user" and 0 < d["state"]["y"] < 200 and d["state"]["mode"] == "idle"

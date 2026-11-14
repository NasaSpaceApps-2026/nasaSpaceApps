import math
import time

import numpy as np
import pytest

FAST = 3000  # sim speed factor so drives finish in seconds


def test_health_and_site(client):
    assert client.get("/health").json()["ok"]
    site = client.get("/site").json()
    assert "astronaut_walk" in site["profiles"]
    assert "carbonate" in site["search_targets"]


def test_sample_and_errors(client):
    s = client.get("/terrain/sample", params={"x": 0, "y": 0}).json()
    assert s["hazard"] in ("safe", "caution", "no-go")
    assert "min_carbonate" in s["layers"]
    r = client.get("/terrain/sample", params={"x": 9000, "y": 0})
    assert r.status_code == 422 and "outside" in r.json()["error"]
    r = client.get("/terrain/sample", params={"at": "poi:nope"})
    assert r.status_code == 422 and "Unknown place" in r.json()["error"]


def test_profile(client):
    p = client.post("/terrain/profile", json={"points": ["poi:base", "poi:north"], "spacing_m": 5}).json()
    assert p["distance_m"] == 400 and p["max_slope_deg"] > 15


def test_flat_duration_matches_speed(client):
    r = client.post("/route", json={"start": {"x": -400, "y": -400}, "goal": {"x": -100, "y": -400}}).json()
    assert abs(r["distance_m"] - 300) / 300 < 0.02
    expected_min = r["distance_m"] / 0.9 / 60
    assert abs(r["duration_min"] - expected_min) / expected_min < 0.02


def test_route_fast(client):
    t = time.time()
    client.post("/route", json={"start": {"x": -480, "y": -480}, "goal": {"x": 480, "y": 480}})
    assert time.time() - t < 1.0


def test_no_route_crosses_nogo(client, data_dir):
    from ares_api.terrain import Terrain
    t = Terrain(data_dir, data_dir / "none")
    rng = np.random.default_rng(0)
    planned = 0
    for _ in range(50):
        a, b = rng.uniform(-500, 500, 2), rng.uniform(-500, 500, 2)
        r = client.post("/route", json={"start": {"x": a[0], "y": a[1]}, "goal": {"x": b[0], "y": b[1]},
                                        "profile": "rover_scout"})
        if r.status_code != 200:
            continue
        planned += 1
        pts = r.json()["points"][1:-1]  # endpoints may sit in the safety buffer
        xs = np.array([p["x"] for p in pts])
        ys = np.array([p["y"] for p in pts])
        if len(xs):
            assert t.slope(xs, ys).max() <= 20.0, r.json()["route_id"]
    assert planned >= 30


def test_walker_crosses_ridge_rover_detours(client):
    walk = client.post("/route", json={"start": "poi:base", "goal": "poi:north"}).json()
    rover = client.post("/route", json={"start": "poi:base", "goal": "poi:north", "profile": "rover_scout"}).json()
    assert walk["max_slope_deg"] > 20 and walk["distance_m"] < 600
    assert rover["max_slope_deg"] <= 20 and rover["distance_m"] > 1000
    assert walk["direct_line"]["max_slope_deg"] > 20


def test_infeasible_goal_message(client):
    r = client.post("/route", json={"start": "poi:base", "goal": {"x": -300, "y": 300}})
    assert r.status_code == 422
    assert "Nearest reachable point" in r.json()["error"] or "slope limit" in r.json()["error"]


def test_assess(client):
    r = client.post("/route", json={"start": "poi:base", "goal": "poi:wpB", "time_budget_min": 1}).json()
    a = client.get(f"/route/{r['route_id']}/assess").json()
    assert a["verdict"] == "NO-GO" and any("budget" in x for x in a["reasons"])


def test_markers_route(client):
    m1 = client.post("/markers", json={"x": -400, "y": -100}).json()
    m2 = client.post("/markers", json={"x": -100, "y": -100, "label": "Second"}).json()
    assert len(client.get("/markers").json()["markers"]) == 2
    r = client.post("/route", json={"start": f"marker:{m1['id']}", "goal": m2["id"]}).json()
    assert r["start"] == m1["label"] and r["goal"] == "Second"
    assert client.delete(f"/markers/{m1['id']}").json()["removed"] == m1["id"]


def test_search_ranks_planted_hotspot_first(client):
    res = client.post("/search/sites", json={"target": "carbonates", "near": "poi:base"}).json()
    top = res["sites"][0]
    assert math.hypot(top["x"] + 200, top["y"] + 300) < 20
    assert res["sites"][0]["evidence"][0]["instrument"] == "CRISM"
    assert top["reachable"] is True
    r = client.post("/search/sites", json={"target": "unobtainium"})
    assert r.status_code == 422 and "Available targets" in r.json()["error"]


def test_follow_route_arrives(client):
    r = client.post("/route", json={"start": "rover", "goal": "poi:wpB", "profile": "rover_scout"}).json()
    res = client.post("/rover/command", json={"cmd": "follow_route", "route_id": r["route_id"], "speed_factor": FAST}).json()
    assert res["stop_reason"] == "arrived"
    s = res["state"]
    assert math.hypot(s["x"] + 100, s["y"] + 400) < 3


def test_drive_blocked_by_slope(client):
    client.post("/rover/command", json={"cmd": "teleport", "target": {"x": -200, "y": -100}})
    res = client.post("/rover/command", json={"cmd": "drive", "distance_m": 200, "heading_deg": 0,
                                               "speed_factor": FAST}).json()
    assert res["stop_reason"] == "blocked_slope"
    assert res["state"]["y"] < 0


def test_fsd_replans_around_ridge(client):
    walk = client.post("/route", json={"start": "poi:base", "goal": "poi:north"}).json()
    client.post("/rover/command", json={"cmd": "fsd_enable"})
    res = client.post("/rover/command", json={"cmd": "follow_route", "route_id": walk["route_id"],
                                              "speed_factor": FAST * 3}).json()
    assert res["replans"] == 1, res
    assert res["stop_reason"] == "arrived", res
    assert client.get("/fsd/status").json()["last_hazard"]["reason"] == "blocked_slope"


def test_science_traverse(client):
    r = client.post("/route/science", json={"start": "poi:base", "time_budget_min": 240,
                                            "interests": ["carbonate"]}).json()
    assert r["science_stops"] and r["duration_min"] + r["stop_minutes"] <= 240


def test_validation(client):
    v = client.get("/validation").json()
    assert v["available"] and v["hazard_agreement"] == 1.0
    assert v["route_realism"][0]["ratio"] == pytest.approx(1.0, abs=0.05)


def test_view_and_ws(client):
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "rover.state"
        client.post("/view", json={"mode": "chase", "target": "poi:north", "highlights": ["north"]})
        kinds = {ws.receive_json()["type"] for _ in range(2)}
        assert kinds == {"view.camera", "view.highlight"}
        ws.send_json({"type": "marker.click", "x": 10, "y": 20})
        assert ws.receive_json()["type"] == "marker.add"
        ws.send_json({"type": "rover.pose", "x": 5, "y": 6, "heading_deg": 90})
        time.sleep(0.1)
        assert client.get("/rover/state").json()["mode"] == "manual"

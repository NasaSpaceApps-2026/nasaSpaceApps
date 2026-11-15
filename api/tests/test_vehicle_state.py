import math

from fastapi.testclient import TestClient

FAST = 3000


def test_vehicle_switch_and_list(client):
    v = client.get("/rover/vehicles").json()
    assert v["current"] == "rover_scout" and "crew_buggy" in v["vehicles"]
    r = client.post("/rover/vehicle", json={"profile": "crew_buggy"}).json()
    assert r["vehicle"] == "crew_buggy" and r["state"]["vehicle"] == "crew_buggy"
    bad = client.post("/rover/vehicle", json={"profile": "hovercraft"})
    assert bad.status_code == 422 and "Unknown profile" in bad.json()["error"]


def test_buggy_auto_drive_uses_force_model(client):
    """Buggy on flat ground cruises at its cap; mission time matches distance / 24 m/s."""
    client.post("/rover/vehicle", json={"profile": "crew_buggy"})
    client.post("/rover/command", json={"cmd": "teleport", "target": {"x": -450, "y": -450}, "heading_deg": 90})
    res = client.post("/rover/command", json={"cmd": "drive", "distance_m": 200, "heading_deg": 90,
                                              "speed_factor": 4}).json()
    assert res["stop_reason"] == "arrived", res
    mission_s = res["state"]["mission_time_min"] * 60
    assert abs(mission_s - 200 / 24) < 1.0, mission_s


def test_buggy_stalls_where_grip_runs_out(client, data_dir):
    """Driving straight up the 22 deg ridge flank: the rear-drive buggy (grip ~20 deg) stalls with a physics note."""
    client.post("/rover/vehicle", json={"profile": "crew_buggy"})
    client.post("/rover/command", json={"cmd": "teleport", "target": {"x": -200, "y": -60}, "heading_deg": 0})
    res = client.post("/rover/command", json={"cmd": "drive", "distance_m": 100, "heading_deg": 0,
                                              "speed_factor": 8}).json()
    assert res["stop_reason"] == "blocked_slope", res
    assert "grip" in res["message"] or "limit" in res["message"]
    assert res["state"]["y"] < 0


def test_markers_and_routes_persist(data_dir, tmp_path):
    from ares_api.main import create_app
    state = tmp_path / "state.json"
    with TestClient(create_app(data_dir, extra_layer_dir=tmp_path / "none", state_file=state)) as c:
        m = c.post("/markers", json={"x": -400, "y": -100, "label": "Keep me"}).json()
        r = c.post("/route", json={"start": "poi:base", "goal": f"marker:{m['id']}"}).json()
    with TestClient(create_app(data_dir, extra_layer_dir=tmp_path / "none", state_file=state)) as c:
        assert [x["label"] for x in c.get("/markers").json()["markers"]] == ["Keep me"]
        assert c.get(f"/route/{r['route_id']}").json()["route_id"] == r["route_id"]
        m2 = c.post("/markers", json={"x": 0, "y": 0}).json()
        assert m2["id"] != m["id"]  # ids continue after a restart


def test_sim_keeps_ticking_during_planning(client):
    """Heavy endpoints run in a worker thread, so the 10 Hz rover loop keeps going."""
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        client.post("/rover/command", json={"cmd": "drive", "distance_m": 300, "heading_deg": 90,
                                            "wait": False, "speed_factor": 50})
        client.post("/route", json={"start": {"x": -480, "y": -480}, "goal": {"x": 480, "y": 480}})
        states = [m for m in (ws.receive_json() for _ in range(5)) if m["type"] == "rover.state"]
        assert states, "rover.state events kept flowing"

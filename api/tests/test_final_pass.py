"""Final-pass autopilot fixes: filled-cell policy, route default profile, vehicle slopes, takeover."""
import time

FAST = 3000
A, B = {"x": -100, "y": -300}, {"x": 150, "y": -300}  # the straight line crosses the filled strip


def _drive(c, policy=None, dist=200):
    c.post("/rover/vehicle", json={"profile": "rover_scout"})
    c.post("/rover/command", json={"cmd": "teleport", "target": A, "heading_deg": 90})
    c.post("/rover/command", json={"cmd": "fsd_enable", "policy": policy or {}})
    return c.post("/rover/command", json={"cmd": "drive", "distance_m": dist, "heading_deg": 90,
                                          "speed_factor": FAST}).json()


def test_route_warns_about_filled_cells_and_policy_block_avoids(filled_client):
    c = filled_client
    body = {"start": A, "goal": B, "profile": "rover_scout"}
    r = c.post("/route", json=body).json()
    assert r["filled_cells"] > 0 and r["filled_length_m"] > 0 and r["filled_policy"] == "caution"
    assert any("interpolated" in w for w in r["warnings"])
    b = c.post("/route", json={**body, "filled": "block"}).json()
    assert b["filled_cells"] == 0 and b["distance_m"] > r["distance_m"]
    assert not any("interpolated" in w for w in b["warnings"])
    a = c.post("/route", json={**body, "filled": "allow"}).json()
    assert a["filled_cells"] > 0 and not any("interpolated" in w for w in a["warnings"])


def test_fsd_caution_crosses_filled_and_reports(filled_client):
    res = _drive(filled_client)
    assert res["stop_reason"] == "arrived", res
    assert res["filled_crossed_m"] > 20
    assert any("interpolated" in e for e in res["events"])


def test_fsd_block_stops_on_filled(filled_client):
    res = _drive(filled_client, {"filled": "block", "replan": False})
    assert res["stop_reason"] == "blocked_hazard", res
    assert res["state"]["x"] < 1


def test_fsd_allow_is_silent(filled_client):
    res = _drive(filled_client, {"filled": "allow"})
    assert res["stop_reason"] == "arrived" and not res["events"]


def test_bad_filled_policy_rejected(filled_client):
    r = filled_client.post("/rover/command", json={"cmd": "fsd_enable", "policy": {"filled": "yolo"}})
    assert r.status_code == 422 and "filled" in r.json()["error"]


def test_route_defaults_to_active_vehicle(client):
    assert client.post("/route", json={"start": "poi:base", "goal": "poi:wpB"}).json()["profile"] == "rover_scout"
    client.post("/rover/vehicle", json={"profile": "crew_buggy"})
    assert client.post("/route", json={"start": "poi:base", "goal": "poi:wpB"}).json()["profile"] == "crew_buggy"
    r = client.post("/route", json={"start": "poi:base", "goal": "poi:wpB", "profile": "astronaut_walk"}).json()
    assert r["profile"] == "astronaut_walk"


def test_vehicles_and_state_carry_caution_slope(client):
    v = client.get("/rover/vehicles").json()["vehicles"]
    assert v["crew_buggy"]["caution_slope_deg"] == 15 and v["crew_buggy"]["nogo_slope_deg"] == 25
    assert all("caution_slope_deg" in x for x in v.values())
    s = client.get("/rover/state").json()
    assert s["caution_slope_deg"] == 15 and s["nogo_slope_deg"] == 20  # rover_scout
    assert client.get("/fsd/status").json()["caution_slope_deg"] == 15


def _start_drive(c):
    c.post("/rover/command", json={"cmd": "teleport", "target": A, "heading_deg": 90})
    # slow drive (real time) so it is still running while we send poses
    r = c.post("/rover/command", json={"cmd": "drive", "distance_m": 200, "heading_deg": 90, "wait": False,
                                       "speed_factor": 100}).json()
    assert r["started"]


def test_periodic_pose_does_not_cancel_auto_drive(client):
    with client.websocket_connect("/ws") as ws:
        _start_drive(client)
        for _ in range(5):
            ws.send_json({"type": "rover.pose", "x": A["x"], "y": A["y"], "heading_deg": 90})
            time.sleep(0.1)
        s = client.get("/rover/state").json()
        assert s["mode"] == "auto" and s["status"] == "drive"
        assert s["x"] > A["x"]  # not snapped back
        ws.send_json({"type": "rover.pose", "x": -50, "y": -250, "heading_deg": 0, "takeover": True})
        time.sleep(0.2)
        s = client.get("/rover/state").json()
        assert s["mode"] == "manual" and s["x"] == -50
        client.post("/rover/command", json={"cmd": "stop"})


def test_pose_without_command_still_adopted(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "rover.pose", "x": 5, "y": 6, "heading_deg": 90})
        time.sleep(0.1)
        assert client.get("/rover/state").json()["mode"] == "manual"


# --- stale viewer pose after a drive; planet view -------------------------------------------------
def _finish_drive(c):
    c.post("/rover/vehicle", json={"profile": "rover_scout"})
    c.post("/rover/command", json={"cmd": "teleport", "target": A, "heading_deg": 90})
    r = c.post("/rover/command", json={"cmd": "drive", "distance_m": 100, "heading_deg": 90, "speed_factor": FAST}).json()
    assert r["stop_reason"] == "arrived"
    return c.get("/rover/state").json()


def test_stale_pose_after_drive_ignored_and_ack_resumes_manual(client):
    end = _finish_drive(client)
    with client.websocket_connect("/ws") as ws:
        for _ in range(5):  # the browser keeps reporting its old manual pose
            ws.send_json({"type": "rover.pose", "x": A["x"], "y": A["y"], "heading_deg": 0})
            time.sleep(0.05)
        s = client.get("/rover/state").json()
        assert (s["x"], s["y"], s["mode"]) == (end["x"], end["y"], "idle")
        ws.send_json({"type": "rover.pose", "x": end["x"] + 1, "y": end["y"], "heading_deg": 90})  # acknowledged
        time.sleep(0.1)
        assert client.get("/rover/state").json()["mode"] == "manual"
        ws.send_json({"type": "rover.pose", "x": end["x"] + 30, "y": end["y"], "heading_deg": 90})  # driving on
        time.sleep(0.1)
        assert client.get("/rover/state").json()["x"] == end["x"] + 30


def test_takeover_pose_applies_after_drive(client):
    end = _finish_drive(client)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "rover.pose", "x": A["x"], "y": A["y"], "heading_deg": 0, "takeover": True})
        time.sleep(0.1)
        s = client.get("/rover/state").json()
        assert s["mode"] == "manual" and s["x"] == A["x"] != end["x"]


def test_moving_far_pose_is_manual_driving_not_stale(client):
    end = _finish_drive(client)
    with client.websocket_connect("/ws") as ws:
        for dx in (0, 5, 10):  # a far pose that keeps changing is real driving
            ws.send_json({"type": "rover.pose", "x": A["x"] - 50 - dx, "y": A["y"], "heading_deg": 0})
            time.sleep(0.05)
        time.sleep(0.1)
        s = client.get("/rover/state").json()
        assert s["mode"] == "manual" and s["x"] == A["x"] - 60


def test_view_planet_relayed(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # initial rover.state
        r = client.post("/view", json={"mode": "planet", "lat": 18.4, "lon": 77.5, "height_m": 2.5e6, "label": "Jezero"})
        assert r.status_code == 200 and r.json()["camera"]["payload"]["lat"] == 18.4
        for _ in range(5):
            m = ws.receive_json()
            if m["type"] == "view.camera":
                break
        want = {"mode": "planet", "lat": 18.4, "lon": 77.5, "height_m": 2.5e6, "label": "Jezero"}
        assert m["payload"] == want and all(m[k] == v for k, v in want.items())
    assert client.get("/view").json()["mode"] == "planet"
    assert client.post("/view", json={"mode": "planet", "lat": 18.4}).status_code == 422
    assert client.post("/view", json={"mode": "planet", "lat": 95, "lon": 1}).status_code == 422
    assert client.post("/view", json={"mode": "orbit", "target": "rover"}).json()["camera"]["mode"] == "orbit"

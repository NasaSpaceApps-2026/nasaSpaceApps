"""Write contracts/api.openapi.yaml and one canned JSON response per endpoint in
contracts/fixtures/api/, generated from the real API running on the fixture site.

Run: api/.venv/bin/python api/scripts/dump_contract.py
"""
import json
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "api"))

from fastapi.testclient import TestClient  # noqa: E402

from ares_api.main import create_app  # noqa: E402

OUT = REPO / "contracts" / "fixtures" / "api"


def save(name: str, req: dict | None, resp):
    body = resp.json()
    (OUT / f"{name}.json").write_text(json.dumps({"request": req, "status": resp.status_code, "response": body},
                                                 indent=1))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    app = create_app(state_file=None)
    (REPO / "contracts" / "api.openapi.yaml").write_text(yaml.safe_dump(app.openapi(), sort_keys=False))
    with TestClient(app) as c:
        calls = [
            ("health", "GET", "/health", None),
            ("site", "GET", "/site", None),
            ("terrain_sample", "GET", "/terrain/sample?at=poi:floor", None),
            ("terrain_profile", "POST", "/terrain/profile", {"points": ["poi:base", "poi:sample1"], "spacing_m": 20}),
            ("pois", "GET", "/pois?type=science_target", None),
            ("pois_near", "GET", "/pois?near=poi:base&radius=300", None),
            ("markers_add", "POST", "/markers", {"x": -300, "y": -150, "label": "My marker"}),
            ("markers_add_2", "POST", "/markers", {"x": 0, "y": -250}),
            ("markers_list", "GET", "/markers", None),
            ("route", "POST", "/route", {"start": "poi:base", "goal": "poi:sample1"}),
            ("route_markers", "POST", "/route", {"start": "marker:m1", "goal": "marker:m2", "max_slope_deg": 15}),
            ("route_infeasible", "POST", "/route", {"start": "poi:base", "goal": "poi:scarp_top"}),
            ("route_assess", "GET", "/route/r1/assess", None),
            ("route_science", "POST", "/route/science", {"start": "poi:base", "time_budget_min": 240,
                                                         "interests": ["carbonate"]}),
            ("search_targets", "GET", "/search/targets", None),
            ("search_sites", "POST", "/search/sites", {"target": "carbonate", "near": "poi:base", "top_k": 3}),
            ("search_unknown", "POST", "/search/sites", {"target": "gold"}),
            ("rover_vehicles", "GET", "/rover/vehicles", None),
            ("rover_vehicle_scout", "POST", "/rover/vehicle", {"profile": "rover_scout"}),
            ("rover_state", "GET", "/rover/state", None),
            ("rover_goto", "POST", "/rover/command", {"cmd": "goto", "target": "poi:sample1", "speed_factor": 3000}),
            ("rover_drive_blocked", "POST", "/rover/command", {"cmd": "drive", "distance_m": 300, "heading_deg": 90,
                                                              "speed_factor": 3000}),
            ("rover_teleport", "POST", "/rover/command", {"cmd": "teleport", "target": "poi:rim", "heading_deg": 180}),
            ("fsd_enable", "POST", "/rover/command", {"cmd": "fsd_enable", "policy": {"max_slope_deg": 18}}),
            ("fsd_status", "GET", "/fsd/status", None),
            ("view", "POST", "/view", {"mode": "orbit", "target": "poi:floor", "distance_m": 400,
                                       "layers": [{"id": "hazard", "visible": True, "opacity": 0.6}],
                                       "highlights": ["floor"]}),
            ("bubble_status", "GET", "/bubble/status", None),
            ("validation", "GET", "/validation", None),
            ("agent_event", "POST", "/agent/event", {"tool": "plan_route", "args_summary": "base -> sample1",
                                                     "result_summary": "490 m, 10 min", "status": "ok"}),
        ]
        for name, method, path, body in calls:
            resp = c.request(method, path, json=body)
            save(name, {"method": method, "path": path, "body": body}, resp)
            print(f"{resp.status_code} {name}")


if __name__ == "__main__":
    main()

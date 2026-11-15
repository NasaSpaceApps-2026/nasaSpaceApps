"""Vehicle profiles for the stand-in rover: the crewed buggy (and other vehicles) from Yousef's api/profiles.yaml.

Our site's profiles.yaml (Caden's pipeline) only has `rover_scout` and `eva_walkable`. Yousef's API also knows
`crew_buggy` and `crew_rover`. So that the stand-in drives the buggy like the real API does, any vehicle that the
site lacks is added here from `api/profiles.yaml` (read, never edited; override the path with ATLAS_VEHICLE_PROFILES).
No limit is typed in this repo: speed and slope values come from that file and keep its `source` string, prefixed
"copied from api/profiles.yaml". The stand-in uses the same travel keys as every other profile (flat speed, caution
and no-go slope, EVA limit, reserve), so `/site` `limits`, `/route` and `/rover/command` honour them.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "api" / "profiles.yaml"
VEHICLES = ("crew_buggy", "crew_rover")


def add_vehicle_profiles(site) -> list[str]:
    """Add missing vehicle profiles to `site.profiles`; returns the names added (empty if the file is absent)."""
    path = Path(os.environ.get("ATLAS_VEHICLE_PROFILES") or DEFAULT_PATH)
    try:
        cfg = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return []
    added = []
    for name in VEHICLES:
        p = (cfg.get("profiles") or {}).get(name)
        if not p or name in site.profiles:
            continue
        src = f"copied from api/profiles.yaml ({p.get('source', 'assumed')})"
        travel = {"flat_speed_mps": {"value": p["flat_speed_mps"], "source": src},
                  "caution_slope_deg": {"value": p["caution_slope_deg"], "source": src},
                  "max_slope_deg": {"value": p["nogo_slope_deg"], "source": src},
                  "speed_curve": "tobler_placeholder"}
        for k_src, k_dst in (("eva_limit_min", "eva_limit_min"), ("reserve_frac", "reserve_fraction")):
            if k_src in p:
                travel[k_dst] = {"value": p[k_src], "source": src}
        site.profiles[name] = {"description": p.get("title", name), "vehicle": True, "travel": travel}
        added.append(name)
    return added

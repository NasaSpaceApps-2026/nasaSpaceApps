"""Paths and settings, all overridable by environment variables."""
import os
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
API_DIR = REPO / "api"

# Folder holding manifest.json, analysis/, data/, tiles/ (fixtures or data/build).
DATA_DIR = Path(os.environ.get("ARES_DATA_DIR", REPO / "contracts" / "fixtures"))
# Extra layer_*.tif rasters on the same grid, e.g. synthetic minerals until Stream A ships real ones.
EXTRA_LAYER_DIR = Path(os.environ.get("ARES_EXTRA_LAYER_DIR", API_DIR / "fixtures" / "analysis"))
PROFILES_PATH = Path(os.environ.get("ARES_PROFILES", API_DIR / "profiles.yaml"))
TARGETS_PATH = Path(os.environ.get("ARES_TARGETS", API_DIR / "targets.yaml"))

TICK_HZ = float(os.environ.get("ARES_TICK_HZ", "10"))
PLANNING_RES_M = float(os.environ.get("ARES_PLANNING_RES_M", "4"))
SNAPSHOT_TIMEOUT_S = 5.0


def load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)

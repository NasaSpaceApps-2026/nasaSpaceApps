"""Small helpers for devapi: JSON-safe conversion of atlas_core results.

Architecture: used by every route and the WS hub so numpy scalars, NaN and inf never reach the wire.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np


def clean(o: Any) -> Any:
    """JSON-safe: numpy -> python, NaN/inf -> None, tuples -> lists."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, np.generic):
        o = o.item()
    if isinstance(o, np.ndarray):
        return clean(o.tolist())
    if isinstance(o, float) and not math.isfinite(o):
        return None
    return o

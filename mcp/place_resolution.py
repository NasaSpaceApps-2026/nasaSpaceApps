"""Natural-language place resolution for tool arguments.

Every location argument of the tools in LOC_ARGS may be a phrase ("the crater", "seitah", "the hill", "landing site")
instead of a reference. `resolve_arguments` replaces such phrases with a location reference the backends understand
(`poi:<id>` or {x, y}) using the gazetteer on the query API (devapi `GET /places/resolve`, atlas_core/places.py), and
returns plain-language notes that say what was chosen and why. Structured references (poi:, marker:, set:, rover,
base, "x,y", {x, y}, {lat, lon}) pass through untouched. If the gazetteer is not available the arguments pass
through unchanged, so nothing breaks against an older backend.
"""
from __future__ import annotations

import re
from contextvars import ContextVar
from typing import Any, Awaitable, Callable

# tool -> arguments that hold ONE location (str) ...
LOC_ARGS: dict[str, tuple[str, ...]] = {
    "describe_location": ("at",), "search_pois": ("near",), "find_sites": ("near",), "plan_route": ("goal", "start"),
    "plan_science_traverse": ("start",), "goto_location": ("target",), "teleport_rover": ("target",),
    "set_view": ("target",), "fly_camera": ("target",), "place_marker": ("at",), "drive_to": ("target", "start"),
    "viewshed": ("at",), "line_of_sight": ("a", "b"), "find_vantage_points": ("target",), "sun_position": ("at",),
    "illumination_at": ("at",), "find_lit_windows": ("target",), "eva_envelope": ("base",),
}
# ... and arguments that hold a list of locations
LOC_LIST_ARGS: dict[str, tuple[str, ...]] = {
    "plan_route": ("via",), "get_elevation_profile": ("points",), "describe_location": ("at",),
}
STRUCTURED = re.compile(r"^(poi:|marker:|set:)|^(rover|base|here|me)$|^-?\d+(\.\d+)?\s*,\s*-?\d+(\.\d+)?$|^\d+(\.\d+)?$", re.I)
GENERIC_KINDS = ("crater", "rim", "highest", "flat")

# the places chosen during the current tool call, for the tools that want a label ("Driving to the crater ...")
current: ContextVar[list | None] = ContextVar("resolved_places", default=None)

Fetch = Callable[[str], Awaitable[dict | None]]


def needs_resolving(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip()) and not STRUCTURED.match(v.strip()) and v.strip()[:1] not in "{["


def _ref_for(res: dict) -> Any:
    ch = res["chosen"]
    if ch.get("kind") in ("poi", "alias") or (ch.get("kind") == "named" and str(ch.get("ref", "")).startswith("poi:")):
        return ch["ref"]
    if str(ch.get("ref", "")).startswith("poi:"):
        return ch["ref"]
    return {"x": ch["x"], "y": ch["y"]}


async def resolve_arguments(tname: str, args: dict, fetch: Fetch) -> tuple[dict, list[dict], dict | None]:
    """-> (new args, resolutions, failure). failure is the unresolved resolution dict (candidates inside) or None.
    Each resolution: {arg, query, chosen, explanation, kind, candidates, ref}."""
    names = LOC_ARGS.get(tname, ())
    lists = LOC_LIST_ARGS.get(tname, ())
    if not names and not lists:
        return args, [], None
    out = dict(args)
    done: list[dict] = []

    async def one(arg: str, text: str):
        res = await fetch(text)
        if not res:
            return text, None
        if not res.get("resolved"):
            return text, ("fail", res)
        if res.get("outside_site"):
            ch = res["chosen"]
            if arg == "start":       # "from Jezero crater": the crater is bigger than the drivable site -> start at the rover
                done.append({"arg": arg, "query": text, "kind": "global", "chosen": ch, "ref": "rover", "candidates": [],
                             "explanation": f"{ch['name']} is bigger than the area the rover can drive in, so I'm starting "
                                            "from where the rover is."})
                return "rover", None
            if tname in ("set_view", "fly_camera") and arg == "target":
                done.append({"arg": arg, "query": text, "kind": "global", "chosen": ch, "candidates": [],
                             "ref": {"lat": ch["lat"], "lon": ch["lon"]}, "explanation": res.get("explanation")})
                return {"lat": ch["lat"], "lon": ch["lon"] if ch["lon"] <= 180 else ch["lon"] - 360}, None
            return text, ("fail", res)
        ref = _ref_for(res)
        done.append({"arg": arg, "query": text, "kind": res.get("kind"), "chosen": res["chosen"],
                     "explanation": res.get("explanation"), "candidates": res.get("candidates") or [], "ref": ref,
                     "radius_m": res.get("radius_m"), "ambiguous": bool(res.get("ambiguous"))})
        return ref, None

    for a in names:
        v = out.get(a)
        if needs_resolving(v):
            new, fail = await one(a, v.strip())
            if fail:
                return args, done, fail[1]
            out[a] = new
    for a in lists:
        v = out.get(a)
        if isinstance(v, list):
            items = []
            for it in v:
                if needs_resolving(it):
                    new, fail = await one(a, it.strip())
                    if fail:
                        return args, done, fail[1]
                    items.append(new)
                else:
                    items.append(it)
            out[a] = items
        elif a in names:
            pass
    if tname in ("set_view", "fly_camera") and any(d["kind"] == "global" and d["arg"] == "target" for d in done):
        out["mode"] = "globe"
    # drive_to "the hill / high ground": use the highest-reachable-point search instead of a single cell
    if tname == "drive_to":
        for r in done:
            if r["arg"] == "target" and r["kind"] == "highest" and out.get("highest_within_m") is None:
                out["highest_within_m"] = float(r.get("radius_m") or 1000.0)
                out["target"] = None
    return out, done, None


def notes_text(done: list[dict]) -> str:
    return "\n".join(f"Place used for \"{d['query']}\": {d['explanation']}" for d in done if d.get("explanation"))

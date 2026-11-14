"""Text formatting for Ares Atlas tool results (compact, key numbers first, ~300 tokens).

Pure functions over the dicts returned by atlas_core, so they are unit-testable without MCP.
Text rounds coordinates to the metre; the structured result keeps full precision.
"""
from __future__ import annotations

SYNTHETIC_BANNER = "[SYNTHETIC TEST SITE — not Mars data]"


# ---- helpers ------------------------------------------------------------------------------

def num(v, nd: int = 1) -> str:
    """Compact number: ints as ints, small values with more decimals, large with fewer."""
    if v is None:
        return "n/a"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return str(v)
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f != f:
        return "n/a"
    a = abs(f)
    if a >= 100:
        return f"{f:.0f}" if a >= 1000 else f"{f:.1f}".rstrip("0").rstrip(".")
    if a >= 1:
        return f"{f:.{nd}f}".rstrip("0").rstrip(".") if nd else f"{f:.0f}"
    return (f"{f:.2f}".rstrip("0").rstrip(".") if a >= 0.01 else f"{f:.3g}")


def xy(x, y) -> str:
    return f"({round(x):.0f}, {round(y):.0f})"


def source_label(src) -> str:
    if src is None:
        return "user-supplied"
    s = str(src)
    if s.strip().lower() == "assumed":
        return "assumed"
    if s.strip().upper().startswith("TBD"):
        return "citation pending"
    return f"cited: {s}"


def assumptions_line(assumptions) -> str | None:
    if not assumptions:
        return None
    parts = [f"{a.get('what')} [{source_label(a.get('source'))}]" for a in assumptions]
    return "Thresholds used: " + "; ".join(parts) + "."


def with_banner(text: str, synthetic: bool) -> str:
    return f"{SYNTHETIC_BANNER}\n{text}" if synthetic else text


def _lines(*items) -> str:
    return "\n".join(i for i in items if i)


# ---- errors -------------------------------------------------------------------------------

def fmt_error(message: str, hint: str | None = None) -> str:
    return f"Error: {message}" + (f"\nTry: {hint}" if hint else "")


# ---- overview / catalogue -----------------------------------------------------------------

def fmt_overview(d: dict) -> str:
    s = d["site"]
    head = (f"Site \"{s['name']}\": {num(s['size_m'], 0)} m square at {num(s['res_m'])} m resolution, "
            f"centre {s['lat0']:.4f} N, {s['lon0']:.4f} E. {s['frame']}")
    by_kind: dict[str, list[dict]] = {}
    for l in d["layers"]:
        by_kind.setdefault(l["kind"], []).append(l)
    layer_lines = []
    for kind, ls in by_kind.items():
        layer_lines.append(f"{kind}: " + "; ".join(f"{l['id']} ({l['measures'].rstrip('.')})" for l in ls))
    prof = "Profiles: " + (", ".join(p["name"] for p in d["profiles"]) or "none")
    pois = "POIs: " + "; ".join(f"{p['id']} {xy(p['x'], p['y'])}" for p in d["pois"]) if d["pois"] else None
    rover = f"Rover: {xy(d['rover']['x'], d['rover']['y'])}" if d.get("rover") else "Rover: position not known."
    tiers = "Evidence tiers (ascending): " + " < ".join(d["evidence_tiers"]) + "."
    return _lines(head, "Layers:", *["  " + x for x in layer_lines], prof, pois, rover, tiers, d.get("next"))


def fmt_layers(d: dict) -> str:
    ls = d["layers"]
    if not ls:
        return "No layers match.\nTry: list_layers() with no filters."
    rows = [f"{len(ls)} layer(s):"]
    for l in ls:
        res = f", {num(l['resolution_m'])} m" if l.get("resolution_m") else ""
        rows.append(f"- {l['id']} [{l['kind']}{res}] {l['title']}: {l['measures']}")
    rows.append("Next: describe_layer(id) for limits, quality and citation before relying on a layer.")
    return "\n".join(rows)


def fmt_layer(d: dict) -> str:
    src = d.get("source") or {}
    cite = src.get("citation") or src.get("product") or "no citation recorded"
    head = f"{d['id']} [{d['kind']}] {d.get('title', '')}" + (f", units: {d['units']}" if d.get("units") else "")
    rng = ""
    if d["kind"] == "continuous" and d.get("min") is not None:
        rng = f"Range {num(d['min'])} to {num(d['max'])}, median {num(d['median'])}."
    cov = f"Coverage: {num(d['coverage_fraction'] * 100, 1)}% of cells have data (rest unknown, never treated as safe or absent)."
    return _lines(
        head,
        f"Measures: {d.get('measures')}",
        f"Does NOT measure: {d.get('does_not_measure')}",
        f"Quality: {d.get('quality')}",
        rng or None,
        cov,
        ("Classes: " + ", ".join(d["classes"]) + ".") if d.get("classes") else None,
        f"Resolution: {num(d.get('resolution_m'))} m. Source: {src.get('mission', '?')}/{src.get('instrument', '?')}, {cite}.",
        "Next: use this layer in find_locations/find_regions conditions or describe_location(layers=[...]).",
    )


# ---- describe_location --------------------------------------------------------------------

def _value_text(v: dict, area: bool) -> str:
    if v.get("unknown"):
        return "unknown (no data)"
    if "value" in v:
        return f"{num(v['value'])}{(' ' + v['units']) if v.get('units') else ''}"
    if "class" in v:
        return v["class"]
    if "tier" in v:
        return f"tier {v['tier']} {v['tier_name']}"
    if "best_tier" in v:
        top = sorted(v["shares"].items(), key=lambda kv: -kv[1])[:3]
        s = ", ".join(f"{k} {num(f * 100, 0)}%" for k, f in top)
        return f"best tier {v['best_tier']} {v['best_tier_name']} ({s}{_unk(v)})"
    if "shares" in v:
        top = sorted(v["shares"].items(), key=lambda kv: -kv[1])[:4]
        return ", ".join(f"{k} {num(f * 100, 0)}%" for k, f in top) + _unk(v)
    if "mean" in v:
        u = f" {v['units']}" if v.get("units") else ""
        return f"mean {num(v['mean'])}, max {num(v['max'])}, min {num(v['min'])}{u}{_unk(v)}"
    return str(v)


def _unk(v: dict) -> str:
    f = v.get("unknown_fraction") or 0
    return f", {num(f * 100, 0)}% unknown" if f > 0 else ""


def fmt_describe(d: dict) -> str:
    locs = d["locations"]
    r = d["radius_m"]
    mode = f"footprint stats within {num(r)} m" if r > 0 else "point values"
    out = [f"{len(locs)} location(s), {mode}."]
    for loc in locs:
        name = f" {loc['name']}" if loc.get("name") else ""
        out.append(f"{loc['id']}{name} {xy(loc['x'], loc['y'])} m ({loc['lat']:.4f}, {loc['lon']:.4f}):")
        for lid, v in loc["values"].items():
            out.append(f"  {lid}: {_value_text(v, r > 0)}")
        out.append(f"  data quality: {loc['quality']}")
        g = loc.get("nearest_ground_truth")
        if g:
            out.append(f"  nearest ground truth: {g['what']} at {xy(g['x'], g['y'])}, {num(g['distance_m'], 0)} m away")
        else:
            out.append("  nearest ground truth: none in this site")
        if len(locs) > 1:
            pass
    if r > 0:
        out.append("Note: slope/roughness max and unknown shares matter more than the mean for hazards.")
    out.append("Unknown means no data, not safe or absent. Next: describe_layer(id) for a layer's limits.")
    return "\n".join(out)


# ---- search results -----------------------------------------------------------------------

def _attrs(m: dict, keys=("slope_2m_mean", "slope_2m_max")) -> str:
    a = m.get("attributes") or {}
    shown = [f"{k}={num(a[k])}" for k in keys if k in a and a[k] is not None]
    extra = [f"{k}={num(v)}" for k, v in a.items()
             if k not in keys and k not in ("centroid_x", "centroid_y", "elevation_mean") and isinstance(v, (int, float))]
    return ", ".join(shown + extra)


def _member_line(m: dict, kind: str) -> str:
    base = f"{m['id']} {xy(m['x'], m['y'])} m"
    if kind == "spots":
        if m.get("clear_radius_m") is not None:
            base += f", clear radius {num(m['clear_radius_m'], 0)} m"
    elif kind == "regions" or m.get("area_m2") is not None:
        base += f", area {num(m.get('area_m2'), 0)} m2"
    if m.get("score") is not None:
        base += f", score {num(m['score'], 2)}"
    at = _attrs(m)
    return base + (f", {at}" if at else "")


def fmt_search(d: dict, kind: str) -> str:
    noun = "spots" if kind == "spots" else "regions"
    n = d["count"]
    res = d.get("results", [])
    if n == 0:
        head = f"No {noun} found (objective: {d.get('objective')})."
    else:
        head = (f"Found {n} {noun}; showing top {len(res)} by {d.get('objective')}. "
                f"Full ranked list stored as {d['set_id']}.")
    out = [head]
    out += [f"{i}. {_member_line(m, kind)}" for i, m in enumerate(res, 1)]
    diag = d.get("diagnostics") or []
    if diag and n == 0:
        out.append("Share of site cells passing each step:")
        out += [f"  {x['step']}: {_pct(x['pass_fraction'])}" for x in diag]
    elif diag:
        last = diag[-1]
        out.append(f"Diagnostics: {last['step']} passes {num(last['pass_fraction'] * 100, 1)}% of cells.")
    a = assumptions_line(d.get("assumptions"))
    if a:
        out.append(a)
    if d.get("hint"):
        out.append("Hint: " + d["hint"])
    elif n:
        out.append(f"Next: describe_location(\"{res[0]['id']}\", radius_m=...) to inspect, "
                   f"get_set(\"{d['set_id']}\") for the rest, or nearest(from_ref=\"{d['set_id']}\", to_ref=...).")
    return "\n".join(out)


def fmt_nearest(d: dict) -> str:
    res = d.get("results", [])
    n = d["count"]
    out = [f"{n} pair(s) ranked by cost; showing {len(res)}. Stored as {d['set_id']}."
           + (" Walk times/distances are approximate." if d.get("approximate") else "")]
    for i, p in enumerate(res, 1):
        unit = {"walk_time_min": "min", "walk_m": "m walk", "straight_m": "m straight"}.get(p.get("cost_metric"), "")
        out.append(f"{i}. {p['from_id']} -> {p['to_id']}: {num(p['cost'])} {unit}, straight {num(p['straight_m'], 0)} m, "
                   f"from {xy(p['x'], p['y'])} m")
    if d.get("unreachable"):
        out.append("Unreachable from: " + ", ".join(d["unreachable"][:10]) + ".")
    for note in (d.get("notes") or [])[:4]:
        out.append("Note: " + note)
    out.append("Next: get_set(set_id) for all pairs, or describe_location(from_id) to inspect an endpoint.")
    return "\n".join(out)


def fmt_set(d: dict) -> str:
    ms = d["members"]
    off = d["offset"]
    out = [f"{d['set_id']} ({d['kind']}, created by {d['created_by']}): {d['count']} member(s); "
           f"showing {off + 1 if ms else 0}-{off + len(ms)}."]
    for m in ms:
        if d["kind"] == "pairs":
            out.append(f"- {m['id']} {m['from_id']} -> {m['to_id']}: {num(m.get('cost'))} {m.get('cost_metric', '')}, "
                       f"straight {num(m.get('straight_m'), 0)} m")
        else:
            out.append("- " + _member_line(m, d["kind"]))
    if off + len(ms) < d["count"]:
        out.append(f"Next: get_set(\"{d['set_id']}\", offset={off + len(ms)}) for more.")
    else:
        out.append("End of set. Use describe_location(member_id) or nearest(...) to continue.")
    return "\n".join(out)


def _pct(f: float) -> str:
    """Percentage that never rounds a non-zero share down to 0 (or a partial share up to 100)."""
    if f <= 0:
        return "0%"
    if f >= 1:
        return "100%"
    if f < 0.0001:
        return "<0.01%"
    if f > 0.9999:
        return ">99.99%"
    return f"{f * 100:.2f}".rstrip("0").rstrip(".") + "%"

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
    s = str(src).strip()
    low = s.lower()
    if low.startswith("assumed"):
        return s if low != "assumed" else "assumed"      # keep the reason, e.g. "assumed (pads ...)"
    if low.startswith("tbd"):
        return "citation pending"
    if low in ("user-specified", "user-supplied", "user"):
        return "user-specified"
    if low.startswith("user") or "default" in low:
        return s                                         # user input or tool default, not a citation
    if low.startswith("default"):
        return s                                         # a tool default, not a citation
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


# ---- evidence provenance, per-tier areas, registration caveats --------------------------------

_TIER_ORDER = ("confirmed_in_situ", "strong_orbital", "weak_orbital", "inferred_from_unit", "not_detected", "no_coverage")
TIER_GLOSS = {
    "not_detected": "covered, nothing detected (absence of evidence, not absence of the mineral)",
    "no_coverage": "no data here (cannot answer)",
}


def area_text(m2) -> str:
    if m2 is None:
        return "n/a"
    return f"{m2 / 1e6:.2f} km2" if m2 >= 1e5 else f"{num(m2, 0)} m2"


def _samples_text(e: dict) -> str:
    s = (e.get("in_situ") or {}).get("samples") or []
    return "; ".join(x["name"] for x in s) or "sample not identified"


def ev_breakdown(e: dict) -> str:
    """Per-tier areas with provenance: orbital tiers cite the orbital instrument, tier 4 names the rover sample(s)."""
    by = e.get("area_by_tier_m2") or {}
    parts = []
    for n in _TIER_ORDER:
        if n not in by:
            continue
        tag = ""
        if n == "confirmed_in_situ":
            tag = f" [rover sample: {_samples_text(e)}]" if e.get("in_situ") else " [rover sample, source not recorded]"
        elif n in ("strong_orbital", "weak_orbital"):
            tag = f" [orbital: {e.get('orbital_source', '?')}]"
        elif n == "not_detected":
            tag = f" [covered by {e.get('orbital_source', '?')}, nothing detected: absence of evidence]"
        elif n == "no_coverage":
            tag = " [no data, cannot answer]"
        parts.append(f"{n} {area_text(by[n])}{tag}")
    return ", ".join(parts)


def ev_headline(layer: str, e: dict) -> str:
    """Best tier with its basis, then the area split (never the region total next to the best tier)."""
    if e.get("best_tier") is None:
        return f"{layer}: no_coverage (no data here, cannot answer)"
    return f"{layer} best tier {e['best_tier']} {e['best_tier_name']}; area by tier: {ev_breakdown(e)}"


def insitu_sources_line(evs) -> str | None:
    """One line naming each rover sample behind a tier-4 mark, with its citation."""
    by_cite: dict = {}
    for e in evs:
        for s in (e.get("in_situ") or {}).get("samples") or []:
            names = by_cite.setdefault(s.get("citation"), [])
            if s["name"] not in names:
                names.append(s["name"])
    if not by_cite:
        return None
    return ("Tier 4 (confirmed_in_situ) is a rover sample claim, placed at a derived position (10-50 m), NOT an orbital detection. Sources: "
            + "; ".join(f"{', '.join(n)}: {c}" for c, n in by_cite.items()) + ".")


def _short_cite(c) -> str | None:
    import re
    if not c:
        return None
    m = re.match(r"[^,(;]*?\d{4}", str(c))
    return m.group(0) if m else str(c)[:60]


def registration_lines(d: dict) -> list[str]:
    return list((d.get("registration_notes") or {}).values())


def _flags(m: dict) -> str:
    f = m.get("near_boundary_of")
    if not f:
        return ""
    return f" [footprint near a boundary of {', '.join(f) if len(f) <= 3 else str(len(f)) + ' layers'}: see caveat]"


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
    pois = ("POIs (use as poi:<id>): " + "; ".join(
        f"poi:{p['id']} {xy(p['x'], p['y'])}" + (f" \"{p['name']}\"" if p.get("name") and p["name"] != p["id"] else "")
        + (f" [{p['type']}]" if p.get("type") else "")
        + (f" science_value {num(p['science_value'])}" if p.get("science_value") is not None else "")
        for p in d["pois"]) + ".") if d["pois"] else "POIs: none."
    rv = d.get("rover")
    rover = (f"Rover: {xy(rv['x'], rv['y'])}" + (f", mode {rv['mode']}" if rv.get("mode") else "")
             + (", FSD armed" if rv.get("fsd_armed") else "")) if rv else "Rover: position not known."
    mk = d.get("markers")
    markers = ("Markers: " + "; ".join(f"marker:{m['id']} {xy(m['x'], m['y'])}"
                                       + (f" \"{m['label']}\"" if m.get("label") and m["label"] != m["id"] else "")
                                       for m in mk)) if mk else "Markers: none placed."
    tg = d.get("search_targets")
    targets = ("Search targets for find_sites: " + "; ".join(
        f"{t['target']} ({t.get('mission') or '?'}{('/' + t['instrument']) if t.get('instrument') else ''})"
        for t in tg) + ".") if tg else (
        "Search targets for find_sites: none (terrain-only site: no mineral or geology layers). find_sites and "
        "evidence/geologic conditions will fail; say the data cannot answer such questions.")
    tiers = "Evidence tiers (ascending): " + " < ".join(d["evidence_tiers"]) + "."
    return _lines(head, "Layers:", *["  " + x for x in layer_lines], prof, pois, rover, markers, targets, tiers,
                  d.get("next"))


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

def _value_text(v: dict, area: bool, evidence_layer: bool = False) -> str:
    if v.get("unknown"):
        return ("unknown = no_coverage: " + TIER_GLOSS["no_coverage"]) if evidence_layer else "unknown (no data)"
    if "value" in v:
        return f"{num(v['value'])}{(' ' + v['units']) if v.get('units') else ''}"
    if "class" in v:
        return v["class"]
    if "tier" in v:
        gl = TIER_GLOSS.get(v["tier_name"])
        return f"tier {v['tier']} {v['tier_name']}" + (f" = {gl}" if gl else "")
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
        evs = loc.get("evidence") or {}
        grouped: dict[str, list[str]] = {"not_detected": [], "no_coverage": []}
        for lid, v in loc["values"].items():
            e = evs.get(lid)
            if e is not None and r == 0 and (v.get("unknown") or v.get("tier") in (0, -1)):
                # One line per state instead of repeating the gloss for every evidence layer.
                grouped["no_coverage" if (v.get("unknown") or v.get("tier") == -1) else "not_detected"].append(lid)
                continue
            if e is not None and (r > 0 or v.get("tier") in (2, 3, 4)):
                if v.get("unknown") or e.get("best_tier") is None:
                    out.append(f"  {lid}: unknown = no_coverage: {TIER_GLOSS['no_coverage']}"
                               + (f" ({num(v['unknown_fraction'] * 100, 0)}% of footprint)" if v.get("unknown_fraction") else ""))
                elif r > 0:
                    out.append(f"  {ev_headline(lid, e)}")
                else:
                    t = v["tier_name"]
                    basis = (f"rover sample: {_samples_text(e)}" if t == "confirmed_in_situ" else f"orbital: {e.get('orbital_source', '?')}")
                    out.append(f"  {lid}: tier {v['tier']} {t} ({basis})")
            else:
                out.append(f"  {lid}: {_value_text(v, r > 0, e is not None)}")
        for state, lids in grouped.items():
            if lids:
                out.append(f"  {state} ({TIER_GLOSS[state]}): " + ", ".join(lids))
        out.append(f"  data quality: {loc['quality']}")
        g = loc.get("nearest_ground_truth")
        if g:
            smp = g.get("sample") or {}
            mins = smp.get("in_situ_minerals") or []
            cite = _short_cite(g.get("citation") if mins else None)
            who = (f" ({smp['name']}, {smp.get('source', 'source not recorded')}; minerals measured in situ per source: "
                   f"{', '.join(mins) if mins else 'none read (rock-type label only)'}"
                   f"{'; ' + cite + ', full citation under the Tier 4 sources line or describe_layer' if cite else ''})") if smp else ""
            out.append(f"  nearest ground truth: {g['what']} at {xy(g['x'], g['y'])}, {num(g['distance_m'], 0)} m away{who}")
        else:
            out.append("  nearest ground truth: none in this site")
        if loc.get("near_boundary_of"):
            nb = loc["near_boundary_of"]
            out.append("  near a boundary of: " + (", ".join(nb) if len(nb) <= 3 else f"{', '.join(nb[:2])} and {len(nb) - 2} more layers")
                       + " (see caveats below)")
    src = insitu_sources_line([e for loc in locs for e in (loc.get("evidence") or {}).values()])
    if src:
        out.append(src)
    out += registration_lines(d)
    if d.get("truncated"):
        out.append(d["truncated"]["note"])
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
    ev = m.get("evidence")
    if ev:
        if kind != "spots":
            base = base.replace(", area ", ", region area ", 1) + " (whole region, not the area at the best tier)"
        base += "; " + "; ".join(
            (f"{k}: no_coverage" if e.get("best_tier") is None else ev_headline(k, e)) for k, e in ev.items())
    return base + (f", {at}" if at else "") + _flags(m)


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
    src = insitu_sources_line([e for m in res for e in (m.get("evidence") or {}).values()])
    if src:
        out.append(src)
    out += registration_lines(d)
    if d.get("registration_notes") and kind != "spots":
        out.append("Region extents follow that layer's boundaries, so their edges are uncertain by the stated distance.")
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
        to_at = f" at {xy(p['to_x'], p['to_y'])}" if p.get("to_x") is not None else ""
        evtxt = ""
        for side in ("from", "to"):
            for k, e in (p.get(f"{side}_evidence") or {}).items():
                evtxt += (f"; {side} {k}: " + ("no_coverage" if e.get("best_tier") is None else
                                               f"best tier {e['best_tier_name']} (area by tier: {ev_breakdown(e)})"))
        out.append(f"{i}. {p['from_id']} -> {p['to_id']}{to_at}: {num(p['cost'])} {unit}, straight {num(p['straight_m'], 0)} m, "
                   f"from {xy(p['x'], p['y'])} m" + evtxt)
    src = insitu_sources_line([e for p in res for side in ("from", "to") for e in (p.get(f"{side}_evidence") or {}).values()])
    if src:
        out.append(src)
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


# ---- routes, traverses, profiles ----------------------------------------------------------

def _min(v) -> str:
    return f"{num(v, 0) if v is not None and abs(v) >= 10 else num(v)} min"


def _consumables_line(c: dict | None) -> str | None:
    if not c:
        return None
    u, m = c["used"], c["margin"]
    stops = f" including {num(c['stops_min'], 0)} min at stops" if c.get("stops_min") else ""
    return (f"Consumables (time only; EVA limit {num(c['eva_limit_min'], 0)} min, reserve "
            f"{num(c['reserve_fraction'] * 100, 0)}%): used {num(u['minutes'])} min{stops} ({num(u['fraction'] * 100, 0)}%), "
            f"margin above reserve {num(m['minutes'])} min ({num(m['fraction'] * 100, 0)}%).")


def _go_line(a: dict | None) -> str | None:
    if not a:
        return None
    if a["go"]:
        return "Assessment: GO" + ("" if not a.get("caution") else " with cautions: " + " ".join(a["caution"]))
    return "Assessment: NO-GO. " + " ".join(a["reasons"])


def fmt_route(d: dict) -> str:
    if not d["feasible"]:
        nr = d.get("nearest_reachable")
        near = (f"Nearest reachable point: {xy(nr['x'], nr['y'])} m, {num(nr['distance_to_goal_m'], 0)} m from the "
                f"goal, {num(nr['travel_min'])} min travel." if nr else "No reachable point found.")
        return _lines(f"NO ROUTE (feasible: false). {d['reason']}", near, "Hint: " + d["hint"] if d.get("hint") else None)
    stops = "; ".join(f"{s['kind']} {xy(s['x'], s['y'])} at {num(s['distance_m'], 0)} m, {num(s['eta_min'])} min"
                      for s in d.get("stops", []))
    head = (f"Route {d['route_id']} ({d['profile']}, slope limit {num(d['max_slope_limit_deg'])} deg): "
            f"{num(d['distance_m'], 0)} m, {num(d['duration_min'])} min, ascent {num(d['ascent_m'], 0)} m, "
            f"max slope {num(d['max_slope_deg'])} deg ({_limits(d)}), {d['hazard_cells']} cell(s) steeper "
            f"than {num(d['caution_slope_deg'])} deg.")
    warn = [w for w in d.get("warnings", []) if w not in (d.get("assessment") or {}).get("reasons", [])
            and w not in (d.get("assessment") or {}).get("caution", [])]
    return _lines(
        head, ("Stops: " + stops) if stops else None, _consumables_line(d.get("consumables")),
        _go_line(d.get("assessment")), ("Warnings: " + " ".join(warn)) if warn else None,
        "Times use a placeholder isotropic speed model (approximate). " + (
            "Assumed: " + "; ".join(d["assumptions"]) + "." if d.get("assumptions") else ""),
        f"Next: assess_route(\"{d['route_id']}\"), then follow_route(\"{d['route_id']}\") (rover must be at the start).")


def _limits(d: dict) -> str:
    lim, cau = d["max_slope_limit_deg"], d["caution_slope_deg"]
    if cau >= lim:
        return f"no-go above {num(lim)} deg, no separate caution band"
    return f"caution above {num(cau)} deg, no-go above {num(lim)} deg"


def fmt_assess(d: dict) -> str:
    if not d["feasible"]:
        return f"Route {d.get('route_id')} is NOT feasible: {d['reason']}"
    a = d["assessment"]
    verdict = "GO" if a["go"] else "NO-GO"
    return _lines(
        f"{verdict} for {d['route_id']} ({d['profile']}): {num(d['distance_m'], 0)} m, "
        f"{'travel ' if (d.get('consumables') or {}).get('stops_min') else ''}{num(d['duration_min'])} min, "
        f"max slope {num(d['max_slope_deg'])} deg ({_limits(d)}), ascent {num(d['ascent_m'], 0)} m.",
        _consumables_line(d.get("consumables")),
        ("Reasons: " + " ".join(a["reasons"])) if a["reasons"] else None,
        ("Cautions: " + " ".join(a["caution"])) if a["caution"] else None,
        "Limits marked assumed in the profile are not cited values." if d.get("assumptions") else None,
        ("Next: follow_route to drive it." if a["go"] else "Next: plan_route with a lower max_slope_deg, a shorter goal, "
         "or a larger time budget; do not call this route safe."))


def fmt_science(d: dict) -> str:
    t = d["time"]
    r = d.get("route")
    if not d["stops"]:
        return _lines(f"No science stops fit (budget {num(t['budget_min'], 0)} min, {d['candidates_considered']} "
                      "candidate(s) considered).", "Hint: " + d["hint"])
    sv = d.get("science_value_total")
    sv_txt = (f"science value {num(sv, 2)} (recorded values only)" if sv is not None
              else "no science value reported: none recorded, stops ordered by travel time only")
    lines = [f"Considered {d['candidates_considered']} candidate stop(s); selected {len(d['stops'])}. "
             f"Traverse with {len(d['stops'])} stop(s), {sv_txt}; "
             f"{num(t['total_min'], 0)} of {num(t['budget_min'], 0)} min ({num(t['travel_min'], 0)} travel + "
             f"{num(t['stops_min'], 0)} at stops{', returns to start' if t['return_to_start'] else ''})."]
    for i, s in enumerate(d["stops"], 1):
        v = f"{num(s['science_value'], 2)}{' (assumed, no value recorded)' if s['value_assumed'] else ''}"
        lines.append(f"{i}. {s['id']} {s.get('name') or ''} {xy(s['x'], s['y'])} m, arrive {num(s['arrive_min'], 0)} min, "
                     f"value {v}" + (f", matched: {'; '.join(s['matched_by'])}" if s["matched_by"] else ""))
    if d.get("science_weighting_note"):
        lines.append("Note: " + d["science_weighting_note"])
    if r:
        lines.append(f"Route {r['route_id']}: {num(r['distance_m'], 0)} m, max slope {num(r['max_slope_deg'])} deg, "
                     f"{_go_line(r.get('assessment')) or ''}")
        c = _consumables_line(r.get("consumables"))
        if c:
            lines.append(c)
    if d["skipped"]:
        lines.append("Skipped: " + "; ".join(f"{s['id']} ({s['reason']})" for s in d["skipped"][:4]) + ".")
    for n in [x for x in d.get("notes", [])[1:3] if x != d.get("science_weighting_note")]:
        lines.append("Note: " + n)
    lines.append("Next: assess_route(route_id) for go/no-go; set_view(highlights=[...]) to show the stops.")
    return "\n".join(lines)


def fmt_profile(d: dict) -> str:
    s = d["samples"]
    pick = s if len(s) <= 9 else [s[round(i * (len(s) - 1) / 8)] for i in range(9)]
    rows = "; ".join(f"{num(p['d'], 0)} m: z {num(p['z'])} slope {num(p['slope_deg'])}" for p in pick)
    unk = f" {d['unknown_samples']} sample(s) have no data." if d["unknown_samples"] else ""
    return _lines(
        f"Profile {num(d['distance_m'], 0)} m, {d['count']} samples every {num(d['spacing_m'])} m: elevation "
        f"{num(d['min_z'])} to {num(d['max_z'])} m, ascent {num(d['ascent_m'])} m, descent {num(d['descent_m'])} m, "
        f"slope mean {num(d['mean_slope_deg'])} / max {num(d['max_slope_deg'])} deg.{unk}",
        "Samples (distance: elevation m, slope deg): " + rows,
        "Next: plan_route to get a drivable route; slope is per 2 m cell, so small rocks are not resolved.")


def fmt_pois(d: dict) -> str:
    ps = d["pois"]
    if not ps:
        ids = d.get("known_ids") or []
        known = f" Known POIs: {', '.join(ids)}." if ids else ""
        return (f"No POIs match.{known} Known types: {', '.join(d['types']) or 'none'}.\n"
                "Try: a shorter query (the query matches id, name, type and description), or search_pois() with no "
                "filters to list them all.")
    rows = []
    for p in ps[:12]:
        sv = p.get("science_value")
        extra = ""
        if p.get("type") == "sample_site":
            mins = p.get("in_situ_minerals") or []
            extra = (f" (region {p.get('region')}, rock type {p.get('rock_type')}; minerals measured in situ per source: "
                     f"{', '.join(mins) if mins else 'none read'}; position approximate"
                     + (f" +-{num(p['position_uncertainty_m'], 0)} m" if p.get("position_uncertainty_m") is not None else "") + ")")
        rows.append(f"- {p['ref']} {p.get('name') or ''} [{p.get('type')}] {xy(p['x'], p['y'])} m"
                    + (f", {num(p['distance_m'], 0)} m away" if p.get("distance_m") is not None else "")
                    + (f", science value {num(sv, 2)}" if sv is not None else ", science value not recorded") + extra)
        if p.get("description") and len(ps) <= 3:
            rows.append("  description: " + p["description"][:900] + ("..." if len(p["description"]) > 900 else ""))
            if p.get("source_url"):
                rows.append(f"  source: {p['source_url']}")
    more = f" (showing {len(ps[:12])})" if d["count"] > 12 else ""
    return _lines(f"{d['count']} POI(s){more}:", *rows,
                  "Next: goto_location(target=\"poi:<id>\"), plan_route(goal=...), or fly_camera(target=...).")


# ---- rover, FSD, view, markers, sites -------------------------------------------------------

def fmt_rover_state(s: dict) -> str:
    z = f", z {num(s['z'], 0)} m" if s.get("z") is not None else ""
    return _lines(
        f"Rover {xy(s['x'], s['y'])} m{z}, heading {num(s['heading_deg'], 0)} deg (clockwise from north), pitch "
        f"{num(s['pitch_deg'])}, roll {num(s['roll_deg'])} deg; mode {s['mode']}"
        f"{' (FSD armed)' if s['fsd_armed'] else ''}, speed {num(s['speed_mps'], 2)} m/s, odometer "
        f"{num(s['odometer_m'], 0)} m.",
        f"Status: {s['status']}. Last event: {s['last_event']}." + (
            f" Route {s['route_id']}, {num(s['remaining_m'], 0)} m remaining." if s.get("route_id") and s.get("remaining_m") else ""),
        f"Profile {s['profile']}. Simulated time driven {num(s['sim_elapsed_s'] / 60)} min.")


_REASON_TEXT = {
    "arrived": "ARRIVED", "blocked_slope": "STOPPED: blocked_slope", "blocked_hazard": "STOPPED: blocked_hazard",
    "replanned": "STOPPED: replanned", "stopped_by_user": "STOPPED: stopped_by_user", "timeout": "STOPPED: timeout",
}


def fmt_command(d: dict) -> str:
    st = d["state"]
    reason = d.get("stop_reason")
    if d.get("feasible") is False:
        nr = d.get("nearest_reachable")
        return _lines(f"NOT DRIVEN ({reason}): {d['detail']}",
                      (f"Nearest reachable point {xy(nr['x'], nr['y'])} m ({num(nr['distance_to_goal_m'], 0)} m from "
                       "the goal).") if nr else None,
                      f"Rover stays at {xy(st['x'], st['y'])} m.", "Hint: " + d["hint"] if d.get("hint") else None)
    head = _REASON_TEXT.get(reason, str(reason)) + (f" - {d['detail']}" if d.get("detail") else "")
    drove = ""
    if d.get("distance_m") is not None and reason is not None and "elapsed_sim_min" in d:
        drove = f" Drove {num(d['distance_m'], 0)} m in {num(d['elapsed_sim_min'])} simulated min."
    nr = d.get("new_route")
    return _lines(
        head, f"Rover now {xy(st['x'], st['y'])} m, heading {num(st['heading_deg'], 0)} deg, odometer "
              f"{num(st['odometer_m'], 0)} m, pitch {num(st['pitch_deg'])} roll {num(st['roll_deg'])} deg.{drove}",
        (f"New route {nr['route_id']}: {num(nr['distance_m'], 0)} m, {num(nr['duration_min'])} min, max slope "
         f"{num(nr['max_slope_deg'])} deg. Next: follow_route(\"{nr['route_id']}\").") if nr else None,
        ("Replan failed: " + d["replan_failed"]) if d.get("replan_failed") else None,
        ("Note: " + d["note"]) if d.get("note") else None,
        ("Hint: " + d["hint"]) if d.get("hint") else None)


def fmt_fsd(d: dict) -> str:
    h = d.get("last_hazard")
    pol = ", ".join(f"{k}={'on' if v else 'off'}" for k, v in d["policy"].items())
    return _lines(
        f"FSD {'ARMED' if d['armed'] else 'off'}; mode {d['mode']}; policy {pol}; replans used {d['replans_used']} of "
        f"{d['replans_allowed']}; route {d.get('route_id') or 'none'}, {num(d['remaining_route_m'], 0)} m remaining.",
        ("Last hazard: " + h["detail"] + f" at {xy(h['x'], h['y'])} m.") if h else "No hazard recorded.",
        ("Blockers: " + "; ".join(d["blockers"]) + ".") if d["blockers"] else "No blockers.",
        "Next: follow_route to continue, or plan_route from the rover to re-plan." if d["blockers"] else
        "Next: plan_route then follow_route; fsd_enable first to allow one automatic replan.")


def fmt_fsd_change(d: dict) -> str:
    return fmt_fsd(d["fsd"])


def fmt_view(d: dict) -> str:
    parts = []
    if d.get("mode"):
        parts.append(f"mode {d['mode']}")
    if d.get("target"):
        t = d["target"]
        parts.append(f"target {t.get('id') or ''} {xy(t['x'], t['y'])} m".replace("  ", " "))
    if d.get("layers"):
        parts.append("layers " + ", ".join(f"{l['layer']} {'on' if l['visible'] else 'off'}" for l in d["layers"]))
    if d.get("highlights"):
        parts.append(f"{len(d['highlights'])} highlight(s): " + ", ".join(str(h['id']) for h in d["highlights"][:8]))
    return _lines("View update sent: " + "; ".join(parts) + ".", d.get("note"),
                  "Next: describe the scene from tool results, or place_marker / plan_route.")


def fmt_markers(d: dict) -> str:
    if not d["markers"]:
        return "No markers placed.\nNext: place_marker(x, y, label) or ask the user to click the ground."
    rows = [f"- marker:{m['id']} {xy(m['x'], m['y'])} m z {num(m.get('z'), 0)}, \"{m.get('label')}\", placed by "
            f"{m['created_by']}" for m in d["markers"]]
    return _lines(f"{d['count']} marker(s):", *rows,
                  "Use them anywhere a location is accepted, e.g. plan_route(start=\"marker:m1\", goal=\"marker:m2\").")


def fmt_marker(m: dict) -> str:
    return (f"Placed marker:{m['id']} \"{m['label']}\" at {xy(m['x'], m['y'])} m (z {num(m.get('z'), 0)}, "
            f"{m['lat']:.4f}, {m['lon']:.4f}).\nUse \"marker:{m['id']}\" as a location in other tools.")


def fmt_sites(d: dict) -> str:
    n, ss = d["count"], d["sites"]
    if n == 0:
        return _lines(f"No sites found for {d['target']}.", "Hint: " + d["hint"])
    out = [f"{n} site(s) for {d['target']} ({d['kind']}); top {len(ss)}, ranked by "
           f"{'best evidence tier then area' if d['kind'] == 'evidence' else 'area'}, from {d['near']['id']} "
           f"{xy(d['near']['x'], d['near']['y'])}. Stored as {d['set_id']}."]
    for i, s in enumerate(ss, 1):
        ev = s["evidence"][0]
        tier = f"best tier {s['best_tier']} {s['best_tier_name']}, " if s.get("best_tier") is not None else ""
        reach = "reachable" if s["reachable"] else "NOT reachable"
        walk = f" ({num(s['walk_distance_m'], 0)} m walk)" if s.get("walk_distance_m") is not None else ""
        if s.get("area_by_tier_m2"):
            e = {"area_by_tier_m2": s["area_by_tier_m2"], "in_situ": ev.get("in_situ"),
                 "orbital_source": ev.get("orbital_source") or f"{ev.get('mission') or '?'}/{ev.get('instrument') or '?'}"}
            area = (f"region area {area_text(s['area_m2'])} in total (whole region, not the area at the best tier); "
                    f"by tier: {ev_breakdown(e)}; ")
            tier = f"best tier {s['best_tier']} {s['best_tier_name']}, "
        else:
            area = f"area {num(s['area_m2'], 0)} m2, "
        out.append(f"{i}. {s['id']} {xy(s['x'], s['y'])} m, {tier}{area}mean slope "
                   f"{num(s['mean_slope_deg'])} deg, {num(s['distance_m'], 0)} m away, {reach}{walk}; layer {ev['layer']}")
    src = insitu_sources_line([{"in_situ": s["evidence"][0].get("in_situ")} for s in ss if s["evidence"][0].get("in_situ")])
    if src:
        out.append(src)
    out += registration_lines(d)
    out.append("Orbital tiers are detections from orbit, not abundance; region extents follow pixel/polygon boundaries and are "
               "uncertain by the stated distance. Hint: " + d["hint"])
    out.append("Next: set_view(highlights=[ids]) to show them, plan_route(goal=\"<id>\") to get there.")
    return "\n".join(out)

"""Condition language -> boolean masks. Spec: contracts/mcp-tools.md section 4.4.
OWNER: builder "search". Signatures are FROZEN; implement the bodies.

Condition forms (all conditions in a list are ANDed):
    {"layer": "<continuous id>", "op": "<"|"<="|">"|">=", "value": number}
    {"layer": "<continuous id>", "op": "between", "value": [lo, hi]}          inclusive
    {"layer": "<categorical id>", "op": "=="|"!=", "value": "<class name>"|code}
    {"layer": "<categorical id>", "op": "in"|"not_in", "value": [names or codes]}
    {"evidence": "<evidence layer id>", "min_tier": "<tier name>"|int}
    {"any_of": [condition, ...]}                                               OR of sub-conditions
Every condition may carry optional "source" (citation string or "assumed"); profiles supply it.

Rules:
    - Unknown cells (NaN, -1) FAIL every condition, including "!=" and "not_in". "No coverage"
      must never be treated as satisfying anything.
    - Using an op that does not fit the layer kind raises AtlasError with a hint naming valid ops.
    - Unknown class names raise AtlasError listing the valid class names.
    - min_tier accepts names from site.TIERS or ints 0..4.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .errors import AtlasError
from .site import TIER_NAMES, TIERS, Site


@dataclass
class Compiled:
    label: str            # human-readable, e.g. "slope_2m <= 10", "carbonate >= strong_orbital"
    mask: np.ndarray      # bool, site grid shape
    source: str | None    # citation or "assumed" or None (user-supplied)


_CONT_OPS = ("<", "<=", ">", ">=", "between")
_CAT_OPS = ("==", "!=", "in", "not_in")


def _key(cond: dict):
    if "layer" in cond:
        return ("layer", cond["layer"])
    if "evidence" in cond:
        return ("evidence", cond["evidence"])
    return None


def expand_aliases(site: Site, conditions: list[dict] | None) -> tuple[list[dict] | None, list[str]]:
    """Replace {"evidence": <alias>} by the layer(s) the alias stands for (a group alias becomes an
    any_of over the member layers). Real layer ids are never rewritten. Returns (conditions, notes);
    notes say how each alias was resolved, e.g. "clay -> fe_mg_smectite, kaolinite, mg_smectite_2"."""
    from .catalog import resolution_text, resolve_alias
    notes: list[str] = []

    def one(c):
        if not isinstance(c, dict):
            return c
        if "any_of" in c and isinstance(c["any_of"], list):
            return {**c, "any_of": [one(x) for x in c["any_of"]]}
        name = c.get("evidence")
        if isinstance(name, str) and name not in site.layers:
            ids = resolve_alias(site, name)
            if ids:
                note = resolution_text(name, ids)
                if note not in notes:
                    notes.append(note)
                base = {k: v for k, v in c.items() if k != "evidence"}
                if len(ids) == 1:
                    return {"evidence": ids[0], **base}
                return {"any_of": [{"evidence": i, **base} for i in sorted(ids)]}
        return c

    if not conditions:
        return conditions, notes
    return [one(c) for c in conditions], notes


def merge_profile(site: Site, conditions: list[dict] | None, profile: str | None) -> tuple[list[dict], dict]:
    """Combine a profile's conditions with explicit ones.

    Explicit conditions override profile conditions on the same (layer or evidence) key.
    Returns (conditions, profile_params) where profile_params is the profile dict minus
    "conditions" (e.g. footprint_radius_m, travel), or {} if no profile.
    Raises AtlasError for an unknown profile (hint lists profile names)."""
    explicit = [dict(c) for c in (conditions or [])]
    if profile is None:
        return explicit, {}
    if profile not in site.profiles:
        raise AtlasError(
            f'Unknown profile "{profile}".',
            hint="Available profiles: " + (", ".join(sorted(site.profiles)) or "none") + ".",
        )
    prof = site.profiles[profile] or {}
    params = {k: v for k, v in prof.items() if k != "conditions"}
    override = {_key(c) for c in explicit if _key(c) is not None}
    kept = [dict(c) for c in prof.get("conditions", []) or []
            if _key(c) is None or _key(c) not in override]
    return kept + explicit, params


def _fmt(v) -> str:
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_fmt(x) for x in v) + "]"
    if isinstance(v, float) and v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return str(v)


def _is_num(v) -> bool:
    return isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, (bool, np.bool_)) \
        and bool(np.isfinite(v))


def _tier(value) -> int:
    if isinstance(value, str):
        if value in TIERS:
            return TIERS[value]
        raise AtlasError(f'Unknown evidence tier "{value}".',
                         hint="Valid tiers: " + ", ".join(TIERS) + " (or integers 0..4).")
    if isinstance(value, (int, np.integer)) and not isinstance(value, (bool, np.bool_)) and 0 <= value <= 4:
        return int(value)
    raise AtlasError(f"Invalid min_tier {value!r}.",
                     hint="Valid tiers: " + ", ".join(TIERS) + " (or integers 0..4).")


def _class_code(layer, v) -> int:
    classes = layer.classes or {}
    names = ", ".join(f"{n} ({c})" for c, n in sorted(classes.items()))
    if isinstance(v, str):
        for code, name in classes.items():
            if name == v:
                return int(code)
        low = [code for code, name in classes.items() if name.lower() == v.lower()]
        if len(low) == 1:
            return int(low[0])
        raise AtlasError(f'Unknown class "{v}" for layer "{layer.id}".',
                         hint=f"Valid classes: {names}. Call describe_layer(\"{layer.id}\").")
    if isinstance(v, (int, np.integer)) and not isinstance(v, (bool, np.bool_)):
        if int(v) in classes:
            return int(v)
        raise AtlasError(f"Unknown class code {v} for layer \"{layer.id}\".",
                         hint=f"Valid classes: {names}.")
    raise AtlasError(f"Invalid class value {v!r} for layer \"{layer.id}\".",
                     hint=f"Use a class name or code. Valid classes: {names}.")


def compile_condition(site: Site, cond: dict) -> Compiled:
    if not isinstance(cond, dict):
        raise AtlasError(f"Condition must be an object, got {cond!r}.",
                         hint='Example: {"layer": "slope_2m", "op": "<=", "value": 10}.')
    source = cond.get("source")
    shape = (site.size_px, site.size_px)

    if "any_of" in cond:
        subs = cond["any_of"]
        if not isinstance(subs, list) or not subs:
            raise AtlasError("any_of needs a non-empty list of conditions.",
                             hint='Example: {"any_of": [{"evidence": "carbonate", "min_tier": "weak_orbital"}, ...]}.')
        parts = [compile_condition(site, s) for s in subs]
        mask = np.zeros(shape, bool)
        for p in parts:
            mask |= p.mask
        return Compiled("any_of(" + " | ".join(p.label for p in parts) + ")", mask, source)

    if "evidence" in cond:
        layer = site.layer(cond["evidence"])
        if layer.kind != "evidence":
            raise AtlasError(f'Layer "{layer.id}" is {layer.kind}, not an evidence layer.',
                             hint=f'Use {{"layer": "{layer.id}", "op": ..., "value": ...}} instead.')
        if "min_tier" not in cond:
            raise AtlasError("Evidence condition needs min_tier.",
                             hint='Example: {"evidence": "carbonate", "min_tier": "strong_orbital"}.')
        t = _tier(cond["min_tier"])
        mask = (layer.data >= t) & ~layer.unknown
        return Compiled(f"{layer.id} >= {TIER_NAMES[t]}", mask, source)

    if "layer" not in cond:
        raise AtlasError(f"Condition {cond!r} has no layer, evidence or any_of key.",
                         hint='Use {"layer":..,"op":..,"value":..}, {"evidence":..,"min_tier":..} or {"any_of":[..]}.')
    layer = site.layer(cond["layer"])
    op = cond.get("op")
    if "value" not in cond:
        raise AtlasError(f"Condition on {layer.id} needs a value.")
    value = cond["value"]

    if layer.kind == "evidence":
        raise AtlasError(f'Layer "{layer.id}" is an evidence layer; ops do not apply.',
                         hint=f'Use {{"evidence": "{layer.id}", "min_tier": "strong_orbital"}}. '
                              "Tiers: " + ", ".join(TIERS) + ".")
    if layer.kind == "continuous":
        if op not in _CONT_OPS:
            raise AtlasError(f'Operator {op!r} does not apply to continuous layer "{layer.id}".',
                             hint="Valid ops for continuous layers: " + ", ".join(_CONT_OPS) + ".")
        d = layer.data
        if op == "between":
            if not (isinstance(value, (list, tuple)) and len(value) == 2 and all(_is_num(v) for v in value)):
                raise AtlasError('"between" needs a value of the form [low, high].')
            lo, hi = value
            if lo > hi:
                raise AtlasError(f"between range [{lo}, {hi}] is empty (low > high).",
                                 hint="Pass [low, high] with low <= high.")
            with np.errstate(invalid="ignore"):
                m = (d >= lo) & (d <= hi)
        else:
            if not _is_num(value):
                raise AtlasError(f"Value for {layer.id} {op} must be a finite number, got {value!r}.")
            with np.errstate(invalid="ignore"):
                m = {"<": d < value, "<=": d <= value, ">": d > value, ">=": d >= value}[op]
        mask = m & ~layer.unknown
        return Compiled(f"{layer.id} {op} {_fmt(value)}", mask, source)

    # categorical
    if op not in _CAT_OPS:
        raise AtlasError(f'Operator {op!r} does not apply to categorical layer "{layer.id}".',
                         hint="Valid ops for categorical layers: " + ", ".join(_CAT_OPS) + ".")
    d = layer.data
    if op in ("==", "!="):
        if isinstance(value, (list, tuple)):
            raise AtlasError(f'"{op}" takes one class; use in / not_in for several.')
        code = _class_code(layer, value)
        m = (d == code) if op == "==" else (d != code)
    else:
        if not isinstance(value, (list, tuple)) or not value:
            raise AtlasError(f'"{op}" needs a non-empty list of classes.')
        codes = [_class_code(layer, v) for v in value]
        m = np.isin(d, codes)
        if op == "not_in":
            m = ~m
    mask = m & ~layer.unknown
    return Compiled(f"{layer.id} {op} {_fmt(value)}", mask, source)


def compile_all(site: Site, conditions: list[dict]) -> list[Compiled]:
    return [compile_condition(site, c) for c in conditions]


def combine(compiled: list[Compiled], shape: tuple[int, int]) -> np.ndarray:
    """AND of all masks; all-True if the list is empty."""
    out = np.ones(shape, bool)
    for c in compiled:
        out &= c.mask
    return out

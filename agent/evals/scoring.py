"""Eval scoring: tool choice, grounded numbers, reply checks. Pure functions, no network, no key."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

# ---------------------------------------------------------------------------------------------
# Number extraction
# ---------------------------------------------------------------------------------------------

NUM_RE = re.compile(r"(?P<sign>[-−+]?)(?P<int>\d{1,3}(?:,\d{3})+|\d+)(?P<frac>\.\d+)?(?P<exp>[eE][-+]?\d+)?")
DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}:\d{2}(?::\d{2})?\b")
UNIT_RE = re.compile(r"\s?(?P<u>%|°|deg(?:rees?)?\b|km\b|m2\b|m²|m\b|metres?\b|meters?\b|min(?:utes?)?\b|"
                     r"h\b|hrs?\b|hours?\b|sols?\b|s\b|ha\b|mps\b|m/s\b)", re.I)
SMALL_BARE_MAX = 12  # bare integers at or below this with no unit are counts/ordinals, not measurements
MAX_TRAILING_ZERO_TOL_FROM = 1000


@dataclass(frozen=True)
class Mention:
    raw: str
    value: float
    decimals: int
    unit: str  # "", "%", "deg", "km", "m", "min", "h", "s", "m2", "sol", "ha"
    tol: float  # absolute tolerance in the mention's own unit


_UNIT_CANON = {"°": "deg", "degree": "deg", "degrees": "deg", "deg": "deg", "metre": "m", "metres": "m",
               "meter": "m", "meters": "m", "m²": "m2", "minute": "min", "minutes": "min", "hour": "h",
               "hours": "h", "hr": "h", "hrs": "h", "sol": "sol", "sols": "sol", "m/s": "mps"}


def _canon_unit(u: str) -> str:
    u = u.lower()
    return _UNIT_CANON.get(u, u)


def _tolerance(int_part: str, decimals: int, value: float) -> float:
    """A reply number is 'a rounding of' a source value: allow round-half-up and truncation at the
    reply's own precision; allow sig-fig rounding for large round integers like 2,500."""
    tol = 10.0 ** (-decimals)
    if decimals == 0 and abs(value) >= MAX_TRAILING_ZERO_TOL_FROM:
        digits = int_part.replace(",", "")
        tz = len(digits) - len(digits.rstrip("0"))
        if tz >= 2:
            tol = max(tol, 0.5 * 10 ** tz)
    return tol


def extract_numbers(text: str, *, skip_small_bare: bool = True, skip_identifiers: bool = True) -> list[Mention]:
    text = DATE_RE.sub(" ", text)
    out: list[Mention] = []
    for m in NUM_RE.finditer(text):
        start, end = m.start(), m.end()
        sign = m.group("sign")
        prev = text[start - 1] if start > 0 else ""
        nxt = text[end] if end < len(text) else ""
        if sign in ("-", "−") and prev and (prev.isdigit() or prev.isalpha() or prev in "_)"):
            # "10-20" range or "x-5": the dash is a separator, not a minus
            start += 1
            sign = ""
            prev = text[start - 1]
        if sign == "+":
            sign = ""
        if skip_identifiers:
            if prev and (prev.isalpha() or prev in "_:/" or prev.isdigit()
                         or (prev == "." and start > 1 and text[start - 2].isalnum())):
                continue
            if nxt == "_" or (nxt.isalpha() and not UNIT_RE.match(text[end:])):
                continue
        line_start = text.rfind("\n", 0, start) + 1
        if text[line_start:start].strip() == "" and nxt in ".)" and end + 1 < len(text) and text[end + 1] == " " \
                and not m.group("frac"):
            continue  # list marker "1. foo"
        int_part, frac, exp = m.group("int"), m.group("frac") or "", m.group("exp") or ""
        value = float(int_part.replace(",", "") + frac + exp)
        if sign in ("-", "−"):
            value = -value
        decimals = len(frac) - 1 if frac else 0
        um = UNIT_RE.match(text[end:]) if not exp else None
        unit = _canon_unit(um.group("u")) if um else ""
        if not unit and not frac and not exp and abs(value) <= SMALL_BARE_MAX and skip_small_bare:
            continue
        out.append(Mention(text[start:end] + (um.group(0) if um else ""), value, decimals, unit,
                           _tolerance(int_part, decimals, value)))
    return out


_FACTORS = {"": (1.0,), "%": (1.0, 0.01), "km": (1.0, 1000.0), "m": (1.0,), "h": (1.0, 60.0, 3600.0),
            "min": (1.0, 1 / 60.0, 60.0), "deg": (1.0,), "s": (1.0, 1 / 60.0), "m2": (1.0,), "ha": (1.0, 10000.0)}


def is_grounded(mention: Mention, source_values: Iterable[float]) -> bool:
    for factor in _FACTORS.get(mention.unit, (1.0,)):
        rv = mention.value * factor
        tol = mention.tol * factor * (1.0 if factor == 1.0 else 0.5)  # converted units: round-half only
        for v in source_values:
            if abs(rv - v) <= tol + 1e-9:
                return True
    return False


def source_value_set(texts: Iterable[str]) -> list[float]:
    vals: list[float] = []
    for t in texts:
        vals.extend(m.value for m in extract_numbers(t, skip_small_bare=False, skip_identifiers=False))
    return vals


def ungrounded_numbers(reply: str, sources: Iterable[str]) -> list[str]:
    """Numbers in `reply` that match no number in `sources` (within rounding)."""
    vals = source_value_set(sources)
    bad = []
    for m in extract_numbers(reply):
        if not is_grounded(m, vals) and not is_grounded(m, [-v for v in vals] if m.value < 0 else []):
            bad.append(m.raw.strip())
    return bad


# ---------------------------------------------------------------------------------------------
# Session model
# ---------------------------------------------------------------------------------------------

@dataclass
class Call:
    id: str
    name: str
    args: Any
    result: str | None = None
    is_error: bool = False


@dataclass
class Session:
    prompt: str
    reply: str
    calls: list[Call]
    context_block: str = ""
    stop_reason: str | None = None
    error: str | None = None
    tools_available: list[str] | None = None
    model: str | None = None

    @property
    def tool_names(self) -> list[str]:
        return [c.name for c in self.calls]


def session_from_events(meta: dict, events: list[dict]) -> Session:
    """events: [{"event":..., "data":...}] in order (SSE stream or a loaded replay)."""
    calls: dict[str, Call] = {}
    order: list[Call] = []
    text: list[str] = []
    stop = err = None
    for e in events:
        ev, d = e["event"], e["data"]
        if ev == "text":
            text.append(d.get("delta", ""))
        elif ev == "tool_call_start":
            c = Call(d["id"], d["name"], d.get("args"))
            calls[d["id"]] = c
            order.append(c)
        elif ev == "tool_call_result" and d["id"] in calls:
            calls[d["id"]].result = d.get("result") if d.get("result") is not None else d.get("summary", "")
            calls[d["id"]].is_error = bool(d.get("is_error"))
        elif ev == "done":
            stop = d.get("stop_reason")
        elif ev == "error":
            err = d.get("message")
    return Session(prompt=meta.get("user_prompt", ""), reply="".join(text), calls=order,
                   context_block=meta.get("context_block", ""), stop_reason=stop, error=err,
                   tools_available=meta.get("tools"), model=meta.get("model"))


# ---------------------------------------------------------------------------------------------
# Case scoring
# ---------------------------------------------------------------------------------------------

def _alts(x: Any) -> list[str]:
    return [x] if isinstance(x, str) else list(x)


def score_tool_path(names: list[str], spec: dict, always_allowed: Iterable[str] = ()) -> list[str]:
    """Returns failure strings (empty = pass).
    required: list of groups; each group (name or list of alternatives) must be called at least once.
    order:    list of groups that must occur in this relative order (first occurrence of a later group
              after some occurrence of the earlier one).
    allowed:  if present, every call must be in allowed + always_allowed.
    forbidden: names that must not be called."""
    fails: list[str] = []
    for g in spec.get("required", []):
        if not any(n in names for n in _alts(g)):
            fails.append(f"required tool never called: {' | '.join(_alts(g))}")
    pos = -1
    for g in spec.get("order", []):
        idx = [i for i, n in enumerate(names) if n in _alts(g) and i > pos]
        if not idx:
            if any(n in names for n in _alts(g)):
                fails.append(f"wrong order: {' | '.join(_alts(g))} not after the previous step")
            # absence is reported by `required` if it matters
            continue
        pos = idx[0]
    if "allowed" in spec:
        ok = set(spec["allowed"]) | set(always_allowed)
        extra = sorted({n for n in names if n not in ok})
        if extra:
            fails.append(f"unexpected tools called: {', '.join(extra)}")
    bad = sorted({n for n in names if n in set(spec.get("forbidden", []))})
    if bad:
        fails.append(f"forbidden tools called: {', '.join(bad)}")
    return fails


def check_reply(reply: str, checks: dict) -> list[str]:
    fails: list[str] = []
    low = reply.lower()
    for group in checks.get("reply_contains_all", []):
        if not any(a.lower() in low for a in _alts(group)):
            fails.append(f"reply lacks: {' | '.join(_alts(group))}")
    any_ = checks.get("reply_contains_any")
    if any_ and not any(a.lower() in low for a in any_):
        fails.append(f"reply mentions none of: {', '.join(any_)}")
    for a in checks.get("reply_not_contains", []):
        if a.lower() in low:
            fails.append(f"reply must not contain: {a}")
    if not reply.strip():
        fails.append("empty reply")
    return fails


@dataclass
class Score:
    case_id: str
    passed: bool
    skipped: bool = False
    tool_ok: bool = True
    numbers_ok: bool = True
    checks_ok: bool = True
    failures: list[str] = field(default_factory=list)
    ungrounded: list[str] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)
    n_tool_calls: int = 0


def score_session(case: dict, s: Session, always_allowed: Iterable[str] = (),
                  allow_prompt_numbers: bool = True) -> Score:
    sc = Score(case_id=case["id"], passed=False, tool_names=s.tool_names, n_tool_calls=len(s.calls))
    # Skip (not fail) when the expected path needs tools this server does not offer yet.
    if s.tools_available is not None:
        missing = [g for g in case.get("tool_path", {}).get("required", [])
                   if not any(n in s.tools_available for n in _alts(g))]
        if missing:
            sc.skipped = True
            sc.failures.append("skipped, tools not offered by the MCP server: "
                               + ", ".join(" | ".join(_alts(g)) for g in missing))
            return sc
    if s.error:
        sc.failures.append(f"session error: {s.error}")
    if s.stop_reason is None and not s.error:
        sc.failures.append("session did not finish (no done event)")
    tp = score_tool_path(s.tool_names, case.get("tool_path", {}), always_allowed)
    sc.tool_ok = not tp
    sc.failures += tp
    checks = case.get("checks", {})
    mx = checks.get("max_tool_calls")
    if mx is not None and len(s.calls) > mx:
        sc.tool_ok = False
        sc.failures.append(f"{len(s.calls)} tool calls > max {mx}")
    ck = check_reply(s.reply, checks)
    sc.checks_ok = not ck
    sc.failures += ck
    if checks.get("numbers_grounded", True):
        sources = [c.result or "" for c in s.calls if c.result is not None]
        if allow_prompt_numbers:
            sources += [s.prompt, s.context_block]
        sc.ungrounded = ungrounded_numbers(s.reply, sources)
        sc.numbers_ok = not sc.ungrounded
        if sc.ungrounded:
            sc.failures.append("numbers not found in any tool result: " + ", ".join(sc.ungrounded))
    sc.passed = sc.tool_ok and sc.numbers_ok and sc.checks_ok and not s.error and s.stop_reason is not None
    return sc

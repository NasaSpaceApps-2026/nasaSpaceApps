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
UNIT_RE = re.compile(r"\s?(?P<u>%|°|deg(?:rees?)?\b|km2\b|km²|km\b|m2\b|m²|m\b|metres?\b|meters?\b|min(?:utes?)?\b|"
                     r"h\b|hrs?\b|hours?\b|sols?\b|s\b|ha\b|mps\b|m/s\b)", re.I)
SMALL_BARE_MAX = 12  # bare integers at or below this with no unit are counts/ordinals, not measurements
MAX_TRAILING_ZERO_TOL_FROM = 1000


@dataclass(frozen=True)
class Mention:
    """A number found in text: raw string, value, decimals, unit and the absolute tolerance (in its own unit) implied by its rounding."""
    raw: str
    value: float
    decimals: int
    unit: str  # "", "%", "deg", "km", "m", "min", "h", "s", "m2", "sol", "ha"
    tol: float  # absolute tolerance in the mention's own unit


_UNIT_CANON = {"°": "deg", "degree": "deg", "degrees": "deg", "deg": "deg", "metre": "m", "metres": "m",
               "meter": "m", "meters": "m", "m²": "m2", "km²": "km2", "minute": "min", "minutes": "min", "hour": "h",
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
    """Find numeric mentions with units in `text`, skipping dates and (by default) small bare integers and identifiers like "set:3"."""
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
        if sign == "-" and prev == "+":
            sign = ""  # "+-43 m" is a plus/minus uncertainty, not a negative number
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


_FACTORS = {"": (1.0,), "%": (1.0, 0.01), "km": (1.0, 1000.0), "m": (1.0, 0.001), "km2": (1.0, 1e6), "h": (1.0, 60.0, 3600.0),
            "min": (1.0, 1 / 60.0, 60.0), "deg": (1.0,), "s": (1.0, 1 / 60.0), "m2": (1.0, 1e-6), "ha": (1.0, 10000.0)}


def is_grounded(mention: Mention, source_values: Iterable[float]) -> bool:
    """True if a mention matches some source value within its rounding tolerance, also after unit conversion (m/km, min/h, m2/ha; converted values allow round-half tolerance only)."""
    for factor in _FACTORS.get(mention.unit, (1.0,)):
        rv = mention.value * factor
        tol = mention.tol * factor * (1.0 if factor == 1.0 else 0.5)  # converted units: round-half only
        for v in source_values:
            if abs(rv - v) <= tol + 1e-9:
                return True
    return False


def source_value_set(texts: Iterable[str]) -> list[float]:
    """All numbers appearing in the tool results (and prompt); negatives also count by magnitude ("-4.4 m" supports "4.4 m below")."""
    vals: list[float] = []
    for t in texts:
        vals.extend(m.value for m in extract_numbers(t, skip_small_bare=False, skip_identifiers=False))
    # "B is -4.4 m relative to A" is legitimately restated as "4.4 m below": the magnitude appeared
    return vals + [-v for v in vals if v < 0]


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
    """One tool call in a session: id, tool name, arguments, result text and whether it errored."""
    id: str
    name: str
    args: Any
    result: str | None = None
    is_error: bool = False


@dataclass
class Session:
    """A recorded or live chat session as the scorer sees it: prompt, reply, tool calls, context block, stop reason, error and available tools."""
    prompt: str
    reply: str
    calls: list[Call]
    context_block: str = ""
    stop_reason: str | None = None
    error: str | None = None
    tools_available: list[str] | None = None
    model: str | None = None
    extra_sources: list[str] = field(default_factory=list)  # tool results of earlier turns (multi-turn conversations)

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
    """Evaluate the case's reply checks (phrases that must or must not appear); returns failure messages (empty = pass)."""
    fails: list[str] = []
    low = reply.lower().replace("*", "").replace("`", "")  # markdown emphasis must not split a phrase ("today's** topography")
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


JARGON_RE = re.compile(r"\b(?:set:\d+(?:\.\d+)?|poi:\w+|marker:\w+|route-\d+|slope_2m|rough_5x5|tpi_\w+|local_relief_\w+|"
                       r"strong_orbital|weak_orbital|confirmed_in_situ|inferred_from_unit|not_detected|no_coverage|"
                       r"blocked_slope|blocked_hazard|stopped_by_user|out_of_bounds|crew_buggy|rover_scout|crew_rover|"
                       r"[a-z]+_[a-z0-9]+(?:_[a-z0-9]+)+)\b")


def jargon_in_reply(reply: str) -> list[str]:
    """Raw ids and internal names in a reply (set:3, slope_2m, strong_orbital, poi:..., snake_case). A SOFT check: the
    copilot should say 'the delta front' and 'seen from orbit', not these. Returns the distinct offenders."""
    text = reply.replace("`", "")
    return sorted({m.group(0) for m in JARGON_RE.finditer(text)})


@dataclass
class Score:
    """Result of scoring one case: pass/skip flags for tools, numbers and reply checks, failure messages and the tool path taken."""
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
    warnings: list[str] = field(default_factory=list)   # soft checks (do not affect `passed`)
    reply: str = ""                                     # the reply text (filled by the conversation runner)


def score_session(case: dict, s: Session, always_allowed: Iterable[str] = (),
                  allow_prompt_numbers: bool = True) -> Score:
    """Score a session against a case: tool choice, numbers grounded in tool results, reply checks. Skips (not fails) a case whose required tools the server does not offer."""
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
        sources += s.extra_sources
        sc.ungrounded = ungrounded_numbers(s.reply, sources)
        sc.numbers_ok = not sc.ungrounded
        if sc.ungrounded:
            sc.failures.append("numbers not found in any tool result: " + ", ".join(sc.ungrounded))
    jg = jargon_in_reply(s.reply)
    if jg and not case.get("checks", {}).get("allow_jargon"):
        sc.warnings.append("jargon in reply (raw ids): " + ", ".join(jg))
    sc.passed = sc.tool_ok and sc.numbers_ok and sc.checks_ok and not s.error and s.stop_reason is not None
    return sc



# ---------------------------------------------------------------------------------------------
# Conversation (multi-turn) checks
# ---------------------------------------------------------------------------------------------

FILLER_RE = re.compile(r"^\W*(i['’]ll|i will|let me|sure|great|certainly|of course|okay,? let|one moment|first,? i)\b", re.I)
LIST_ITEM_RE = re.compile(r"^\s*(\d+)\.\s+(\S+)(?:\s+(?:->\s+\S+\s+)?(?:at\s+)?(\(-?[\d.]+, -?[\d.]+\)))?", re.M)


def first_sentence(reply: str) -> str:
    """The reply's first sentence (markdown stripped), split on . ! ? or a newline."""
    t = reply.strip().replace("*", "").replace("`", "")
    m = re.search(r"(?<=[.!?])\s|\n", t)
    return t[: m.start()] if m else t


def prior_item_handles(results: list[str], index: int) -> list[str]:
    """Id token and coordinates of item `index` in the last numbered list printed in `results` (earlier turns)."""
    for text in reversed(results):
        for m in LIST_ITEM_RE.finditer(text):
            if int(m.group(1)) == index:
                return [h for h in (m.group(2).rstrip(":,"), (m.group(3) or "").strip("()")) if h]
    return []


def check_conversation_turn(checks: dict, s: Session, prior: list[Session]) -> list[str]:
    """Checks that need the earlier turns: answer-first, recall of a prior number, right referent in tool arguments.
    answer_first: true            first sentence <= 45 words and no filler opener ("I'll check...")
    max_words: N                  whole reply
    shorter_than_prev: true       reply has fewer words than the previous turn's reply
    recall_from_turn: N           reply has a number and every number is in turn N's tool results (0-based)
    args_contain_any: [..]        some tool call of THIS turn has one of these substrings in its arguments
    args_regex: pattern           same, as a regex
    args_contain_prior: {turn, regex}   args contain what the regex finds in turn's tool results (e.g. a route id)
    args_match_prior_item: {turn, index}  args contain the id or coordinates of item N of the list shown in that turn
    args_not_contain: [..]        no tool call of this turn has these in its arguments"""
    import json
    fails: list[str] = []
    args_all = " ".join(json.dumps(c.args, ensure_ascii=False) for c in s.calls).lower()
    if checks.get("answer_first"):
        fs = first_sentence(s.reply)
        if FILLER_RE.search(fs):
            fails.append(f"not answer-first: opens with filler: {fs[:60]!r}")
        if len(fs.split()) > 45:
            fails.append(f"not answer-first: first sentence is {len(fs.split())} words (max 45)")
    mw = checks.get("max_words")
    if mw and len(s.reply.split()) > mw:
        fails.append(f"reply is {len(s.reply.split())} words > {mw}")
    if checks.get("shorter_than_prev") and prior and len(s.reply.split()) >= len(prior[-1].reply.split()):
        fails.append("reply is not shorter than the previous one")
    if "recall_from_turn" in checks:
        src = [c.result or "" for c in prior[checks["recall_from_turn"]].calls]
        nums = extract_numbers(s.reply)
        if not nums:
            fails.append("recall: reply quotes no number")
        bad = ungrounded_numbers(s.reply, src)
        if bad:
            fails.append("recall: numbers not in that earlier result: " + ", ".join(bad))
    if "args_contain_any" in checks and not any(a.lower() in args_all for a in checks["args_contain_any"]):
        fails.append(f"no tool call mentions any of: {checks['args_contain_any']} (wrong referent or no action)")
    if "args_regex" in checks and not re.search(checks["args_regex"], args_all, re.I):
        fails.append(f"no tool call matches /{checks['args_regex']}/")
    for a in checks.get("args_not_contain", []):
        if a.lower() in args_all:
            fails.append(f"tool call must not mention: {a}")
    spec = checks.get("args_contain_prior")
    if spec:
        found = {m.group(0).lower() for c in prior[spec["turn"]].calls for m in re.finditer(spec["regex"], c.result or "")}
        if not found:
            fails.append("setup problem: nothing to refer back to in the earlier turn")
        elif not any(f in args_all for f in found):
            fails.append(f"tool call does not use the earlier {sorted(found)}")
    spec = checks.get("args_match_prior_item")
    if spec:
        hs = prior_item_handles([c.result or "" for c in prior[spec["turn"]].calls], spec["index"])
        if not hs:
            fails.append("setup problem: earlier turn showed no numbered list")
        elif not any(h.lower() in args_all for h in hs):
            fails.append(f"tool call does not target item {spec['index']} of the earlier list ({hs})")
    return fails

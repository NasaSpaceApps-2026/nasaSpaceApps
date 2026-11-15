"""Server-side conversation memory: the full Anthropic-format transcript (tool_use / tool_result included) per session.

The browser only sends the new user message and a session id; follow-ups ("the second one", "how far was that?")
resolve against what the tools actually returned earlier, not against a text-only copy of the chat.

- ConversationStore: in-memory LRU with a TTL; one asyncio.Lock per conversation so turns never interleave.
- sanitize(): repairs a transcript (drops dangling tool_use / orphan tool_result, merges same-role neighbours),
  so an aborted or failed turn can never poison the next one.
- trim(): keeps the history inside a character budget without breaking tool-pair integrity: old tool results are
  shortened first, then whole old turns are dropped (their questions are kept as a one-line digest).
- track_entities(): remembers the last set / route / place so the context block can hand them back after trimming.
"""
from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

MAX_SESSIONS = 200
TTL_S = 6 * 3600
HISTORY_CHARS = 90_000        # whole-transcript budget (~22k tokens)
OLD_RESULT_CHARS = 700        # tool results older than the last KEEP_FULL_TURNS turns are cut to this
KEEP_FULL_TURNS = 2
MIN_TURNS = 2                 # never drop the last two turns
DIGEST_MAX = 8


def new_session_id() -> str:
    """A fresh opaque session id."""
    return "s" + uuid.uuid4().hex[:16]


def _blocks(m: dict) -> list[dict]:
    c = m.get("content")
    if isinstance(c, str):
        return [{"type": "text", "text": c}]
    return [b for b in c or [] if isinstance(b, dict)]


def is_user_turn_start(m: dict) -> bool:
    """True for a real user message (text), false for a tool_result carrier."""
    if m.get("role") != "user":
        return False
    return not any(b.get("type") == "tool_result" for b in _blocks(m))


def sanitize(msgs: list[dict]) -> list[dict]:
    """Return a valid alternating transcript: every tool_use is answered in the next user message, no orphan tool_result."""
    out: list[dict] = []
    i = 0
    n = len(msgs)
    while i < n:
        m = msgs[i]
        if m.get("role") == "assistant":
            uses = [b for b in _blocks(m) if b.get("type") == "tool_use"]
            if uses:
                nxt = msgs[i + 1] if i + 1 < n else None
                answered = {b.get("tool_use_id") for b in _blocks(nxt) if b.get("type") == "tool_result"} \
                    if nxt and nxt.get("role") == "user" else set()
                if not all(u["id"] in answered for u in uses):
                    # incomplete pair: keep only the text of this assistant message, drop the dangling result carrier
                    keep = [b for b in _blocks(m) if b.get("type") == "text" and b.get("text", "").strip()]
                    if keep:
                        out.append({"role": "assistant", "content": keep})
                    i += 1
                    if nxt and nxt.get("role") == "user" and any(b.get("type") == "tool_result" for b in _blocks(nxt)):
                        i += 1
                    continue
            out.append(m)
        else:
            blocks = _blocks(m)
            if any(b.get("type") == "tool_result" for b in blocks):
                prev = out[-1] if out else None
                ids = {b.get("id") for b in _blocks(prev) if b.get("type") == "tool_use"} if prev and prev["role"] == "assistant" else set()
                blocks = [b for b in blocks if b.get("type") != "tool_result" or b.get("tool_use_id") in ids]
                if not any(b.get("type") == "tool_result" for b in blocks):
                    blocks = [b for b in blocks if b.get("type") == "text" and not b.get("text", "").startswith("[system note]")]
                if not blocks:
                    i += 1
                    continue
                out.append({"role": "user", "content": blocks})
            else:
                out.append(m)
        i += 1
    merged: list[dict] = []
    for m in out:
        if merged and merged[-1]["role"] == m["role"]:
            a, b = merged[-1], m
            if a["role"] == "user" and any(x.get("type") == "tool_result" for x in _blocks(a)) is False \
                    and any(x.get("type") == "tool_result" for x in _blocks(b)) is False:
                merged[-1] = {"role": "user", "content": _text_of(a) + "\n\n" + _text_of(b)}
            else:
                merged[-1] = {"role": a["role"], "content": _blocks(a) + _blocks(b)}
        else:
            merged.append(m)
    while merged and merged[0]["role"] != "user":
        merged.pop(0)
    return merged


def _text_of(m: dict) -> str:
    return "".join(b.get("text", "") for b in _blocks(m) if b.get("type") == "text")


def _size(m: dict) -> int:
    return sum(len(str(b.get("text") or b.get("content") or b.get("input") or "")) for b in _blocks(m))


def _shorten_results(m: dict, limit: int) -> dict:
    if not isinstance(m.get("content"), list):
        return m
    blocks = []
    for b in m["content"]:
        if b.get("type") == "tool_result" and isinstance(b.get("content"), str) and len(b["content"]) > limit:
            b = {**b, "content": b["content"][:limit].rstrip() + "\n[older result shortened]"}
        blocks.append(b)
    return {**m, "content": blocks}


def turns_of(msgs: list[dict]) -> list[list[dict]]:
    """Split a transcript into turns, each starting at a real user message."""
    turns: list[list[dict]] = []
    for m in msgs:
        if is_user_turn_start(m) or not turns:
            turns.append([m])
        else:
            turns[-1].append(m)
    return turns


def trim(msgs: list[dict], budget: int = HISTORY_CHARS) -> tuple[list[dict], list[str]]:
    """(trimmed transcript, questions of the dropped turns). Tool pairs stay intact: only whole turns are dropped."""
    turns = turns_of(msgs)
    cut = max(0, len(turns) - KEEP_FULL_TURNS)
    turns = [[_shorten_results(m, OLD_RESULT_CHARS) for m in t] if i < cut else t for i, t in enumerate(turns)]
    dropped: list[str] = []
    while len(turns) > MIN_TURNS and sum(_size(m) for t in turns for m in t) > budget:
        first = turns.pop(0)
        dropped.append(_text_of(first[0]).strip().replace("\n", " ")[:90])
    return [m for t in turns for m in t], dropped


# ---- entities -------------------------------------------------------------------------------------

SET_RE = re.compile(r"\bset:\d+\b")
ROUTE_RE = re.compile(r"\b(route-\d+|r\d+)\b")
LIST_LINE = re.compile(r"^\s*\d+\.\s+\S")
PLACE_TOOLS = {"teleport_rover", "drive_to", "fly_camera", "describe_location", "drive_rover"}


def _clip(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def track_entities(ent: dict, msgs: list[dict]) -> None:
    """Update `ent` (in place) from tool_use/tool_result pairs in `msgs` (new messages of the latest turn)."""
    calls: dict[str, tuple[str, dict]] = {}
    for m in msgs:
        for b in _blocks(m):
            if b.get("type") == "tool_use":
                calls[b["id"]] = (b["name"], b.get("input") or {})
                acts = ent.setdefault("actions", [])
                acts.append(f"{b['name']}({_clip(str(b.get('input') or {}), 110)})")
                del acts[:-6]
            elif b.get("type") == "tool_result" and not b.get("is_error"):
                name, args = calls.get(b.get("tool_use_id"), ("?", {}))
                text = b.get("content") if isinstance(b.get("content"), str) else ""
                if not text:
                    continue
                sets = SET_RE.findall(text)
                items = [_clip(ln, 150) for ln in text.splitlines() if LIST_LINE.match(ln)][:3]
                if sets and items and name not in ("get_set",):
                    ent["last_set"] = {"id": sets[0], "tool": name, "head": _clip(text.splitlines()[0], 140), "top": items}
                if name == "get_set" and sets:
                    ent["last_set_viewed"] = sets[0]
                if name in ("plan_route", "plan_science_traverse", "assess_route", "follow_route", "drive_to"):
                    mt = ROUTE_RE.search(text)
                    if mt:
                        ent["last_route"] = {"id": mt.group(1), "tool": name, "head": _clip(text.splitlines()[0] if name != "drive_to" else (re.search(r"Route r?\S+:.*", text) or re.search(r".+", text)).group(0), 200)}
                if name in PLACE_TOOLS:
                    tgt = args.get("target") or args.get("at") or args.get("place") or args.get("location")
                    ent["last_place"] = {"tool": name, "asked": _clip(str(tgt), 60) if tgt else None,
                                         "result": _clip(text.splitlines()[0], 160)}


def render_entities(ent: dict, dropped: list[str]) -> str | None:
    """Text for the context block: handles for the last set, route, place and recent actions; None when empty."""
    lines: list[str] = []
    if dropped:
        lines.append("older turns dropped from memory; the user had asked: " + " | ".join(dropped[-DIGEST_MAX:]))
    s = ent.get("last_set")
    if s:
        lines.append(f"last result set: {s['id']} (from {s['tool']}): {s['head']}; top: " + " ; ".join(s["top"]))
    if ent.get("last_set_viewed"):
        lines.append(f"last set opened: {ent['last_set_viewed']}")
    r = ent.get("last_route")
    if r:
        lines.append(f"last route: {r['id']} ({r['tool']}): {r['head']}")
    p = ent.get("last_place")
    if p:
        lines.append(f"last place: {p['tool']} target={p['asked']!r}: {p['result']}")
    if ent.get("actions"):
        lines.append("recent tool calls (oldest first): " + "; ".join(ent["actions"]))
    return "\n".join(lines) or None


# ---- store ----------------------------------------------------------------------------------------

@dataclass
class Conversation:
    """One chat session: transcript, entity handles, digest of dropped turns."""
    sid: str
    messages: list[dict] = field(default_factory=list)
    entities: dict = field(default_factory=dict)
    dropped: list[str] = field(default_factory=list)
    touched: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ConversationStore:
    """In-memory LRU of conversations with a TTL."""

    def __init__(self, max_sessions: int = MAX_SESSIONS, ttl_s: float = TTL_S):
        self.max_sessions, self.ttl_s = max_sessions, ttl_s
        self._d: OrderedDict[str, Conversation] = OrderedDict()

    def __len__(self) -> int:
        return len(self._d)

    def _expire(self) -> None:
        now = time.monotonic()
        for k in [k for k, c in self._d.items() if now - c.touched > self.ttl_s and not c.lock.locked()]:
            del self._d[k]
        while len(self._d) > self.max_sessions:
            for k, c in self._d.items():
                if not c.lock.locked():
                    del self._d[k]
                    break
            else:
                break

    def get(self, sid: str | None) -> tuple[Conversation, bool]:
        """(conversation, existed). Creates (with a generated id when `sid` is falsy or invalid)."""
        self._expire()
        if sid and not re.fullmatch(r"[\w.-]{1,64}", sid):
            sid = None
        sid = sid or new_session_id()
        c = self._d.get(sid)
        existed = c is not None
        if c is None:
            c = self._d[sid] = Conversation(sid)
        self._d.move_to_end(sid)
        c.touched = time.monotonic()
        self._expire()
        return c, existed

    def drop(self, sid: str) -> None:
        """Forget a session."""
        self._d.pop(sid, None)

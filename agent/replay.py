"""Session recording and replay.

File format: JSON lines, one per SSE event, in order:
  {"t": <seconds since session start>, "event": "<sse event name>", "data": {...}}
The first line is {"t": 0, "event": "meta", "data": {...}} (prompt, context block, model, tools); it is
never streamed. Replay streams the recorded events with their relative timing and needs no API key.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Awaitable


class Recorder:
    """Writes one JSON line per SSE event (with relative time) to a new file under `directory`; a no-op when disabled."""
    def __init__(self, directory: Path, meta: dict, enabled: bool = True):
        self.path: Path | None = None
        self._f = None
        self._t0 = time.monotonic()
        if not enabled:
            return
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        path, n = directory / f"{stamp}.jsonl", 1
        while path.exists():
            path = directory / f"{stamp}-{n}.jsonl"
            n += 1
        self.path = path
        self._f = path.open("w", encoding="utf-8")
        self.write("meta", meta)

    def write(self, event: str, data: dict) -> None:
        if self._f is None:
            return
        rec = {"t": round(time.monotonic() - self._t0, 3), "event": event, "data": data}
        self._f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        self._f.flush()

    def close(self) -> None:
        if self._f is not None:
            self._f.close()
            self._f = None


def load_replay(path: str | Path) -> tuple[dict, list[dict]]:
    """Returns (meta, events). Raises ValueError on malformed files."""
    meta: dict = {}
    events: list[dict] = []
    with Path(path).open(encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                ev, data, t = rec["event"], rec.get("data", {}), float(rec.get("t", 0))
            except (ValueError, KeyError, TypeError) as e:
                raise ValueError(f"{path}:{i}: not a valid replay record ({e})") from None
            if ev == "meta":
                meta = data
            else:
                events.append({"t": t, "event": ev, "data": data})
    if not events:
        raise ValueError(f"{path}: no events recorded")
    return meta, events


async def replay_events(path: str | Path, speed: float = 1.0, max_gap_s: float = 3.0,
                        on_tool_call: Callable[[str, dict], Awaitable[Any]] | None = None
                        ) -> AsyncIterator[dict]:
    """Yield recorded events with relative timing. If on_tool_call is given (ATLAS_REPLAY_REPLAY_TOOLS=1)
    it is awaited for each recorded tool_call_start (name, args) so viewer side effects are re-issued;
    its outcome is ignored: the recorded result is what gets streamed."""
    _, events = load_replay(path)
    prev = 0.0
    for ev in events:
        gap = max(0.0, ev["t"] - prev)
        prev = ev["t"]
        if speed > 0 and gap:
            await asyncio.sleep(min(gap, max_gap_s) / speed)
        if ev["event"] == "tool_call_start" and on_tool_call is not None:
            try:
                await on_tool_call(ev["data"].get("name", ""), ev["data"].get("args") or {})
            except Exception:  # noqa: BLE001 - replay must not die on a live side-effect failure
                pass
        yield {"event": ev["event"], "data": ev["data"]}

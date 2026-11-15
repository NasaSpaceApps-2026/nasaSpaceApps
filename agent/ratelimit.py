"""Optional abuse/cost guard for POST /chat, enabled only through the environment (default: off, local behaviour unchanged).

  ATLAS_CHAT_RATE_PER_MIN   max chat requests per client IP per rolling minute (unset or 0 = off)
  ATLAS_CHAT_DAILY_CAP      max chat requests per UTC day across all clients (unset or 0 = off)
  ATLAS_TRUST_XFF           1 = take the client IP from the first X-Forwarded-For entry (set behind a reverse proxy)
"""
from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from datetime import datetime, timezone


def _int(name: str) -> int:
    try:
        return max(0, int(os.environ.get(name, "") or 0))
    except ValueError:
        return 0


class ChatLimiter:
    """Rolling-minute per-IP limit plus a global per-UTC-day cap. check() returns None if allowed, else a sentence for the user."""

    def __init__(self, per_min: int | None = None, daily_cap: int | None = None, trust_xff: bool | None = None, clock=time.time):
        self.per_min = _int("ATLAS_CHAT_RATE_PER_MIN") if per_min is None else per_min
        self.daily_cap = _int("ATLAS_CHAT_DAILY_CAP") if daily_cap is None else daily_cap
        self.trust_xff = (os.environ.get("ATLAS_TRUST_XFF", "").strip().lower() in ("1", "true", "yes", "on")) if trust_xff is None else trust_xff
        self.clock = clock
        self.hits: dict[str, deque] = defaultdict(deque)
        self.day = ""
        self.day_count = 0

    @property
    def enabled(self) -> bool:
        return bool(self.per_min or self.daily_cap)

    def client_ip(self, request) -> str:
        if self.trust_xff:
            xff = request.headers.get("x-forwarded-for", "")
            if xff.strip():
                return xff.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def check(self, ip: str) -> str | None:
        if not self.enabled:
            return None
        now = self.clock()
        if self.daily_cap:
            day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")
            if day != self.day:
                self.day, self.day_count = day, 0
            if self.day_count >= self.daily_cap:
                return "The demo copilot has reached its daily question limit. Please try again tomorrow."
        if self.per_min:
            q = self.hits[ip]
            while q and now - q[0] >= 60:
                q.popleft()
            if len(q) >= self.per_min:
                wait = int(60 - (now - q[0])) + 1
                return f"You are asking too fast. Please wait about {wait} seconds and try again."
            q.append(now)
            if len(self.hits) > 5000:  # bound memory: drop idle clients
                for k in [k for k, v in self.hits.items() if not v or now - v[-1] >= 60]:
                    del self.hits[k]
        if self.daily_cap:
            self.day_count += 1
        return None

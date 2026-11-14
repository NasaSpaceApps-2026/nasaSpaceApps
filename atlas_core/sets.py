"""Location set store (handles). FROZEN INTERFACE.

A member is a dict with at least: id, x, y, lat, lon, score. Optional: name, clear_radius_m,
area_m2, bbox [xmin, ymin, xmax, ymax], attributes {...}. Pair sets (from nearest()) have members
with: id, from_id, to_id, x, y (of the `from` member), cost, cost_metric, straight_m.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from .errors import AtlasError


@dataclass
class LocationSet:
    set_id: str
    kind: str                 # spots | regions | pairs | points
    created_by: str
    query: dict
    members: list[dict] = field(default_factory=list)   # sorted best-first

    @property
    def count(self) -> int:
        return len(self.members)


class SetStore:
    def __init__(self):
        self._sets: dict[str, LocationSet] = {}
        self._counter = itertools.count(1)

    def create(self, kind: str, created_by: str, query: dict, members: list[dict]) -> LocationSet:
        n = next(self._counter)
        set_id = f"set:{n}"
        for i, m in enumerate(members, 1):
            m.setdefault("id", f"{n}.{i}")
        s = LocationSet(set_id=set_id, kind=kind, created_by=created_by, query=query, members=members)
        self._sets[set_id] = s
        return s

    def get(self, set_id: str) -> LocationSet:
        if not set_id.startswith("set:"):
            set_id = "set:" + set_id
        if set_id not in self._sets:
            known = ", ".join(self._sets) or "none yet"
            raise AtlasError(f'Unknown set "{set_id}".', hint=f"Known sets: {known}. Sets last for this session only.")
        return self._sets[set_id]

    def find_member(self, member_id: str) -> dict | None:
        if member_id.startswith("set:"):
            member_id = member_id[4:]
        prefix = member_id.split(".")[0]
        s = self._sets.get(f"set:{prefix}")
        if s is None:
            return None
        return next((m for m in s.members if m.get("id") == member_id), None)

    def page(self, set_id: str, offset: int = 0, limit: int = 10, sort: str | None = None,
             descending: bool = False) -> dict:
        s = self.get(set_id)
        members = s.members
        if sort:
            def key(m):
                v = m.get(sort, m.get("attributes", {}).get(sort))
                return (v is None, v if v is not None else 0)
            members = sorted(members, key=key, reverse=descending)
        return {
            "set_id": s.set_id, "kind": s.kind, "created_by": s.created_by, "count": s.count,
            "offset": offset, "members": members[offset: offset + limit],
        }
